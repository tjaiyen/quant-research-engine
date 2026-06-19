"""All order placement goes through this module.

H6: ``submit_order`` rejects calls that pass both ``notional`` AND ``shares``.
Callers must specify exactly one. ``time_in_force`` is required from the
caller — there is no default — so the calling layer chooses the right
TIF for the action (sells use 'day', buys use 'opg').

Pre-flight checks:
  * halt-flag (rejects every order while set)
  * trading-day (rejects on weekends/holidays)
  * ``MAX_ORDER_SIZE_USD`` (rejects oversize)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from auto_trader.broker.alpaca_client import get_client, rate_limited
from auto_trader.broker.market_calendar import is_trading_day
from auto_trader.config import (
    ALPACA_RETRY_ATTEMPTS,
    ALPACA_RETRY_MAX_WAIT,
    ALPACA_RETRY_MIN_WAIT,
    FILL_CONFIRM_POLL_INTERVAL,
    FRACTIONAL_SHARES,
    MAX_ORDER_SIZE_USD,
)
from auto_trader.credentials import is_halted
from auto_trader.execution.execution_log import log_execution_event
from auto_trader.state.portfolio_db import log_system_event

logger = logging.getLogger(__name__)


def submit_order(
    ticker: str,
    side: str,
    notional: Optional[float] = None,
    shares: Optional[float] = None,
    time_in_force: Optional[str] = None,
    trigger_reason: str = "UNKNOWN",
    score: Optional[float] = None,  # noqa: ARG001 - logged via system_events upstream
    regime: Optional[str] = None,   # noqa: ARG001 - logged via system_events upstream
) -> Optional[dict]:
    """Submit a single order. Returns a small status dict on success, None on rejection.

    H6: ``notional`` and ``shares`` are mutually exclusive — passing both
    raises ``ValueError``. ``time_in_force`` MUST be passed explicitly.
    """
    # H6: parameter validation
    if notional is not None and shares is not None:
        raise ValueError(
            f"Cannot specify both notional and shares for {ticker}. "
            "Use notional for fractional-share dollar amounts, "
            "shares for whole-share qty sells."
        )
    if notional is None and shares is None:
        raise ValueError(f"Must specify notional or shares for {ticker}")
    if time_in_force is None:
        raise ValueError(
            f"time_in_force must be explicit for {ticker}. "
            "Use SELL_TIME_IN_FORCE or BUY_TIME_IN_FORCE from config."
        )
    if notional is not None and not FRACTIONAL_SHARES:
        raise ValueError(
            f"FRACTIONAL_SHARES=False but notional specified for {ticker}. "
            "Use shares (qty) instead."
        )

    # Pre-flight safety
    if is_halted():
        logger.error("HALT FLAG — order rejected: %s %s", side, ticker)
        return None
    if not is_trading_day():
        logger.warning("Not a trading day — order rejected: %s", ticker)
        return None
    if notional is not None and notional > MAX_ORDER_SIZE_USD:
        logger.warning(
            "%s: $%.2f > MAX $%.2f", ticker, notional, MAX_ORDER_SIZE_USD,
        )
        return None
    if notional is not None and notional < 1.0:
        logger.warning("%s: $%.2f < $1.00 minimum", ticker, notional)
        return None

    try:
        client = get_client()
        kwargs: dict = {
            "symbol": ticker,
            "side": side,
            "type": "market",
            "time_in_force": time_in_force,
        }
        if notional is not None:
            kwargs["notional"] = str(round(notional, 2))
        else:
            kwargs["qty"] = str(round(shares or 0.0, 6))

        order = rate_limited(client.submit_order)(**kwargs)
        log_execution_event(
            "ORDER_SUBMITTED",
            ticker,
            side.upper(),
            float(notional or (shares or 0) * 100),
            {
                "order_id": order.id,
                "tif": time_in_force,
                "trigger": trigger_reason,
            },
        )
        log_system_event(
            "ORDER_SUBMITTED",
            f"{side.upper()} {ticker}",
            {"order_id": order.id, "trigger": trigger_reason},
        )
        logger.info(
            "Order: %s %s %s tif=%s | %s",
            side.upper(), ticker,
            f"${notional}" if notional is not None else f"{shares} sh",
            time_in_force, order.id,
        )
        return {
            "order_id": order.id,
            "ticker": ticker,
            "side": side,
            "notional": notional,
            "shares": shares,
            "status": order.status,
        }

    except Exception as exc:
        logger.error("Order FAILED: %s %s — %s", side, ticker, exc)
        log_system_event(
            "ORDER_FAILED",
            f"{side.upper()} {ticker}",
            {"error": str(exc)},
        )
        return None


@rate_limited
def get_order_status(order_id: str) -> dict:
    order = get_client().get_order(order_id)
    return {
        "order_id": order.id,
        "symbol": getattr(order, "symbol", "UNK"),
        "side": getattr(order, "side", "buy"),
        "status": order.status,
        "filled_qty": float(getattr(order, "filled_qty", 0) or 0),
        "filled_avg": float(getattr(order, "filled_avg_price", 0) or 0),
    }


def cancel_order(order_id: str) -> bool:
    try:
        rate_limited(get_client().cancel_order)(order_id)
        return True
    except Exception as exc:
        logger.error("Cancel failed for %s: %s", order_id, exc)
        return False


def cancel_all_orders() -> int:
    try:
        client = get_client()
        cancel_fn = getattr(client, "cancel_all_orders", None)
        if callable(cancel_fn):
            rate_limited(cancel_fn)()
            return 0
        # Fallback: iterate open orders
        open_orders = client.list_orders(status="open")
        for o in open_orders:
            cancel_order(o.id)
        return len(open_orders)
    except Exception as exc:
        logger.error("cancel_all_orders failed: %s", exc)
        return -1


def confirm_fills(order_ids: list[str], timeout_seconds: int = 300) -> dict:
    """Poll order statuses until all settle or timeout fires.

    Returns categorized buckets:
      * ``full``: filled in full
      * ``partial``: partially filled
      * ``failed``: cancelled / expired / rejected
      * ``timeout``: still pending after deadline
    """
    result: dict[str, list] = {"full": [], "partial": [], "failed": [], "timeout": []}
    pending = list(order_ids)
    start = time.time()
    while pending and (time.time() - start) < timeout_seconds:
        still_pending: list[str] = []
        for oid in pending:
            try:
                s = get_order_status(oid)
            except Exception as exc:
                logger.error("Status check failed %s: %s", oid, exc)
                still_pending.append(oid)
                continue
            status = s["status"]
            if status == "filled":
                result["full"].append(s)
            elif status == "partially_filled":
                result["partial"].append(s)
                logger.warning(
                    "PARTIAL FILL: %s %s shares",
                    s.get("symbol", "?"), s["filled_qty"],
                )
            elif status in ("canceled", "expired", "rejected"):
                result["failed"].append(s)
            else:
                still_pending.append(oid)
        pending = still_pending
        if pending:
            time.sleep(FILL_CONFIRM_POLL_INTERVAL)
    result["timeout"] = pending  # type: ignore[assignment]
    return result


__all__ = [
    "submit_order",
    "get_order_status",
    "cancel_order",
    "cancel_all_orders",
    "confirm_fills",
]
