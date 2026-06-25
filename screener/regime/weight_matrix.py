"""Probability-weighted regime weight blending.

Prevents cliff-edge rescoring at regime boundaries: instead of using the
weight vector for the single most-likely regime, we blend the three
vectors weighted by the model's posterior probabilities.
"""
from __future__ import annotations

import os

from screener.config import (
    EXPECTED_SIGNAL_KEYS,
    WEIGHT_MATRIX,
    WEIGHT_MATRIX_CANDIDATE,
    WEIGHT_MATRIX_MODE,
)


def _active_matrix() -> dict:
    """The live weight matrix, or the signal-lab candidate when opted in.

    Default "current" keeps live behavior identical; WEIGHT_MATRIX_MODE
    (config or env) = "candidate" swaps in the ARIMA+Sharpe vector for A/B.
    """
    mode = os.getenv("WEIGHT_MATRIX_MODE", WEIGHT_MATRIX_MODE)
    return WEIGHT_MATRIX_CANDIDATE if mode == "candidate" else WEIGHT_MATRIX


def get_blended_weights(regime_probabilities: dict[str, float]) -> dict[str, float]:
    """Compute probability-weighted blend of regime weight vectors.

    Args:
        regime_probabilities: ``{"bull": float, "sideways": float, "bear": float}``.
            Values should sum to 1.0; missing regimes default to 0.

    Returns:
        Mapping ``signal_name → weight`` whose values sum to 1.0 ± 1e-6.

    Raises:
        AssertionError if the blended weights do not sum to 1.0 within
        tolerance — typically signals that the input probabilities did
        not normalize.
    """
    matrix = _active_matrix()
    blended: dict[str, float] = {}
    for signal in EXPECTED_SIGNAL_KEYS:
        blended[signal] = sum(
            float(prob) * matrix[regime][signal]
            for regime, prob in regime_probabilities.items()
            if regime in matrix
        )
    total = sum(blended.values())
    assert abs(total - 1.0) < 1e-6, (
        f"Blended weights sum to {total:.10f}. "
        "Regime probabilities may not sum to 1.0."
    )
    return blended


__all__ = ["get_blended_weights"]
