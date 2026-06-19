"""HMM regime predictor with hysteresis (M2).

Loads the trained model bundle written by ``hmm_trainer.train_hmm`` and
classifies the most recent market state. To prevent rapid regime flipping
on marginal signals, we accept a regime change only when the same regime
has been the top-probability label for ``REGIME_HYSTERESIS_DAYS`` calls in
a row AND every one of those calls had ``confidence > REGIME_CONFIDENCE_THRESHOLD``.

The hysteresis history is kept at module level — appropriate for the
single-process orchestrator. Tests should call ``reset_hysteresis()``
between runs.
"""
from __future__ import annotations

import logging
from pathlib import Path

import hmmlearn  # type: ignore[import-untyped]
import joblib

from screener.config import (
    HMM_FEATURES,
    MODEL_PATH,
    REGIME_CONFIDENCE_THRESHOLD,
    REGIME_HYSTERESIS_DAYS,
)
from screener.data.feature_store import get_market_features_cached
from screener.regime.weight_matrix import get_blended_weights

logger = logging.getLogger(__name__)

# M2: module-level hysteresis history (single-process use)
_regime_history: list[tuple[str, float]] = []


def reset_hysteresis() -> None:
    """Clear hysteresis state — used by tests."""
    _regime_history.clear()


def get_regime() -> dict:
    """Detect the current market regime from the persisted HMM bundle.

    Returns a dict with the keys:
        - ``regime``: ``"bull" | "sideways" | "bear"`` (hysteresis-stable)
        - ``probabilities``: ``{label: prob}``
        - ``blended_weights``: signal-weight blend (sums to 1.0)
        - ``confidence``: probability of the top regime
        - ``stable``: True if hysteresis confirmed the label
    """
    if not Path(MODEL_PATH).exists():
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH}. Run train_hmm() first."
        )

    bundle = joblib.load(MODEL_PATH)

    # Version check (warn only — model is still usable)
    if bundle.get("hmmlearn_version") != hmmlearn.__version__:
        logger.warning(
            "Model trained on hmmlearn %s, current is %s. Recommend retraining.",
            bundle.get("hmmlearn_version"), hmmlearn.__version__,
        )

    model = bundle["hmm_model"]
    scaler = bundle["scaler"]
    regime_map = bundle["regime_map"]

    # Use a 1-year lookback for prediction — only the last row is consumed.
    # A 100-row floor is enough to clear the rolling 50dMA + 20d-vol burn-in.
    features_df = get_market_features_cached(lookback_years=1, min_rows=100)
    last_row = features_df[HMM_FEATURES].values[-1:, :]
    scaled = scaler.transform(last_row)

    raw_probs = model.predict_proba(scaled)[0]
    probs_by_label: dict[str, float] = {}
    for state in range(len(raw_probs)):
        if state in regime_map:
            label = regime_map[state]
            probs_by_label[label] = probs_by_label.get(label, 0.0) + float(raw_probs[state])

    current_regime = max(probs_by_label, key=lambda k: probs_by_label[k])
    current_confidence = probs_by_label[current_regime]

    # M2: hysteresis — only flip when N days consistent + above confidence floor
    _regime_history.append((current_regime, current_confidence))
    if len(_regime_history) > REGIME_HYSTERESIS_DAYS:
        _regime_history.pop(0)

    stable = False
    stable_regime = current_regime
    if len(_regime_history) >= REGIME_HYSTERESIS_DAYS:
        regimes = [r for r, _ in _regime_history]
        confidences = [c for _, c in _regime_history]
        if (
            len(set(regimes)) == 1
            and min(confidences) > REGIME_CONFIDENCE_THRESHOLD
        ):
            stable_regime = regimes[0]
            stable = True

    blended = get_blended_weights(probs_by_label)

    logger.info(
        "Regime: %s (confidence=%.1f%%, stable=%s)",
        stable_regime.upper(), current_confidence * 100.0, stable,
    )

    return {
        "regime": stable_regime,
        "probabilities": probs_by_label,
        "blended_weights": blended,
        "confidence": float(current_confidence),
        "stable": bool(stable),
    }


__all__ = ["get_regime", "reset_hysteresis"]
