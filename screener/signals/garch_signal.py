"""GARCH(p, q) volatility-forecast signal.

Universal interface (Phase 7):
    garch_signal(ticker, price_history, horizon) -> dict

The score itself is just the volatility-efficiency component:
    ``score = 1 - min(annualized_vol / GARCH_VOL_SCORE_CAP, 1.0)``.
The downstream veto gate uses the *raw* annualized vol from metadata.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from screener.config import (
    FORECAST_HORIZON_DAYS,
    GARCH_P,
    GARCH_Q,
    GARCH_VOL_SCORE_CAP,
    MIN_HISTORY_GARCH,
)

logger = logging.getLogger(__name__)


def garch_signal(
    ticker: str,
    price_history: pd.DataFrame,
    horizon: int = FORECAST_HORIZON_DAYS,
) -> dict:
    if len(price_history) < MIN_HISTORY_GARCH:
        return {
            "score": 0.0,
            "raw": None,
            "metadata": {
                "error": "insufficient_history",
                "daily_vol": 999.0,
                "annualized_vol": 999.0,
                "rows": len(price_history),
                "required": MIN_HISTORY_GARCH,
            },
        }
    try:
        from arch import arch_model

        # arch convention: scale returns by 100 for numerical stability
        log_ret = (
            np.log(price_history["Close"] / price_history["Close"].shift(1))
            .dropna() * 100.0
        )

        model = arch_model(log_ret, vol="Garch", p=GARCH_P, q=GARCH_Q)
        res = model.fit(disp="off", show_warning=False)

        conv_flag = int(getattr(res, "convergence_flag", 0))
        conv_warn = conv_flag != 0
        if conv_warn:
            logger.debug("%s GARCH convergence flag=%d", ticker, conv_flag)

        forecast = res.forecast(horizon=horizon)
        # Undo the *100 scaling: variance was on (returns*100), so divide by 10_000
        daily_var = float(forecast.variance.values[-1, -1]) / 10_000.0
        daily_vol = float(np.sqrt(daily_var))
        ann_vol = float(np.sqrt(daily_var * 252.0))
        # Score uses annualized vol — `GARCH_VOL_SCORE_CAP=0.60` is a 60%
        # annualized cap. Veto downstream uses `daily_vol` (its thresholds
        # are stated on the daily-vol scale).
        score = float(1.0 - min(ann_vol / GARCH_VOL_SCORE_CAP, 1.0))

        return {
            "score": score,
            "raw": forecast,
            "metadata": {
                "daily_vol": daily_vol,
                "annualized_vol": ann_vol,
                "daily_var": daily_var,
                "convergence_flag": conv_flag,
                "convergence_warning": conv_warn,
            },
        }
    except Exception as exc:
        logger.warning("%s GARCH failed: %s", ticker, exc)
        return {
            "score": 0.0,
            "raw": None,
            "metadata": {"error": str(exc), "daily_vol": 999.0, "annualized_vol": 999.0},
        }


__all__ = ["garch_signal"]
