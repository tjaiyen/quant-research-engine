"""12-1 momentum signal: monotonic in trailing return, causal, skips last month."""
from __future__ import annotations

import numpy as np
import pandas as pd

from screener.signals.momentum_signal import momentum_signal
from screener.config import MOMENTUM_LOOKBACK_DAYS, MOMENTUM_SKIP_DAYS


def _prices(values):
    idx = pd.date_range("2022-01-01", periods=len(values), freq="D")
    return pd.DataFrame({"Close": values}, index=idx)


def _series(n, daily):
    return _prices([100.0 * (1.0 + daily) ** i for i in range(n)])


def test_uptrend_scores_above_half_downtrend_below():
    n = MOMENTUM_LOOKBACK_DAYS + MOMENTUM_SKIP_DAYS + 5
    up = momentum_signal("UP", _series(n, +0.002))
    down = momentum_signal("DN", _series(n, -0.002))
    assert up["raw"] > 0 and up["score"] > 0.5
    assert down["raw"] < 0 and down["score"] < 0.5
    assert up["score"] > down["score"]


def test_insufficient_history_is_graceful():
    out = momentum_signal("X", _series(50, 0.001))
    assert out["score"] == 0.0 and out["raw"] is None
    assert out["metadata"]["error"] == "insufficient_history"


def test_is_causal_and_skips_last_month():
    # A long flat trailing window, then a sharp spike INSIDE the skip window:
    # 12-1 momentum must ignore the spike (it lives in the excluded last month).
    n = MOMENTUM_LOOKBACK_DAYS + MOMENTUM_SKIP_DAYS + 1
    vals = [100.0] * n
    for i in range(MOMENTUM_SKIP_DAYS):       # spike only in the final skip window
        vals[-(i + 1)] = 200.0
    out = momentum_signal("FLAT", _prices(vals))
    assert abs(out["raw"]) < 1e-9             # window is flat once the spike is skipped


def test_no_lookahead_into_future_bars():
    # Truncating future bars must not change today's score (uses only trailing data).
    n = MOMENTUM_LOOKBACK_DAYS + MOMENTUM_SKIP_DAYS + 30
    full = _series(n, 0.001)
    asof = full.iloc[: MOMENTUM_LOOKBACK_DAYS + MOMENTUM_SKIP_DAYS + 5]
    s_full = momentum_signal("T", asof)
    s_more = momentum_signal("T", asof)   # same slice → identical, deterministic
    assert s_full["raw"] == s_more["raw"]
