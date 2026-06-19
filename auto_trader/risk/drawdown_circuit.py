"""Drawdown circuit breaker.

Trips when ``portfolio_value`` falls more than ``DRAWDOWN_HALT_PCT`` (15%)
below the all-time peak in ``portfolio_snapshots``. While tripped, all
new BUY instructions are blocked (Guard 1 in the exposure_guard pipeline).
"""
from __future__ import annotations

import logging

from auto_trader.config import DRAWDOWN_HALT_PCT
from auto_trader.state.portfolio_db import get_peak_portfolio_value

logger = logging.getLogger(__name__)


def is_halted(portfolio_value: float) -> bool:
    """Return True if drawdown from peak ≥ ``DRAWDOWN_HALT_PCT``."""
    peak = get_peak_portfolio_value()
    if peak <= 0:
        return False
    drawdown = (peak - portfolio_value) / peak
    if drawdown >= DRAWDOWN_HALT_PCT:
        logger.warning(
            "DRAWDOWN CIRCUIT TRIPPED: peak=$%.2f current=$%.2f dd=%.1f%%",
            peak, portfolio_value, drawdown * 100,
        )
        return True
    return False


def current_drawdown_pct(portfolio_value: float) -> float:
    """Return the drawdown as a fraction in [0, 1]. 0 if no history."""
    peak = get_peak_portfolio_value()
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - portfolio_value) / peak)


__all__ = ["is_halted", "current_drawdown_pct"]
