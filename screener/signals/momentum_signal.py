"""12-1 cross-sectional momentum — the textbook strongest equity anomaly.

Universal interface (Phase 7):
    momentum_signal(ticker, price_history, horizon) -> dict

12-1 momentum = the total return over the trailing ~12 months EXCLUDING the most
recent ~1 month. The one-month skip is deliberate: short-horizon returns exhibit
*reversal* (last month's winners tend to give a bit back), so including it dilutes
the momentum effect (Jegadeesh & Titman 1993; Asness et al. 2013). Price-only and
fully causal — uses only `price_history` up to the as-of bar, so it slots into the
backtest panel with no look-ahead (unlike fundamentals-based quality/value, which
yfinance only serves as a current snapshot).

The raw 12-1 return is squashed through a sigmoid into a (0,1) score so it shares
the same scale as the other five signals. Cross-sectional rank is what the
signal-lab IC test actually uses, so the squash is monotonic-only by design.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from screener.config import (FORECAST_HORIZON_DAYS, MOMENTUM_LOOKBACK_DAYS,
                             MOMENTUM_SKIP_DAYS)

logger = logging.getLogger(__name__)


def momentum_signal(
    ticker: str,
    price_history: pd.DataFrame,
    horizon: int = FORECAST_HORIZON_DAYS,
) -> dict:
    need = MOMENTUM_LOOKBACK_DAYS + MOMENTUM_SKIP_DAYS
    if len(price_history) < need:
        return {
            "score": 0.0,
            "raw": None,
            "metadata": {
                "error": "insufficient_history",
                "rows": len(price_history),
                "required": need,
            },
        }
    try:
        close = price_history["Close"].values.astype(float)
        # End the window MOMENTUM_SKIP_DAYS before the as-of bar (skip last month);
        # start MOMENTUM_LOOKBACK_DAYS before that.
        end = close[-(MOMENTUM_SKIP_DAYS + 1)]
        start = close[-(MOMENTUM_SKIP_DAYS + MOMENTUM_LOOKBACK_DAYS)]
        if start <= 0:
            return {"score": 0.0, "raw": None,
                    "metadata": {"error": "nonpositive_start_price"}}
        mom = end / start - 1.0
        # Sigmoid on the raw return; scale so a ±50% 12-1 return spans most of (0,1).
        score = float(1.0 / (1.0 + np.exp(-mom / 0.25)))
        return {
            "score": score,
            "raw": float(mom),
            "metadata": {
                "mom_12_1": float(mom),
                "lookback_days": MOMENTUM_LOOKBACK_DAYS,
                "skip_days": MOMENTUM_SKIP_DAYS,
            },
        }
    except Exception as exc:
        logger.warning("%s momentum failed: %s", ticker, exc)
        return {"score": 0.0, "raw": None, "metadata": {"error": str(exc)}}


__all__ = ["momentum_signal"]
