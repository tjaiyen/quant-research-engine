"""Regime predictive-power evaluator.

For each historical regime classification on SPY, computes the mean
realized SPY 20-day forward log-return. A label is *predictive* if its
mean forward return is meaningfully higher (bull) or lower (bear) than
the unconditional mean.

Usage::

    python -m screener.backtest.regime_accuracy
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from screener.backtest.walk_forward import _train_hmm_on
from screener.config import (
    FORECAST_HORIZON_DAYS,
    HMM_FEATURES,
    HMM_LOOKBACK_YEARS,
    OUTPUT_DIR,
)
from screener.data.market_features import get_market_features

logger = logging.getLogger(__name__)


def evaluate_regime_predictive_power(
    horizon_days: int = FORECAST_HORIZON_DAYS,
    lookback_years: int = HMM_LOOKBACK_YEARS,
) -> dict:
    """Train an HMM on the full feature DF and report per-regime forward returns."""
    features = get_market_features(lookback_years=lookback_years, min_rows=300)
    model, scaler, regime_map = _train_hmm_on(features)
    states = model.predict(scaler.transform(features[HMM_FEATURES].values))

    # Forward log-returns aligned to the index
    log_ret = features["log_return"].copy()
    fwd_log = log_ret.rolling(horizon_days).sum().shift(-horizon_days)

    label_series = pd.Series([regime_map[s] for s in states], index=features.index)
    df = pd.concat([label_series.rename("regime"), fwd_log.rename("fwd")], axis=1).dropna()

    overall_mean = float(df["fwd"].mean())
    rows: list[dict] = []
    for label, sub in df.groupby("regime"):
        mean = float(sub["fwd"].mean())
        std = float(sub["fwd"].std(ddof=1)) if len(sub) > 1 else float("nan")
        # Annualize for human readability (20d → 252/20 ≈ 12.6 multiples)
        annualized = mean * (252.0 / horizon_days)
        rows.append(
            {
                "regime": label,
                "n_observations": int(len(sub)),
                "fraction": float(len(sub) / max(len(df), 1)),
                "mean_forward_logret": mean,
                "std_forward_logret": std,
                "annualized_drift": annualized,
                "lift_vs_unconditional": mean - overall_mean,
            }
        )
    rows.sort(key=lambda r: r["annualized_drift"], reverse=True)

    bull = next((r for r in rows if r["regime"] == "bull"), None)
    bear = next((r for r in rows if r["regime"] == "bear"), None)
    monotone = bool(
        bull is not None
        and bear is not None
        and bull["annualized_drift"] > 0 > bear["annualized_drift"]
    )

    return {
        "horizon_days": horizon_days,
        "n_total": int(len(df)),
        "unconditional_mean_logret": overall_mean,
        "regimes": rows,
        "monotone_bull_gt_bear": monotone,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the predictive power of HMM regime labels."
    )
    parser.add_argument("--horizon", type=int, default=FORECAST_HORIZON_DAYS)
    parser.add_argument(
        "--out",
        type=str,
        default=str(Path(OUTPUT_DIR) / "regime_accuracy.json"),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")

    summary = evaluate_regime_predictive_power(horizon_days=args.horizon)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info("Regime accuracy summary written: %s", args.out)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["evaluate_regime_predictive_power"]
