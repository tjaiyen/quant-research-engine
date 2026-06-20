"""Tests for U4 strategy portfolio backtest (pure metrics + injected sim)."""
from __future__ import annotations

import pandas as pd
import pytest

from screener.backtest import portfolio_backtest as pb
from render import notes


# ── pure metric helpers ──────────────────────────────────────────────────────

def test_cagr():
    assert pb._cagr(0.0, 3) == pytest.approx(0.0)
    assert pb._cagr(1.0, 1) == pytest.approx(1.0)        # doubled in 1y → 100%
    assert pb._cagr(0.331, 3) == pytest.approx(0.10, abs=1e-3)  # ~10%/yr for 3y


def test_max_drawdown():
    assert pb._max_drawdown([100, 110, 120]) == pytest.approx(0.0)   # monotone up
    assert pb._max_drawdown([100, 120, 90, 110]) == pytest.approx(90/120 - 1)
    assert pb._max_drawdown([]) == 0.0


def test_sharpe():
    assert pb._sharpe([0.05, 0.05, 0.05], 4) is None      # zero variance
    assert pb._sharpe([0.1], 4) is None                   # too few
    s = pb._sharpe([0.1, -0.05, 0.08, 0.02], 4)
    assert s is not None and isinstance(s, float)


# ── injected end-to-end sim (no real signals / HMM / network) ────────────────

def test_run_portfolio_backtest_injected(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cockpit.sqlite"))
    from utils.db import init_db, upsert_ticker, upsert_prices
    init_db()

    idx = pd.date_range("2023-01-02", periods=520, freq="B")
    # Rising series → positive returns for picks + SPY.
    def seed(t, drift):
        close = [100.0 * (drift ** i) for i in range(len(idx))]
        upsert_ticker(t)
        upsert_prices(t, pd.DataFrame({"open": close, "high": close, "low": close,
                                       "close": close, "adj_close": close,
                                       "volume": [1] * len(idx)}, index=idx))
    for t in ("AAA", "BBB"):
        seed(t, 1.0008)
    seed("SPY", 1.0004)

    # Synthetic market features over the same dates (regime_fn injected, so the
    # values don't matter — only the index drives rebalance dates).
    feats = pd.DataFrame(
        {"log_return": 0.0, "realized_vol_20d": 0.1, "vix_normalized": 1.0,
         "breadth_pct": 1.0}, index=idx)
    monkeypatch.setattr(pb, "get_market_features", lambda **k: feats, raising=False)
    import screener.data.market_features as mf
    monkeypatch.setattr(mf, "get_market_features", lambda **k: feats)
    import screener.backtest.walk_forward as wf
    monkeypatch.setattr(wf, "_load_universe", lambda: {"Tech": ["AAA", "BBB"]})

    data = pb.run_portfolio_backtest(
        years=1, rebalance="quarter", max_per_sector=None,
        score_fn=lambda t, r, ph: {"composite_score": 0.7, "passed_veto": True},
        regime_fn=lambda f, d: {"regime": "bull", "confidence": 0.9,
                                "blended_weights": {}, "probabilities": {}},
    )
    assert data["n_rebalances"] >= 2
    assert len(data["equity_curve"]) == data["n_rebalances"] + 1
    m = data["metrics"]
    assert m["total_return"] > 0          # rising series → positive
    assert m["excess"] == pytest.approx(m["total_return"] - m["spy_total_return"])
    assert -1.0 <= m["max_drawdown"] <= 0.0


# ── note rendering ───────────────────────────────────────────────────────────

def test_strategy_backtest_note_renders():
    data = {"as_of": "x", "years": 3, "rebalance": "quarter", "n_rebalances": 12,
            "avg_picks": 40.0,
            "equity_curve": [{"date": "2023-01-01", "strategy": 100.0, "spy": 100.0},
                             {"date": "2023-04-01", "strategy": 105.0, "spy": 103.0}],
            "metrics": {"total_return": 0.25, "spy_total_return": 0.15, "excess": 0.10,
                        "cagr": 0.077, "max_drawdown": -0.08, "sharpe": 1.1,
                        "win_rate": 0.6}}
    md = notes.strategy_backtest_note(data)
    assert "type: tracker-strategy-backtest" in md
    assert "portfolio simulation" in md
    assert "```chart" in md
    assert "edge" in md


def test_strategy_backtest_note_empty():
    md = notes.strategy_backtest_note({"equity_curve": [], "metrics": {}, "years": 3})
    assert "Not enough cached history" in md
    assert "type: tracker-strategy-backtest" in md
