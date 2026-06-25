"""Phase J — screener invariants + all 11 gates from SCREENER_BUILD_v3 §13.

Run::

    pytest tests/test_screener.py -v

The cockpit's existing 17 tests live in ``tests/test_math_invariants.py`` and
must continue to pass. These tests are additive.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

# Bootstrap the cockpit DB once per session — many gates touch SQLite
from utils.db import init_db


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_db():
    init_db()


@pytest.fixture(scope="module")
def aapl_history() -> pd.DataFrame:
    """Provide AAPL OHLCV (Title-cased) from cockpit's prices table for all tests."""
    from utils.db import fetch_prices

    df = fetch_prices("AAPL")
    if df.empty:
        pytest.skip("AAPL not in cockpit prices table — run tasks.refresh_prices first")
    return pd.DataFrame(
        {
            "Open": df["open"],
            "High": df["high"],
            "Low": df["low"],
            "Close": df["adj_close"],
            "Volume": df["volume"],
        },
        index=df.index,
    ).dropna(how="all")


# ---------------------------------------------------------------------------
# GATE 1 — Import check
# ---------------------------------------------------------------------------
def test_gate1_imports_run_screener():
    from screener.screener_main import run_screener  # noqa: F401


# ---------------------------------------------------------------------------
# GATE 2 — Config integrity
# ---------------------------------------------------------------------------
def test_gate2_config_integrity():
    from screener.config import (
        EXPECTED_SIGNAL_KEYS,
        VETO_THRESHOLDS,
        WEIGHT_MATRIX,
    )

    for regime, weights in WEIGHT_MATRIX.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-9, f"{regime} sums to {total}"
        assert set(weights.keys()) == EXPECTED_SIGNAL_KEYS

    # Each regime must have a veto-threshold row
    for regime in WEIGHT_MATRIX:
        assert regime in VETO_THRESHOLDS, f"VETO_THRESHOLDS missing {regime}"
        assert "garch_vol" in VETO_THRESHOLDS[regime]
        assert "mc_loss_prob" in VETO_THRESHOLDS[regime]


# ---------------------------------------------------------------------------
# GATE 3 — Market features pull (column order)
# ---------------------------------------------------------------------------
def test_gate3_market_features_column_order():
    from screener.config import HMM_FEATURES
    from screener.data.feature_store import clear_cache
    from screener.data.market_features import get_market_features

    clear_cache()
    df = get_market_features(lookback_years=1, min_rows=100)
    assert len(df) > 200
    assert list(df.columns) == HMM_FEATURES, f"Column order wrong: {list(df.columns)}"
    assert df.isna().sum().sum() == 0


# ---------------------------------------------------------------------------
# GATE 4 — Signal interface contract (all 5)
# ---------------------------------------------------------------------------
def test_gate4_signal_interface_contract(aapl_history: pd.DataFrame):
    from screener.config import FORECAST_HORIZON_DAYS
    from screener.signals.arima_signal import arima_signal
    from screener.signals.garch_signal import garch_signal
    from screener.signals.kalman_signal import kalman_signal
    from screener.signals.monte_carlo_signal import monte_carlo_signal
    from screener.signals.sharpe_signal import sharpe_signal

    for name, fn in [
        ("arima", arima_signal),
        ("kalman", kalman_signal),
        ("garch", garch_signal),
        ("monte_carlo", monte_carlo_signal),
        ("sharpe", sharpe_signal),
    ]:
        result = fn("AAPL", aapl_history, FORECAST_HORIZON_DAYS)
        assert "score" in result, f"{name}: missing 'score'"
        assert "raw" in result, f"{name}: missing 'raw'"
        assert "metadata" in result, f"{name}: missing 'metadata'"
        assert np.isfinite(result["score"]), f"{name}: non-finite score"
        assert 0.0 <= result["score"] <= 1.0, f"{name}: score {result['score']} OOB"


# ---------------------------------------------------------------------------
# GATE 5 — HMM train + predict + hysteresis
# ---------------------------------------------------------------------------
def test_gate5_hmm_train_predict_hysteresis():
    from screener.config import REGIME_HYSTERESIS_DAYS
    from screener.regime.hmm_predictor import get_regime, reset_hysteresis
    from screener.regime.hmm_trainer import train_hmm

    train_hmm()  # uses default 3y lookback for stability
    reset_hysteresis()
    last = None
    for _ in range(REGIME_HYSTERESIS_DAYS):
        last = get_regime()
    assert last is not None
    assert last["regime"] in {"bull", "sideways", "bear"}
    assert "probabilities" in last
    assert "blended_weights" in last
    assert "stable" in last
    bw = last["blended_weights"]
    assert abs(sum(bw.values()) - 1.0) < 1e-6, f"weights sum={sum(bw.values())}"


