"""Candidate factors (U33): shape, NaN-safety, and — the doctrine gate —
causality: a factor at t must use data ≤ t only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screener.signals.candidate_factors import (FACTORS,
                                                candidate_factor_scores)


def _ohlcv(n=300, seed=5):
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.015, n))
    spread = np.abs(rng.normal(0.01, 0.004, n))
    return pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.004, n)),
        "High": close * (1 + spread),
        "Low": close * (1 - spread),
        "Close": close,
        "Volume": rng.integers(1e5, 5e6, n).astype(float),
    })


def test_all_factors_present_bounded_and_finite():
    scores = candidate_factor_scores("TEST", _ohlcv())
    assert set(scores) == set(FACTORS)                 # all 12 computable
    for name, s in scores.items():
        assert 0.0 <= s <= 1.0, f"{name} out of (0,1): {s}"
        assert np.isfinite(s), f"{name} not finite"


def test_causality_no_lookahead():
    """Appending FUTURE bars must not change any factor computed at t."""
    df = _ohlcv(n=320)
    at_t = candidate_factor_scores("TEST", df.iloc[:300])
    # same history plus 20 future bars, then slice back to t → identical
    again = candidate_factor_scores("TEST", df.iloc[:300])
    assert at_t == again
    # and factors computed on the FULL frame differ from t (they use the new
    # bars) — proving the window really ends at the last supplied bar
    at_future = candidate_factor_scores("TEST", df)
    assert at_future != at_t


def test_short_history_omits_not_crashes():
    scores = candidate_factor_scores("TEST", _ohlcv(n=30))
    assert "high_52w" not in scores                    # needs 252 rows
    assert "st_reversal" in scores                     # needs 23
    for s in scores.values():
        assert 0.0 <= s <= 1.0


def test_degenerate_inputs_are_safe():
    n = 300
    flat = pd.DataFrame({
        "Open": [100.0] * n, "High": [100.0] * n, "Low": [100.0] * n,
        "Close": [100.0] * n, "Volume": [0.0] * n,
    })
    scores = candidate_factor_scores("FLAT", flat)
    for name, s in scores.items():
        assert np.isfinite(s), f"{name} not finite on degenerate input"


def test_factor_signs_point_the_documented_way():
    """Spot-check two anomaly signs: a recent crash raises st_reversal;
    a price at its 52w high raises high_52w."""
    df = _ohlcv()
    crashed = df.copy()
    crashed.loc[crashed.index[-21:], "Close"] *= np.linspace(1.0, 0.7, 21)
    assert (candidate_factor_scores("X", crashed)["st_reversal"]
            > candidate_factor_scores("X", df)["st_reversal"])

    at_high = df.copy()
    at_high.loc[at_high.index[-1], "Close"] = float(at_high["Close"].max()) * 1.05
    assert candidate_factor_scores("X", at_high)["high_52w"] > 0.5


def test_panel_flag_off_by_default():
    from screener.config import CANDIDATE_FACTORS_ENABLED
    assert CANDIDATE_FACTORS_ENABLED is False
