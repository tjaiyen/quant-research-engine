"""Alpaca singleton broker client.

C3: ``rate_limited`` decorator uses ``functools.wraps`` so wrapped
functions keep their metadata (introspection, doctest collection, etc.).

Smart-reuse: when ``ALPACA_USE_MOCK=true`` (default for the build),
``get_client()`` returns ``MockAlpacaClient`` instead of the real REST
client. The auto_trader passes all 18 gates against either backend.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, TypeVar

from auto_trader.config import (
    ALPACA_API_CALLS_PER_MIN,
    ALPACA_RETRY_ATTEMPTS,
    ALPACA_RETRY_MAX_WAIT,
    ALPACA_RETRY_MIN_WAIT,
)
from auto_trader.credentials import get_alpaca_credentials, use_mock_broker

logger = logging.getLogger(__name__)

_client: Any = None  # singleton; set lazily on first use
T = TypeVar("T", bound=Callable[..., Any])


# ── C3: rate_limited decorator ─────────────────────────────────────────────
def rate_limited(fn: T) -> T:
    """Wrap ``fn`` with a 150/min rate limiter (config-driven).

    Falls back to a passthrough if ``ratelimit`` isn't installed (so the
    auto_trader can still be imported in environments without it).
    """
    try:
        from ratelimit import limits, sleep_and_retry

        @sleep_and_retry
        @limits(calls=ALPACA_API_CALLS_PER_MIN, period=60)
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]
    except Exception:  # pragma: no cover - dep missing
        logger.warning("ratelimit not installed — passthrough (no rate limit)")

        @wraps(fn)
        def passthrough(*args, **kwargs):
            return fn(*args, **kwargs)

        return passthrough  # type: ignore[return-value]


# ── Singleton getter ───────────────────────────────────────────────────────
def get_client() -> Any:
    """Lazy singleton. Honors ``ALPACA_USE_MOCK`` for the mock path."""
    global _client
    if _client is not None:
        return _client

    creds = get_alpaca_credentials()

    if use_mock_broker() or creds.get("mock"):
        import os
        from pathlib import Path

        from mock_broker import MockAlpacaClient

        # File-backed so the paper account survives across the loop's separate
        # processes (cycle/monitor/report). Off-Drive under store/; overridable.
        state_path = os.getenv(
            "MOCK_BROKER_STATE",
            str(Path(__file__).resolve().parents[2] / "store" / "mock_broker.json"),
        )
        _client = MockAlpacaClient(state_path=state_path)
        logger.info("Alpaca client: MOCK (file-backed paper account: %s)", state_path)
        # Smoke-check the surface
        acct = _client.get_account()
        logger.info(
            "  Status=%s | Cash=$%s",
            acct.status, acct.cash,
        )
        return _client

    # Real client
    try:
        import alpaca_trade_api as tradeapi  # type: ignore[import-untyped]
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "alpaca-trade-api is not installed. "
            "Install via `pip install -r auto_trader/requirements_trader.txt`, "
            "or set ALPACA_USE_MOCK=true to use the mock broker."
        ) from exc

    _client = tradeapi.REST(
        key_id=creds["api_key"],
        secret_key=creds["secret_key"],
        base_url=creds["base_url"],
        api_version="v2",
    )
    acct = _client.get_account()
    logger.info(
        "Alpaca: %s | Status=%s | Cash=$%.2f",
        creds["base_url"], acct.status, float(acct.cash),
    )
    return _client


def reset_client() -> None:
    """Drop the singleton (used by tests when env changes)."""
    global _client
    _client = None


# ── Account info / positions ───────────────────────────────────────────────
def _retry(fn: T) -> T:
    """Wrap ``fn`` with exponential-backoff retry. tenacity-optional."""
    try:
        from tenacity import retry, stop_after_attempt, wait_exponential

        return retry(
            stop=stop_after_attempt(ALPACA_RETRY_ATTEMPTS),
            wait=wait_exponential(min=ALPACA_RETRY_MIN_WAIT, max=ALPACA_RETRY_MAX_WAIT),
            reraise=True,
        )(fn)  # type: ignore[return-value]
    except Exception:  # pragma: no cover - dep missing
        return fn


@rate_limited
@_retry
def get_account_info() -> dict:
    acct = get_client().get_account()
    return {
        "cash": float(acct.cash),
        "portfolio_value": float(acct.portfolio_value),
        "buying_power": float(acct.buying_power),
        "status": acct.status,
        "pattern_day_trader": getattr(acct, "pattern_day_trader", False),
        "trading_blocked": getattr(acct, "trading_blocked", False),
    }


@rate_limited
@_retry
def get_current_positions() -> list[dict]:
    positions = get_client().list_positions()
    out: list[dict] = []
    for p in positions:
        cost = float(p.avg_entry_price)
        qty = float(p.qty)
        out.append(
            {
                "ticker": p.symbol,
                "shares": qty,
                "current_price": float(p.current_price),
                "cost_basis": cost,
                "market_value": float(p.market_value),
                "unrealized_pnl": float(getattr(p, "unrealized_pl", 0) or 0),
                "unrealized_pnl_pct": float(getattr(p, "unrealized_plpc", 0) or 0),
            }
        )
    return out


__all__ = [
    "get_client",
    "reset_client",
    "rate_limited",
    "get_account_info",
    "get_current_positions",
]