# ---------------------------------------------------------------------------
# GATE 6 — Single sector rank (H7 dict structure)
# ---------------------------------------------------------------------------
def test_gate6_rank_industry_dict_contract():
    from screener.engine.industry_ranker import rank_industry
    from screener.regime.hmm_predictor import get_regime, reset_hysteresis

    reset_hysteresis()
    regime = get_regime()
    result = rank_industry(
        "Technology", ["AAPL", "MSFT", "NVDA", "AVGO", "META"], regime
    )

    assert isinstance(result, dict)
    for key in ("passed", "skipped", "failed", "total_screened"):
        assert key in result, f"missing {key}"
    assert isinstance(result["passed"], list)
    for s in result["passed"]:
        assert "ticker" in s
        assert "composite_score" in s
        assert "signal_scores" in s
        assert 0.0 <= s["composite_score"] <= 1.0


# ---------------------------------------------------------------------------
# GATE 7 — Full pipeline smoke (slow — full 220 stocks across 11 sectors).
# This is gated behind RUN_SLOW_SCREENER_TESTS=1 to keep the unit-test
# suite under 10 seconds. Runs in CI only when explicitly enabled.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    os.environ.get("RUN_SLOW_SCREENER_TESTS") != "1",
    reason="Slow gate (~70 s); set RUN_SLOW_SCREENER_TESTS=1 to enable",
)
def test_gate7_full_pipeline():
    from screener.config import SECTOR_ETFS, TOP_N_OUTPUT
    from screener.screener_main import run_screener

    results = run_screener(persist_to_db=False)
    assert "regime" in results
    assert "sectors" in results
    assert "summary" in results
    for sector in SECTOR_ETFS:
        assert sector in results["sectors"], f"missing sector {sector}"
        stocks = results["sectors"][sector]
        assert isinstance(stocks, list)
        assert len(stocks) <= TOP_N_OUTPUT


# NOTE: GATE 8 (test_gate8_integration_contract) was removed in the
# Obsidian-native rebuild — it imported the deleted Dash dev stub
# (mock_existing_app) to assert the web app's DataFrame render contract.
# The screener->surface contract is now covered by render/ tests.


# ---------------------------------------------------------------------------
# GATE 9 — Cross-platform atomic write (C5)
# ---------------------------------------------------------------------------
def test_gate9_atomic_write():
    from screener.regime.hmm_trainer import atomic_write_model

    bundle = {"test": "data", "version": 3}
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "test_model.pkl")
        atomic_write_model(bundle, path)
        assert os.path.exists(path), "Model file not written"
        assert joblib.load(path) == bundle
        assert not os.path.exists(path + ".tmp"), ".tmp not cleaned up"


# ---------------------------------------------------------------------------
# GATE 10 — Sector key alignment (H6)
# ---------------------------------------------------------------------------
def test_gate10_sector_key_alignment():
    from screener.config import SECTOR_ETFS, STOCKS_PER_SECTOR

    with open("screener/data/holdings.json") as f:
        holdings = json.load(f)
    sectors = {k for k in holdings if not k.startswith("_")}
    config = set(SECTOR_ETFS.keys())
    assert sectors == config, (
        f"Mismatch! Missing: {config - sectors} | Extra: {sectors - config}"
    )
    for sector in sectors:
        # ≤ cap, not ==: a sector may carry fewer after dual-class dedup
        # (Communications dropped GOOG/FOX/NWS — see the dedup change).
        assert 0 < len(holdings[sector]) <= STOCKS_PER_SECTOR, (
            f"{sector}: {len(holdings[sector])} tickers, expected 1..{STOCKS_PER_SECTOR}"
        )
    # No dual-class duplicates re-creep in (same company, two share classes).
    allt = [t for s in sectors for t in holdings[s]]
    for redundant in ("GOOG", "FOX", "NWS"):
        assert redundant not in allt, f"dual-class duplicate back in universe: {redundant}"


