"""Univariate-GBM Monte-Carlo loss-probability signal.

Universal interface (Phase 7):
    monte_carlo_signal(ticker, price_history, horizon) -> dict

Implementation notes:
  - H5: multi-seed runs (``MC_SEED_VARIATIONS``) for uncertainty quantification.
  - Ito-corrected GBM drift: ``drift = mu - 0.5 * sigma**2``.
  - Recent lookback only for drift/vol (``MC_DRIFT_LOOKBACK_DAYS``).
  - Score = ``1 - loss_probability`` where loss = terminal < 0.90 × current.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from screener.config import (
    FORECAST_HORIZON_DAYS,
    MC_DRIFT_LOOKBACK_DAYS,
    MC_LOSS_THRESHOLD,
    MC_SEED,
    MC_SEED_VARIATIONS,
    MC_SIMULATIONS,
    MIN_HISTORY_MC,
)

logger = logging.getLogger(__name__)


def monte_carlo_signal(
    ticker: str,
    price_history: pd.DataFrame,
    horizon: int = FORECAST_HORIZON_DAYS,
) -> dict:
    if len(price_history) < MIN_HISTORY_MC:
        return {
            "score": 0.0,
            "raw": None,
            "metadata": {
                "error": "insufficient_history",
                "loss_probability": 1.0,
                "rows": len(price_history),
                "required": MIN_HISTORY_MC,
            },
        }
    try:
        close = price_history["Close"].values.astype(float)
        log_ret = np.log(close[1:] / close[:-1])
        recent = log_ret[-MC_DRIFT_LOOKBACK_DAYS:]
        mu_daily = float(recent.mean())
        sigma_daily = float(recent.std())
        # Ito correction: drift = mu - 0.5*sigma^2
        drift = mu_daily - 0.5 * sigma_daily ** 2
        current_price = float(close[-1])

        # H5: multi-seed for uncertainty quantification
        loss_probs: list[float] = []
        all_terminals: list[np.ndarray] = []
        for seed_offset in range(MC_SEED_VARIATIONS):
            rng = np.random.default_rng(seed=MC_SEED + seed_offset)
            Z = rng.standard_normal((MC_SIMULATIONS, horizon))
            paths = current_price * np.exp(
                np.cumsum(drift + sigma_daily * Z, axis=1)
            )
            terminal = paths[:, -1]
            all_terminals.append(terminal)
            loss_probs.append(
                float((terminal < current_price * MC_LOSS_THRESHOLD).mean())
            )

        loss_prob = float(np.mean(loss_probs))
        loss_prob_std = float(np.std(loss_probs)) if len(loss_probs) > 1 else 0.0
        combined = np.concatenate(all_terminals)
        score = float(1.0 - loss_prob)

        return {
            "score": score,
            "raw": combined,
            "metadata": {
                "loss_probability": loss_prob,
                "loss_prob_std": loss_prob_std,
                "loss_prob_range": [min(loss_probs), max(loss_probs)],
                "mu_daily": mu_daily,
                "sigma_daily": sigma_daily,
                "drift": drift,
                "loss_threshold_pct": float(MC_LOSS_THRESHOLD),
                "percentiles": {
                    "p5": float(np.percentile(combined, 5)),
                    "p25": float(np.percentile(combined, 25)),
                    "p50": float(np.percentile(combined, 50)),
                    "p75": float(np.percentile(combined, 75)),
                    "p95": float(np.percentile(combined, 95)),
                },
            },
        }
    except Exception as exc:
        logger.warning("%s Monte Carlo failed: %s", ticker, exc)
        return {
            "score": 0.0,
            "raw": None,
            "metadata": {"error": str(exc), "loss_probability": 1.0},
        }


__all__ = ["monte_carlo_signal"]
