"""Daily position tracking helpers.

* ``update_all_position_prices`` — refresh ``current_price`` for every
  active position from the broker (or a price dict).
* ``get_position_pnl_summary`` — current unrealized P&L summary across
  active positions.
"""
from __future__ import annotations

import logging

from auto_trader.state.portfolio_db import (
    get_all_positions,
    update_position_price,
)

logger = logging.getLogger(__name__)


def update_all_position_prices(prices: dict[str, float]) -> int:
    """Update current_price for every position in the dict. Returns count updated."""
    n = 0
    for ticker, price in prices.items():
        if price is None:
            continue
        try:
            update_position_price(ticker, float(price))
            n += 1
        except Exception as exc:
            logger.debug("update_position_price(%s) failed: %s", ticker, exc)
    return n


def get_position_pnl_summary() -> dict:
    """Return aggregate unrealized P&L across active positions."""
    positions = get_all_positions()
    total_cost = sum(
        float(p.get("cost_basis", 0)) * float(p.get("shares", 0))
        for p in positions
    )
    total_value = sum(
        float(p.get("current_price") or p.get("cost_basis") or 0)
        * float(p.get("shares", 0))
        for p in positions
    )
    unrealized = total_value - total_cost
    return {
        "n_positions": len(positions),
        "total_cost": total_cost,
        "total_value": total_value,
        "unrealized_pnl": unrealized,
        "unrealized_pct": (unrealized / total_cost) if total_cost > 0 else 0.0,
    }


__all__ = ["update_all_position_prices", "get_position_pnl_summary"]