# ---------------------------------------------------------------------------
# GATE 11 — Skipped/failed ticker tracking (H7)
# ---------------------------------------------------------------------------
def test_gate11_skipped_failed_tracking():
    from screener.engine.industry_ranker import rank_industry
    from screener.regime.hmm_predictor import get_regime, reset_hysteresis

    reset_hysteresis()
    regime = get_regime()
    # Fake ticker should land in failed/skipped, not crash the pipeline
    result = rank_industry("Technology", ["AAPL", "FAKE_TICKER_XYZ123"], regime)
    assert "skipped" in result
    assert "failed" in result
    assert "total_screened" in result
    assert result["total_screened"] == 2


# ---------------------------------------------------------------------------
# Additional invariants beyond the 11 gates
# ---------------------------------------------------------------------------
def test_blended_weights_sum_to_one_for_synthetic_distributions():
    from screener.regime.weight_matrix import get_blended_weights

    for probs in [
        {"bull": 1.0, "sideways": 0.0, "bear": 0.0},
        {"bull": 0.0, "sideways": 1.0, "bear": 0.0},
        {"bull": 0.0, "sideways": 0.0, "bear": 1.0},
        {"bull": 1 / 3, "sideways": 1 / 3, "bear": 1 / 3},
        {"bull": 0.5, "sideways": 0.3, "bear": 0.2},
    ]:
        b = get_blended_weights(probs)
        assert math.isclose(sum(b.values()), 1.0, abs_tol=1e-6)


def test_veto_thresholds_tighten_in_bear():
    from screener.config import VETO_THRESHOLDS

    bull = VETO_THRESHOLDS["bull"]
    bear = VETO_THRESHOLDS["bear"]
    side = VETO_THRESHOLDS["sideways"]
    assert bear["garch_vol"] < side["garch_vol"] < bull["garch_vol"]
    assert bear["mc_loss_prob"] < side["mc_loss_prob"] < bull["mc_loss_prob"]


def test_composite_scorer_zero_when_vetoed(aapl_history: pd.DataFrame):
    """Vetoed stocks must have composite_score=0 and zero contributions."""
    from screener.engine.composite_scorer import score_stock

    # Construct a regime_data with bear thresholds that any reasonable
    # stock will fail
    regime_data = {
        "regime": "bear",
        "confidence": 0.99,
        "stable": True,
        "probabilities": {"bull": 0.0, "sideways": 0.0, "bear": 1.0},
        "blended_weights": {
            "arima": 0.15, "kalman": 0.15, "garch": 0.35,
            "monte_carlo": 0.25, "sharpe": 0.10,
        },
    }
    res = score_stock("AAPL", regime_data, aapl_history)
    if not res["passed_veto"]:
        assert res["composite_score"] == 0.0
        assert all(v == 0.0 for v in res["signal_contributions"].values())


def test_screener_glossary_terms_present():
    """All Phase J glossary terms must be present (slice-7 + slice-8 narrative)."""
    from glossary import GLOSSARY

    must_have = {
        "regime", "veto_gate", "composite_score_screener",
        "arima", "kalman", "garch", "mc_loss_prob",
        "signal_ic", "walk_forward",
    }
    missing = must_have - set(GLOSSARY.keys())
    assert not missing, f"Phase J glossary missing: {missing}"


def test_screener_results_table_exists():
    """The additive schema tables must exist after init_db."""
    import sqlite3

    from utils.config import load_settings

    conn = sqlite3.connect(load_settings().db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('screener_results', 'screener_runs') "
        "ORDER BY name"
    )
    tables = [r[0] for r in cur.fetchall()]
    conn.close()
    assert tables == ["screener_results", "screener_runs"]


def test_signal_insufficient_history_returns_zero(aapl_history: pd.DataFrame):
    """The min-history guard must return score=0 with an error in metadata."""
    from screener.signals.arima_signal import arima_signal
    from screener.signals.garch_signal import garch_signal
    from screener.signals.kalman_signal import kalman_signal
    from screener.signals.monte_carlo_signal import monte_carlo_signal
    from screener.signals.sharpe_signal import sharpe_signal

    short = aapl_history.head(10)  # below every signal's min-history floor
    for fn in (arima_signal, kalman_signal, garch_signal, monte_carlo_signal, sharpe_signal):
        out = fn("AAPL", short, horizon=20)
        assert out["score"] == 0.0
        assert out["metadata"].get("error") == "insufficient_history"
