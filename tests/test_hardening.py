"""Stress-test remediation tests (Phase 7): doctor negative paths, scorecard
coverage, graceful cold-DB backtest, defensive note rendering."""
from __future__ import annotations

import argparse

import pytest


# ── doctor.py negative paths (pure functions, no FS install) ─────────────────

def test_doctor_rejects_synced_store():
    import doctor
    r = doctor.check_store_local(
        "/Users/x/Library/CloudStorage/GoogleDrive-a/My Drive 2/Vault/store"
    )
    assert r["safe"] is False
    assert any("cloud-sync" in reason or "sync" in reason for reason in r["reasons"])


def test_doctor_rejects_non_canonical_vault():
    import doctor
    # A local /tmp path: not on a sync mount AND not the CloudStorage mount.
    r = doctor.check_vault_canonical("/tmp/not-the-vault")
    assert r["safe"] is False
    reasons = " ".join(r["reasons"]).lower()
    assert "cloud-sync" in reasons or "cloudstorage" in reasons


def test_heal_drive_suffix_resolves_volatile_segment(tmp_path):
    import doctor
    real = tmp_path / "My Drive" / "vault"   # Drive currently shows this
    real.mkdir(parents=True)
    stale = tmp_path / "My Drive 2" / "vault"  # stale config points here (gone)
    assert doctor._heal_drive_suffix(stale) == real        # flips to the existing one
    assert doctor._heal_drive_suffix(real) == real         # existing path unchanged
    missing = tmp_path / "My Drive 2" / "nope"             # neither exists
    assert doctor._heal_drive_suffix(missing) == missing   # returned as-is, no crash


def test_doctor_store_local_ok(tmp_path):
    import doctor
    r = doctor.check_store_local(str(tmp_path / "store"))
    assert r["safe"] is True


# ── scorecard coverage (G3): missing-price picks are visible, not silent ─────

def test_scorecard_reports_coverage(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cockpit.sqlite"))
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "portfolio.db"))

    import pandas as pd
    from utils.db import init_db, get_conn, upsert_ticker, upsert_prices
    from screener.backtest.scorecard import compute_scorecard
    init_db()

    run_at = "2026-05-31T12:00:00+00:00"
    idx = pd.to_datetime(["2026-05-31", "2026-06-28"])
    def mkdf(p):
        v = [p, p]
        return pd.DataFrame({"open": v, "high": v, "low": v, "close": v,
                             "adj_close": v, "volume": [1, 1]}, index=idx)
    # GRADEABLE has prices + SPY exists; NOPRICE has none.
    for t, p in (("GRADEABLE", 100.0), ("SPY", 400.0)):
        upsert_ticker(t); upsert_prices(t, mkdf(p))
    upsert_ticker("NOPRICE")

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO screener_runs(run_at, regime_label, regime_confidence, "
            "regime_stable, total_sectors, total_screened, total_passed_veto, "
            "total_skipped, total_failed, veto_rate_pct, elapsed_seconds, payload_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_at, "bull", 0.9, 1, 1, 2, 2, 0, 0, 0.0, 1.0, "{}"))
        for tk in ("GRADEABLE", "NOPRICE"):
            conn.execute(
                "INSERT INTO screener_results(run_at, ticker, sector, rank, "
                "composite_score, regime, regime_confidence, passed_veto, veto_relaxed, "
                "relaxation_passes, signal_scores_json, signal_contributions_json, "
                "top_overall_rank) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_at, tk, "Tech", 1, 0.7, "bull", 0.9, 1, 0, 0, "{}", "{}", None))

    data = compute_scorecard(today=date(2026, 6, 30))
    s28 = data["horizons"]["28d"]
    assert s28["attempted"] == 2      # both picks attempted
    assert s28["n"] == 1              # only GRADEABLE graded
    assert s28["coverage"] == pytest.approx(0.5)  # 50% coverage — bias is visible


# ── backtest_note is defensive (G2): missing keys render without crashing ────

def test_backtest_note_handles_empty():
    from render import notes
    md = notes.backtest_note({})          # no walk_forward/ic/regime keys
    assert "type: tracker-backtest" in md
    assert "skill" in md.lower()


# ── cmd_backtest degrades gracefully on a failing component (G2) ─────────────

def test_cmd_backtest_friendly_on_failure(monkeypatch, capsys):
    import cli.track as track
    import screener.backtest.signal_ic as sic
    import screener.backtest.walk_forward as wf

    monkeypatch.setattr(track, "_preflight", lambda: None)
    monkeypatch.setattr(wf, "run_walk_forward",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("No price histories")))

    args = argparse.Namespace(windows=1, samples=1, max_per_sector=1, max_tickers=1)
    rc = track.cmd_backtest(args)
    assert rc == 1                                   # non-zero, no traceback
    err = capsys.readouterr().err
    assert "could not run" in err and "seed" in err  # friendly guidance


def test_trader_db_path_is_absolute_and_cwd_independent(tmp_path, monkeypatch):
    # The two-portfolio.db bug: a relative default resolved differently by cwd.
    # Now it must be absolute, repo-anchored, and identical from any directory.
    import os
    from auto_trader.config import get_db_path
    monkeypatch.delenv("TRADER_DB_PATH", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    p1 = get_db_path()
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        p2 = get_db_path()
    finally:
        os.chdir(cwd)
    assert p1 == p2 and os.path.isabs(p1)
    assert p1.endswith("store/portfolio.db")          # the canonical DB, not the stray
