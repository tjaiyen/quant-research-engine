"""Kalman-filter slope signal.

Universal interface (Phase 7):
    kalman_signal(ticker, price_history, horizon) -> dict

Implementation notes:
  - H3: pykalman import guard. When pykalman is unavailable we fall back to
    a simple α=0.3 EMA — same downstream pipeline (slope-of-recent-filtered
    series → sigmoid score).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from screener.config import (
    FORECAST_HORIZON_DAYS,
    KALMAN_OBSERVATION_COV,
    KALMAN_TRANSITION_COV,
    MIN_HISTORY_KALMAN,
)

logger = logging.getLogger(__name__)

# H3: import guard for pykalman (optional)
try:  # pragma: no cover
    from pykalman import KalmanFilter  # type: ignore[import-untyped]
    HAS_PYKALMAN = True
except Exception:
    HAS_PYKALMAN = False
    KalmanFilter = None  # type: ignore[assignment]


def kalman_signal(
    ticker: str,
    price_history: pd.DataFrame,
    horizon: int = FORECAST_HORIZON_DAYS,
) -> dict:
    if len(price_history) < MIN_HISTORY_KALMAN:
        return {
            "score": 0.0,
            "raw": None,
            "metadata": {
                "error": "insufficient_history",
                "rows": len(price_history),
                "required": MIN_HISTORY_KALMAN,
            },
        }
    try:
        close = price_history["Close"].values.astype(float)

        if HAS_PYKALMAN:
            kf = KalmanFilter(  # type: ignore[misc]
                transition_matrices=[1],
                observation_matrices=[1],
                initial_state_mean=close[0],
                initial_state_covariance=1.0,
                observation_covariance=KALMAN_OBSERVATION_COV,
                transition_covariance=KALMAN_TRANSITION_COV,
            )
            state_means, _ = kf.filter(close)
            method = "pykalman"
        else:
            # H3: full EMA fallback (smoothness ≈ pykalman with low transition cov)
            alpha = 0.3
            ema = np.zeros_like(close)
            ema[0] = close[0]
            for i in range(1, len(close)):
                ema[i] = alpha * close[i] + (1 - alpha) * ema[i - 1]
            state_means = ema.reshape(-1, 1)
            method = "ema_fallback"

        recent = state_means[-20:, 0] if state_means.ndim > 1 else state_means[-20:]
        slope = float(np.polyfit(range(len(recent)), recent, 1)[0])
        norm_slope = slope * 20.0 / max(float(close[-1]), 1e-6)
        score = float(1.0 / (1.0 + np.exp(-norm_slope)))

        return {
            "score": score,
            "raw": state_means,
            "metadata": {
                "slope": slope,
                "norm_slope": norm_slope,
                "method": method,
                "filtered_last": float(recent[-1]),
            },
        }
    except Exception as exc:
        logger.warning("%s Kalman failed: %s", ticker, exc)
        return {"score": 0.0, "raw": None, "metadata": {"error": str(exc)}}


__all__ = ["kalman_signal"]
