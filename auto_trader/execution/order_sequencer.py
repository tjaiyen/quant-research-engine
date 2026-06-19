"""Submit + confirm a list of trade instructions.

Strict ordering (NON-NEGOTIABLE):
  1. Submit ALL sells
  2. Wait for sells to confirm (timeout: ``FILL_CONFIRM_TIMEOUT_SELL``)
  3. Refresh ``get_account_info()`` so cash reflects the fills
  4. Submit ALL buys
  5. Wait for buys to confirm (timeout: ``FILL_CONFIRM_TIMEOUT_BUY``)

M5: partial buy fills are tracked — the position is upserted with the
filled quantity and a ``system_events`` row records the partial.

trade_history is appended only after a fill is confirmed (or partially
confirmed).

This module is the **only** caller of ``broker.order_executor.submit_order``
during the monthly cycle.
"""
from __future__ import annotations

import logging
from typing import Iterable

from auto_trader.allocator.delta_engine import TradeInstruction
from auto_trader.broker.alpaca_client import get_account_info
from auto_trader.broker.order_executor import (
    confirm_fills,
    submit_order,
)
from auto_trader.config import (
    FILL_CONFIRM_TIMEOUT_BUY,
    FILL_CONFIRM_TIMEOUT_SELL,
    STOP_LOSS_PCT,
)
from auto_trader.execution.order_builder import to_submit_kwargs
from auto_trader.state.portfolio_db import (
    close_position,
    get_position,
    log_system_event,
    log_trade,
    upsert_position,
)
from auto_trader.utils import now_iso, today_iso

logger = logging.getLogger(__name__)


def execute_sequence(
    instructions: list[TradeInstruction],
    current_positions: list[dict],  # noqa: ARG001 - reserved for future use
    regime: str,
) -> dict:
    """Execute a list of (already-risk-approved) instructions.

    Returns a summary dict with:
      ``{n_sells_submitted, n_sells_filled, n_buys_submitted,
         n_buys_filled, n_partial, n_failed}``.
    """
    sells = [i for i in instructions if i.action == "SELL"]
    buys = [i for i in instructions if i.action == "BUY"]

    summary = {
        "n_sells_submitted": 0, "n_sells_filled": 0,
        "n_buys_submitted": 0, "n_buys_filled": 0,
        "n_partial": 0, "n_failed": 0,
        "regime": regime,
    }

    # Build maps so we can look up the originating instruction later
    sell_order_map: dict[str, TradeInstruction] = {}
    buy_order_map: dict[str, TradeInstruction] = {}

    # ── Phase 1: submit sells ─────────────────────────────────────────────
    sell_ids: list[str] = []
    for instr in sells:
        kwargs = to_submit_kwargs(instr)
        kwargs["regime"] = regime
        result = submit_order(**kwargs)
        if result is None:
            summary["n_failed"] += 1
            continue
        sell_ids.append(result["order_id"])
        sell_order_map[result["order_id"]] = instr
        summary["n_sells_submitted"] += 1

    # ── Phase 2: wait for sell fills ──────────────────────────────────────
    if sell_ids:
        logger.info("Waiting for %d sell fills…", len(sell_ids))
        sell_fills = confirm_fills(sell_ids, timeout_seconds=FILL_CONFIRM_TIMEOUT_SELL)
        _record_fills(sell_fills, sell_order_map, regime, side="SELL", summary=summary)

    # ── Phase 3: refresh cash ────────────────────────────────────────────
    try:
        acct_after_sells = get_account_info()
        logger.info(
            "Cash after sells: $%.2f (buying_power $%.2f)",
            acct_after_sells["cash"], acct_after_sells["buying_power"],
        )
    except Exception as exc:
        logger.warning("Cash refresh failed: %s", exc)

    # ── Phase 4: submit buys ─────────────────────────────────────────────
    buy_ids: list[str] = []
    for instr in buys:
        kwargs = to_submit_kwargs(instr)
        kwargs["regime"] = regime
        result = submit_order(**kwargs)
        if result is None:
            summary["n_failed"] += 1
            continue
        buy_ids.append(result["order_id"])
        buy_order_map[result["order_id"]] = instr
        summary["n_buys_submitted"] += 1

    # ── Phase 5: wait for buy fills ──────────────────────────────────────
    if buy_ids:
        logger.info("Waiting for %d buy fills…", len(buy_ids))
        buy_fills = confirm_fills(buy_ids, timeout_seconds=FILL_CONFIRM_TIMEOUT_BUY)
        _record_fills(buy_fills, buy_order_map, regime, side="BUY", summary=summary)

    log_system_event(
        "EXECUTE_SEQUENCE_COMPLETE",
        f"Sells {summary['n_sells_filled']}/{summary['n_sells_submitted']}, "
        f"Buys {summary['n_buys_filled']}/{summary['n_buys_submitted']}",
        summary,
    )
    return summary


