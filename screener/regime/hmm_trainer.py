"""Multi-feature HMM trainer with atomic persistence.

Implements the spec's Phase 6 trainer:
  - C4: explicit column ordering via ``features_df[HMM_FEATURES]``
  - C5: cross-platform atomic write via ``shutil.move`` (with .backup on Win)
  - Multi-feature composite labels (return rank + vol rank)
  - Convergence gate (raises on non-convergence)
  - Regime-separation gate (raises if bull/bear too close)
  - 28-day retrain cadence via ``should_retrain``

The cockpit's existing ``quant_models._hmm_regimes`` is single-feature
(log-returns only) and not persisted; this trainer operates on the full
4-feature HMM and saves a model bundle to disk.
"""
from __future__ import annotations

import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

import hmmlearn  # type: ignore[import-untyped]
import joblib
import pandas as pd
import sklearn
from hmmlearn.hmm import GaussianHMM
from sklearn.preprocessing import StandardScaler

from screener.config import (
    HMM_CONVERGENCE_THRESHOLD,
    HMM_FEATURES,
    HMM_LABEL_VALIDATION,
    HMM_LOOKBACK_YEARS,
    HMM_MIN_REGIME_SEPARATION,
    HMM_N_ITER,
    HMM_N_STATES,
    HMM_RETRAIN_CADENCE_DAYS,
    MODEL_PATH,
)
from screener.data.feature_store import get_market_features_cached

logger = logging.getLogger(__name__)


def atomic_write_model(bundle: dict, model_path: str) -> None:
    """C5: cross-platform atomic model write.

    Writes to a ``.tmp`` sibling, then atomically moves it into place via
    ``shutil.move`` (cross-device safe, unlike ``os.replace``). On Windows
    we also copy the existing model to ``.backup`` before the move.
    Cleans up the ``.tmp`` on any failure.
    """
    target = Path(model_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(".tmp")
    try:
        joblib.dump(bundle, tmp_path)
        if sys.platform == "win32" and target.exists():
            backup_path = target.with_suffix(".backup")
            shutil.copy2(target, backup_path)
            logger.debug("Backup written: %s", backup_path)
        shutil.move(str(tmp_path), str(target))
        logger.info("Model saved atomically: %s", target)
    except Exception as exc:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise RuntimeError(f"Model save failed: {exc}") from exc


def train_hmm(lookback_years: int = HMM_LOOKBACK_YEARS) -> dict:
    """Train a 4-feature GaussianHMM and persist the bundle.

    Returns a training-summary dict. Raises ``RuntimeError`` if the model
    fails to converge or if the bull/bear separation is too small to trust
    the labels.
    """
    logger.info("Training HMM on %d-year window…", lookback_years)
    features_df = get_market_features_cached(lookback_years)

    # C4: explicit column ordering (never rely on DataFrame column order)
    features_array = features_df[HMM_FEATURES].values
    scaler = StandardScaler()
    scaled = scaler.fit_transform(features_array)

    model = GaussianHMM(
        n_components=HMM_N_STATES,
        covariance_type="full",
        n_iter=HMM_N_ITER,
        tol=HMM_CONVERGENCE_THRESHOLD,
    )
    model.fit(scaled)

    # Convergence gate
    if not getattr(model.monitor_, "converged", False):
        last_delta = (
            model.monitor_.history[-1] if model.monitor_.history else float("nan")
        )
        raise RuntimeError(
            f"HMM failed to converge after {HMM_N_ITER} iterations "
            f"(final ll-delta={last_delta:.6f}). "
            "Try increasing HMM_N_ITER or reviewing feature scaling."
        )

    # Multi-feature composite label assignment (C4 / v2 fix)
    predicted = model.predict(scaled)
    rows = []
    for s in range(HMM_N_STATES):
        mask = predicted == s
        rows.append(
            {
                "state": s,
                "mean_return": float(features_df.loc[mask, "log_return"].mean()),
                "mean_vol": float(features_df.loc[mask, "realized_vol_20d"].mean()),
                "pct_obs": float(mask.mean()),
            }
        )
    df_stats = pd.DataFrame(rows)
    df_stats["return_rank"] = df_stats["mean_return"].rank(ascending=False)
    df_stats["vol_rank"] = df_stats["mean_vol"].rank(ascending=True)
    df_stats["composite"] = df_stats["return_rank"] + df_stats["vol_rank"]
    df_stats = df_stats.sort_values("composite").reset_index(drop=True)

    regime_map = {
        int(df_stats.iloc[0]["state"]): "bull",
        int(df_stats.iloc[1]["state"]): "sideways",
        int(df_stats.iloc[2]["state"]): "bear",
    }

    # Label validation gate. The mean returns above are *daily* log returns,
    # so we annualize (×252) before comparing to the threshold — which the
    # spec defines on annualized scale (0.10 = 10pp/yr spread between bull
    # and bear). Without this conversion, even a strong daily separation of
    # 30 bps fails the 10pp gate.
    separation: float = float("nan")
    if HMM_LABEL_VALIDATION:
        bull_state = next(s for s, lbl in regime_map.items() if lbl == "bull")
        bear_state = next(s for s, lbl in regime_map.items() if lbl == "bear")
        bull_mean = float(df_stats[df_stats["state"] == bull_state]["mean_return"].iloc[0])
        bear_mean = float(df_stats[df_stats["state"] == bear_state]["mean_return"].iloc[0])
        separation = (bull_mean - bear_mean) * 252.0
        if separation < HMM_MIN_REGIME_SEPARATION:
            raise RuntimeError(
                f"HMM state separation insufficient: {separation:.4f}/yr < "
                f"{HMM_MIN_REGIME_SEPARATION}/yr. Regime labels unreliable. "
                "Check feature quality."
            )

    # Build and persist bundle
    bundle = {
        "hmm_model": model,
        "scaler": scaler,
        "regime_map": regime_map,
        "feature_names": HMM_FEATURES,
        "trained_on": datetime.now().isoformat(),
        "hmmlearn_version": hmmlearn.__version__,
        "sklearn_version": sklearn.__version__,
        "training_rows": int(len(features_array)),
        "date_range": [
            str(features_df.index[0].date()),
            str(features_df.index[-1].date()),
        ],
        "state_stats": df_stats.to_dict("records"),
    }
    atomic_write_model(bundle, MODEL_PATH)

    summary = {
        "regime_distribution": {
            label: f"{df_stats[df_stats.state == state]['pct_obs'].iloc[0]:.1%}"
            for state, label in regime_map.items()
        },
        "date_range": bundle["date_range"],
        "converged": True,
        "separation": float(separation),
    }
    logger.info("HMM training complete: %s", summary)
    return summary


def should_retrain() -> bool:
    """Return True if the saved model is missing or older than cadence."""
    p = Path(MODEL_PATH)
    if not p.exists():
        return True
    age_days = (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).days
    return age_days >= HMM_RETRAIN_CADENCE_DAYS


__all__ = ["atomic_write_model", "train_hmm", "should_retrain"]
