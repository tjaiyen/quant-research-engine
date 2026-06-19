"""Rolling Sharpe analog — efficiency score.

Universal interface (Phase 7):
    sharpe_signal(ticker, price_history, horizon) -> dict

Uses up to 252 trailing days of log-returns to compute annualized Sharpe,
then squashes through a sigmoid to produce a score in (0, 1).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from screener.config import FORECAST_HORIZON_DAYS, MIN_HISTORY_SHARPE

logger = logging.getLogger(__name__)


def sharpe_signal(
    ticker: str,
    price_history: pd.DataFrame,
    horizon: int = FORECAST_HORIZON_DAYS,
) -> dict:
    if len(price_history) < MIN_HISTORY_SHARPE:
        return {
            "score": 0.0,
            "raw": None,
            "metadata": {
                "error": "insufficient_history",
                "rows": len(price_history),
                "required": MIN_HISTORY_SHARPE,
            },
        }
    try:
        close = price_history["Close"].values.astype(float)
        log_ret = np.log(close[1:] / close[:-1])
        window = min(252, len(log_ret))
        recent = log_ret[-window:]

        ann_return = float(recent.mean() * 252.0)
        ann_vol = float(recent.std() * np.sqrt(252.0))
        sharpe = ann_return / max(ann_vol, 1e-6)
        score = float(1.0 / (1.0 + np.exp(-sharpe)))

        return {
            "score": score,
            "raw": sharpe,
            "metadata": {
                "sharpe": sharpe,
                "ann_return": ann_return,
                "ann_vol": ann_vol,
                "window_days": window,
                "full_year_available": bool(window == 252),
            },
        }
    except Exception as exc:
        logger.warning("%s Sharpe failed: %s", ticker, exc)
        return {"score": 0.0, "raw": None, "metadata": {"error": str(exc)}}


__all__ = ["sharpe_signal"]