def _record_fills(
    fills: dict,
    order_map: dict[str, TradeInstruction],
    regime: str,
    side: str,
    summary: dict,
) -> None:
    """Persist each filled / partial / failed order to trade_history + positions."""
    for full in fills["full"]:
        instr = order_map.get(full["order_id"])
        if instr is None:
            continue
        _persist_fill(instr, full, partial=False, regime=regime)
        if side == "SELL":
            summary["n_sells_filled"] += 1
        else:
            summary["n_buys_filled"] += 1

    for partial in fills["partial"]:
        instr = order_map.get(partial["order_id"])
        if instr is None:
            continue
        _persist_fill(instr, partial, partial=True, regime=regime)
        summary["n_partial"] += 1
        log_system_event(
            "PARTIAL_FILL",
            f"{side} {instr.ticker} partially filled "
            f"({partial['filled_qty']} shares @ ${partial['filled_avg']:.2f})",
            partial,
        )
        if side == "SELL":
            summary["n_sells_filled"] += 1
        else:
            summary["n_buys_filled"] += 1

    for failed in fills["failed"]:
        instr = order_map.get(failed["order_id"])
        log_system_event(
            "ORDER_FAILED_AFTER_SUBMIT",
            f"{side} {instr.ticker if instr else '?'} {failed['status']}",
            failed,
        )
        summary["n_failed"] += 1


def _persist_fill(
    instr: TradeInstruction,
    fill: dict,
    partial: bool,
    regime: str,
) -> None:
    """Append to trade_history + upsert positions table."""
    filled_qty = float(fill.get("filled_qty") or 0.0)
    filled_avg = float(fill.get("filled_avg") or 0.0)
    if filled_qty <= 0 or filled_avg <= 0:
        return

    total_value = filled_qty * filled_avg

    # Determine cost_basis for the trade row.
    cost_basis = None
    if instr.action == "SELL":
        existing = get_position(instr.ticker)
        if existing is not None:
            cost_basis = float(existing.get("cost_basis") or filled_avg)
    elif instr.action == "BUY":
        cost_basis = filled_avg

    log_trade(
        {
            "ticker": instr.ticker,
            "action": instr.action,
            "shares": filled_qty,
            "price": filled_avg,
            "total_value": total_value,
            "cost_basis": cost_basis,
            "order_id": fill.get("order_id"),
            "trigger_reason": instr.trigger_reason,
            "composite_score_at": instr.score,
            "regime_at_trade": regime,
            "notes": "PARTIAL" if partial else None,
        }
    )

    # Update positions table
    if instr.action == "BUY":
        existing = get_position(instr.ticker)
        if existing is None:
            stop = filled_avg * (1 - STOP_LOSS_PCT)
            upsert_position(
                {
                    "ticker": instr.ticker,
                    "shares": filled_qty,
                    "cost_basis": filled_avg,
                    "total_cost": total_value,
                    "current_price": filled_avg,
                    "sector": instr.sector,
                    "entry_date": today_iso(),
                    "entry_score": float(instr.score or 0.0),
                    "last_score": float(instr.score or 0.0),
                    "last_scored_at": now_iso(),
                    "stop_loss_price": stop,
                    "target_allocation": float(instr.amount_usd),
                    "status": "ACTIVE",
                    "regime_at_entry": regime,
                }
            )
        else:
            # Top-up — WACC the cost basis
            old_shares = float(existing["shares"])
            old_cost = float(existing["cost_basis"])
            new_shares = old_shares + filled_qty
            new_cost = (
                (old_shares * old_cost + filled_qty * filled_avg) / new_shares
                if new_shares > 0
                else filled_avg
            )
            upsert_position(
                {
                    **existing,
                    "shares": new_shares,
                    "cost_basis": new_cost,
                    "total_cost": new_shares * new_cost,
                    "current_price": filled_avg,
                    "last_score": float(instr.score or existing.get("last_score") or 0.0),
                    "last_scored_at": now_iso(),
                    "stop_loss_price": float(existing.get("stop_loss_price") or new_cost * (1 - STOP_LOSS_PCT)),
                    "status": "ACTIVE",
                }
            )

    elif instr.action == "SELL":
        existing = get_position(instr.ticker)
        if existing is None:
            return
        old_shares = float(existing["shares"])
        remaining = old_shares - filled_qty
        if remaining <= 0.0001:
            close_position(instr.ticker)
        else:
            upsert_position(
                {
                    **existing,
                    "shares": remaining,
                    "total_cost": remaining * float(existing["cost_basis"]),
                    "current_price": filled_avg,
                }
            )


__all__ = ["execute_sequence"]
