"""Compose a risk-snapshot dict for inclusion in monthly reports + alerts.

The report is just a concise dict — the monthly_report module formats it
into Markdown. M4: include this in every monthly run output.
"""
from __future__ import annotations

import logging

from auto_trader.config import (
    BEAR_REGIME_CONFIDENCE_HALT,
    CASH_RESERVE_PCT,
    DRAWDOWN_HALT_PCT,
    MAX_POSITIONS,
    MAX_SINGLE_STOCK_PCT,
)
from auto_trader.credentials import is_halted
from auto_trader.risk.drawdown_circuit import current_drawdown_pct, is_halted as drawdown_halted
from auto_trader.state.portfolio_db import get_all_positions, get_peak_portfolio_value

logger = logging.getLogger(__name__)


def generate_risk_snapshot(portfolio_value: float, cash: float) -> dict:
    """Return a one-shot snapshot of all risk controls + state."""
    positions = get_all_positions()
    peak = get_peak_portfolio_value()
    dd_pct = current_drawdown_pct(portfolio_value)
    halt = is_halted()
    circuit = drawdown_halted(portfolio_value)

    invested = sum(
        float(p["shares"]) * float(p.get("current_price") or p.get("cost_basis") or 0)
        for p in positions
    )
    cash_pct = cash / portfolio_value if portfolio_value > 0 else 1.0
    largest_pos_pct = 0.0
    largest_ticker = None
    if positions:
        for p in positions:
            value = float(p["shares"]) * float(p.get("current_price") or p.get("cost_basis") or 0)
            pct = value / portfolio_value if portfolio_value > 0 else 0.0
            if pct > largest_pos_pct:
                largest_pos_pct = pct
                largest_ticker = p["ticker"]

    return {
        "portfolio_value": portfolio_value,
        "cash": cash,
        "cash_pct": cash_pct,
        "invested_value": invested,
        "n_positions": len(positions),
        "peak_value": peak,
        "drawdown_pct": dd_pct,
        "halt_flag": halt,
        "circuit_breaker": circuit,
        "largest_position_ticker": largest_ticker,
        "largest_position_pct": largest_pos_pct,
        "limits": {
            "MAX_POSITIONS": MAX_POSITIONS,
            "MAX_SINGLE_STOCK_PCT": MAX_SINGLE_STOCK_PCT,
            "CASH_RESERVE_PCT": CASH_RESERVE_PCT,
            "DRAWDOWN_HALT_PCT": DRAWDOWN_HALT_PCT,
            "BEAR_REGIME_CONFIDENCE_HALT": BEAR_REGIME_CONFIDENCE_HALT,
        },
    }


__all__ = ["generate_risk_snapshot"]
