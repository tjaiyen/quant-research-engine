"""Tests for the evaluation layer: forward-pick scorecard + note renders."""
from __future__ import annotations

from datetime import date

import pytest

from screener.backtest.scorecard import summarize, compute_scorecard, paper_vs_spy
from render import notes


# ── summarize (pure) ─────────────────────────────────────────────────────────

def test_summarize_empty():
    s = summarize([])
    assert s["n"] == 0
    assert s["hit_rate"] is None and s["avg_alpha"] is None


def test_summarize_math():
    graded = [
        {"forward_return": 0.10, "alpha": 0.04},   # up, beat
        {"forward_return": -0.02, "alpha": 0.01},  # down, beat
        {"forward_return": 0.05, "alpha": -0.03},  # up, lagged
    ]
    s = summarize(graded)
    assert s["n"] == 3
    assert s["hit_rate"] == pytest.approx(2 / 3)   # 2 of 3 beat SPY
    assert s["up_rate"] == pytest.approx(2 / 3)    # 2 of 3 rose
    assert s["avg_return"] == pytest.approx((0.10 - 0.02 + 0.05) / 3)
    assert s["avg_alpha"] == pytest.approx((0.04 + 0.01 - 0.03) / 3)


# ── compute_scorecard against a seeded temp DB ───────────────────────────────

def test_compute_scorecard_grades_old_run(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cockpit.sqlite"))
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "portfolio.db"))

    from utils.db import init_db, get_conn, upsert_ticker, upsert_prices
    import pandas as pd
    init_db()

    # A run 30 days before our fixed 'today' so the 7d + 28d horizons elapse.
    today = date(2026, 6, 30)
    run_at = "2026-05-31T12:00:00+00:00"
    for t in ("AAA", "SPY"):
        upsert_ticker(t)
    # AAA rises 10% over the window; SPY rises 4% → alpha +6%.
    idx = pd.to_datetime(["2026-05-31", "2026-06-07", "2026-06-28", "2026-06-30"])
    def mkdf(p0, p1):
        vals = [p0, p0 * 1.0, p1, p1]
        return pd.DataFrame({"open": vals, "high": vals, "low": vals,
                             "close": vals, "adj_close": vals, "volume": [1, 1, 1, 1]},
                            index=idx)
    upsert_prices("AAA", mkdf(100.0, 110.0))
    upsert_prices("SPY", mkdf(400.0, 416.0))

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO screener_runs(run_at, regime_label, regime_confidence, "
            "regime_stable, total_sectors, total_screened, total_passed_veto, "
            "total_skipped, total_failed, veto_rate_pct, elapsed_seconds, payload_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_at, "bull", 0.9, 1, 1, 1, 1, 0, 0, 0.0, 1.0, "{}"),
        )
        conn.execute(
            "INSERT INTO screener_results(run_at, ticker, sector, rank, composite_score, "
            "regime, regime_confidence, passed_veto, veto_relaxed, relaxation_passes, "
            "signal_scores_json, signal_contributions_json, top_overall_rank) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_at, "AAA", "Tech", 1, 0.7, "bull", 0.9, 1, 0, 0, "{}", "{}", 1),
        )

    data = compute_scorecard(today=today)
    assert data["n_graded_runs"] == 1
    s7 = data["horizons"]["7d"]
    s28 = data["horizons"]["28d"]
    assert s7["n"] == 1 and s28["n"] == 1
    assert s28["avg_alpha"] == pytest.approx(0.10 - 0.04, abs=1e-6)  # +6%
    assert s28["hit_rate"] == 1.0
    # 84d horizon hasn't elapsed (run is 30 days old) → empty.
    assert data["horizons"]["84d"]["n"] == 0


# ── paper_vs_spy ─────────────────────────────────────────────────────────────

def test_paper_vs_spy(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "portfolio.db"))
    from auto_trader.state import portfolio_db as pdb
    pdb.initialize_db()
    pdb.log_portfolio_snapshot({"snapshot_date": "2026-06-01", "total_value": 10000.0,
                                "cash": 0.0, "invested_value": 10000.0, "unrealized_pnl": 0.0,
                                "realized_pnl_ytd": 0.0, "n_positions": 5, "regime": "bull",
                                "benchmark_value": 100.0, "drawdown_from_peak": 0.0})
    pdb.log_portfolio_snapshot({"snapshot_date": "2026-06-30", "total_value": 11000.0,
                                "cash": 0.0, "invested_value": 11000.0, "unrealized_pnl": 1000.0,
                                "realized_pnl_ytd": 0.0, "n_positions": 5, "regime": "bull",
                                "benchmark_value": 105.0, "drawdown_from_peak": 0.0})
    p = paper_vs_spy()
    assert p["status"] == "ok"
    assert p["port_return"] == pytest.approx(0.10)   # 10000 → 11000
    assert p["spy_return"] == pytest.approx(0.05)    # 100 → 105
    assert p["excess"] == pytest.approx(0.05)


# ── note renders (no crash, verdict present) ─────────────────────────────────

def test_scorecard_note_too_early():
    data = {"as_of": "2026-06-19T00:00:00", "n_runs": 1, "n_graded_runs": 0,
            "horizons": {k: summarize([]) for k in ("7d", "28d", "84d", "to_date")},
            "top_horizons": {}, "paper": {"status": "no_data"}}
    md = notes.scorecard_note(data)
    assert "Too early" in md
    assert "type: tracker-scorecard" in md


def test_scorecard_note_beating():
    good = [{"forward_return": 0.1, "alpha": 0.05} for _ in range(6)]
    data = {"as_of": "x", "n_runs": 4, "n_graded_runs": 4,
            "horizons": {"7d": summarize(good), "28d": summarize(good),
                         "84d": summarize([]), "to_date": summarize(good)},
            "top_horizons": {}, "paper": {"status": "ok", "port_return": 0.1,
                                          "spy_return": 0.05, "excess": 0.05, "n_days": 30}}
    md = notes.scorecard_note(data)
    assert "beating the market" in md


def test_backtest_note_renders():
    data = {"as_of": "x",
            "walk_forward": {"n_windows": 4, "mean_lift": 0.012, "win_rate": 0.75,
                             "by_regime": {"bull": {"win_rate": 0.8}}},
            "ic": {"horizon_days": 20, "aggregate": {"arima": 0.05, "garch": -0.01}},
            "regime": {"monotone_bull_gt_bear": True,
                       "regimes": [{"regime": "bull", "mean_forward_logret": 0.01,
                                    "n_observations": 100}]}}
    md = notes.backtest_note(data)
    assert "skill" in md.lower()
    assert "arima" in md
    assert "type: tracker-backtest" in md
