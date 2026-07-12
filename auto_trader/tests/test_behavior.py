"""Behavior analyzer (U30): roundtrip pairing + trade-quality stats.

Fixtures build synthetic trade_history rows through the real ``log_trade``
writer (conftest isolates TRADER_DB_PATH per test), so the pairing logic is
exercised against the actual ledger schema.
"""
from __future__ import annotations

from auto_trader.monitor.behavior import analyze_behavior, build_roundtrips
from auto_trader.state.portfolio_db import initialize_db, log_trade


def _fill(ticker, action, shares, price, at, cost_basis=None, reason=None,
          regime=None):
    log_trade({
        "ticker": ticker, "action": action, "shares": shares, "price": price,
        "total_value": shares * price, "cost_basis": cost_basis,
        "executed_at": at, "trigger_reason": reason, "regime_at_trade": regime,
    })


def test_simple_roundtrip_pairs_and_pnl():
    initialize_db()
    _fill("AAA", "BUY", 10, 100.0, "2026-01-05T15:00:00", regime="bull")
    _fill("AAA", "SELL", 10, 110.0, "2026-01-25T15:00:00", cost_basis=100.0,
          reason="decay_exit")
    out = build_roundtrips()
    assert out["n_open"] == 0 and out["n_ledger_gaps"] == 0
    (rt,) = out["roundtrips"]
    assert rt["ticker"] == "AAA"
    assert abs(rt["pnl"] - 100.0) < 1e-9          # (110-100)*10
    assert abs(rt["hold_days"] - 20.0) < 0.1
    assert rt["exit_reason"] == "decay_exit"
    assert rt["regime_at_entry"] == "bull"


def test_partial_sells_stay_one_episode():
    # Two buys (lot averaging) then two partial sells → exactly ONE roundtrip
    # closed at the flat point, PnL summed over both sells' cost bases.
    initialize_db()
    _fill("BBB", "BUY", 10, 100.0, "2026-02-01T15:00:00")
    _fill("BBB", "BUY", 10, 120.0, "2026-02-10T15:00:00")
    _fill("BBB", "SELL", 5, 130.0, "2026-02-20T15:00:00", cost_basis=110.0)
    _fill("BBB", "SELL", 15, 90.0, "2026-03-01T15:00:00", cost_basis=110.0)
    out = build_roundtrips()
    (rt,) = out["roundtrips"]
    assert out["n_open"] == 0
    # hand-computed: 5*(130-110) + 15*(90-110) = 100 - 300 = -200
    assert abs(rt["pnl"] - (-200.0)) < 1e-9
    assert rt["entry_at"].startswith("2026-02-01")
    assert rt["exit_at"].startswith("2026-03-01")


def test_open_position_excluded_and_counted():
    initialize_db()
    _fill("CCC", "BUY", 10, 50.0, "2026-03-01T15:00:00")
    out = build_roundtrips()
    assert out["roundtrips"] == [] and out["n_open"] == 1
    report = analyze_behavior()
    assert report["n_roundtrips"] == 0 and report["n_open_positions"] == 1


def test_oversell_is_clamped_not_credited():
    # SELL for more shares than the episode holds → clamp to open shares,
    # count a gap; P&L must use the CLAMPED quantity (no phantom shares).
    initialize_db()
    _fill("OVR", "BUY", 10, 100.0, "2026-03-01T15:00:00")
    _fill("OVR", "SELL", 15, 110.0, "2026-03-10T15:00:00", cost_basis=100.0)
    out = build_roundtrips()
    (rt,) = out["roundtrips"]
    assert abs(rt["pnl"] - 100.0) < 1e-9              # 10 sh, not 15
    assert out["n_ledger_gaps"] == 1
    assert out["n_open"] == 0


def test_null_cost_basis_is_a_loud_gap_not_a_crash():
    initialize_db()
    _fill("DDD", "BUY", 10, 50.0, "2026-03-01T15:00:00")
    _fill("DDD", "SELL", 10, 60.0, "2026-03-10T15:00:00", cost_basis=None)
    out = build_roundtrips()
    (rt,) = out["roundtrips"]
    assert rt["pnl"] == 0.0                        # contributes $0, loudly
    assert out["n_ledger_gaps"] == 1


def _seed_disposition_biased():
    """3 quick winners (5d) + 3 long-held losers (20d) → ratio 4.0 > 1.5."""
    initialize_db()
    for i, t in enumerate(("W1", "W2", "W3")):
        _fill(t, "BUY", 10, 100.0, f"2026-01-{i+1:02d}T15:00:00")
        _fill(t, "SELL", 10, 105.0, f"2026-01-{i+6:02d}T15:00:00",
              cost_basis=100.0, reason="target")
    for i, t in enumerate(("L1", "L2", "L3")):
        _fill(t, "BUY", 10, 100.0, f"2026-02-{i+1:02d}T15:00:00")
        _fill(t, "SELL", 10, 95.0, f"2026-02-{i+21:02d}T15:00:00",
              cost_basis=100.0, reason="stop_loss")


def test_disposition_ratio_flagged_on_biased_fixture():
    _seed_disposition_biased()
    report = analyze_behavior()
    assert report["n_roundtrips"] == 6
    disp = report["disposition"]
    assert disp is not None
    assert abs(disp["ratio"] - 4.0) < 1e-9         # 20d / 5d
    assert disp["flagged"] is True
    assert report["confidence"] == "low"           # 6 < MIN_SAMPLE, labelled


def test_stop_churn_reentry_detected():
    initialize_db()
    _fill("EEE", "BUY", 10, 100.0, "2026-01-01T15:00:00")
    _fill("EEE", "SELL", 10, 90.0, "2026-01-10T15:00:00", cost_basis=100.0,
          reason="stop_loss")
    _fill("EEE", "BUY", 10, 91.0, "2026-01-13T15:00:00")   # 3d later → churn
    _fill("EEE", "SELL", 10, 95.0, "2026-02-01T15:00:00", cost_basis=91.0,
          reason="decay_exit")
    report = analyze_behavior()
    churn = report["stop_churn"]
    assert len(churn) == 1
    assert churn[0]["ticker"] == "EEE"


def test_breakdowns_and_headline_stats():
    _seed_disposition_biased()
    report = analyze_behavior()
    assert abs(report["win_rate"] - 0.5) < 1e-9
    assert report["profit_factor"] is not None
    reasons = {r["group"] for r in report["by_exit_reason"]}
    assert {"target", "stop_loss"} <= reasons
    assert report["attribution"]["band_lo_days"] <= report["attribution"]["band_hi_days"]


def test_empty_ledger_report():
    initialize_db()
    report = analyze_behavior()
    assert report["n_roundtrips"] == 0
    assert report["confidence"] == "low"
