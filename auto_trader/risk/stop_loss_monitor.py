"""Scan active positions for triggered stop losses.

Called from ``daily_run.py`` after a price refresh. Returns the list of
hits with the context needed to alert / sell on the next monthly cycle.
"""
from __future__ import annotations

import logging
from typing import Iterable

from auto_trader.state.portfolio_db import get_all_positions

logger = logging.getLogger(__name__)


def scan_stop_losses(prices: dict[str, float]) -> list[dict]:
    """Return list of triggered stop-loss positions.

    Args:
        prices: ``{ticker: latest_price}``. Tickers not in the dict are
            evaluated against the position's stored ``current_price``.

    Returns:
        List of dicts ``{ticker, current_price, stop_price, loss_pct, sector,
        cost_basis}`` for each position whose price is at or below its
        stop-loss line.
    """
    hits: list[dict] = []
    for pos in get_all_positions():
        ticker = pos["ticker"]
        stop = float(pos.get("stop_loss_price") or 0)
        if stop <= 0:
            continue
        current = float(prices.get(ticker, pos.get("current_price") or 0))
        if current <= 0:
            continue
        if current <= stop:
            cost = float(pos.get("cost_basis") or current)
            loss_pct = (current - cost) / cost if cost > 0 else 0.0
            hits.append(
                {
                    "ticker": ticker,
                    "current_price": current,
                    "stop_price": stop,
                    "loss_pct": loss_pct,
                    "sector": pos.get("sector", "UNKNOWN"),
                    "cost_basis": cost,
                }
            )
    if hits:
        logger.warning("Stop-loss scan: %d hits — %s", len(hits), [h["ticker"] for h in hits])
    return hits


__all__ = ["scan_stop_losses"]
