"""Tests for the Obsidian renderer (render/) and its read of the paper ledger.

Pure builders are tested directly; build_all() is tested end-to-end against a
temp vault + a seeded temp paper ledger so nothing touches the real vault.
"""
from __future__ import annotations

import json

import pytest

from render import notes
from render.markdown import atomic_write, frontmatter, money, pct, table


# ── markdown helpers ─────────────────────────────────────────────────────────

def test_frontmatter_scalars_and_lists():
    fm = frontmatter({"ticker": "AAPL", "score": 0.72, "ok": True,
                      "empty": None, "tags": ["a", "b"]})
    assert fm.startswith("---") and fm.rstrip().endswith("---")
    assert "ticker: AAPL" in fm
    assert "score: 0.72" in fm
    assert "ok: true" in fm
    assert "empty: null" in fm
    assert "  - a" in fm and "  - b" in fm


def test_frontmatter_quotes_risky_values():
    fm = frontmatter({"k": "value: with colon", "leading": " spaced "})
    assert '"value: with colon"' in fm
    assert '" spaced "' in fm


def test_table_and_formatters():
    t = table(["A", "B"], [[1, 2], [3, 4]])
    assert "| A | B |" in t and "| 1 | 2 |" in t
    assert table(["A"], []) == "_No rows._"
    assert pct(0.05) == "5.0%"
    assert pct(None) == "—"
    assert money(1234.5) == "$1,234.50"


def test_atomic_write(tmp_path):
    p = tmp_path / "sub" / "note.md"
    atomic_write(p, "hello")
    assert p.read_text() == "hello"
    assert not (tmp_path / "sub" / "note.md.tmp").exists()


# ── pure note builders ───────────────────────────────────────────────────────

FAKE_RESULTS = {
    "generated_at": "2026-06-19T12:00:00+00:00",
    "regime": {"label": "bull", "confidence": 0.82, "stable": True,
               "probabilities": {"bull": 0.8, "sideways": 0.15, "bear": 0.05},
               "blended_weights": {"arima": 0.4, "kalman": 0.2, "garch": 0.1,
                                    "monte_carlo": 0.1, "sharpe": 0.2}},
    "sectors": {
        "Technology": [
            {"rank": 1, "ticker": "AAPL", "composite_score": 0.72,
             "passed_veto": True, "veto_reason": None, "veto_relaxed": False,
             "signal_scores": {}, "signal_contributions": {},
             "regime": "bull", "regime_confidence": 0.82},
        ],
        "Energy": [],
    },
    "summary": {"total_sectors": 2, "total_screened": 40, "total_passed_veto": 1,
                "total_skipped": 0, "total_failed": 0, "veto_rate_pct": 97.5,
                "top_overall": [{"rank": 1, "ticker": "AAPL",
                                 "composite_score": 0.72, "sector": "Technology"}]},
}


def test_screener_run_note():
    md = notes.screener_run_note(FAKE_RESULTS)
    assert 'type: tracker-screener-run' in md
    assert "run_date: 2026-06-19" in md
    assert "AAPL" in md
    assert "Technology" in md
    assert "No stocks passed veto" in md  # Energy sector empty
    assert "AUTO-GENERATED" in md


def test_position_note_pnl_math():
    pos = {"ticker": "MSFT", "sector": "Technology", "shares": 10.0,
           "cost_basis": 100.0, "total_cost": 1000.0, "current_price": 110.0,
           "status": "ACTIVE", "entry_date": "2026-06-01", "entry_score": 0.7,
           "last_score": 0.68, "stop_loss_price": 88.0, "regime_at_entry": "bull"}
    md = notes.position_note(pos)
    assert "ticker: MSFT" in md
    assert "market_value: 1100.0" in md
    assert "unrealized_pnl: 100.0" in md
    assert "unrealized_pct: 0.1" in md


def test_position_note_trade_log_inline_fields():
    pos = {"ticker": "MSFT", "sector": "Technology", "shares": 10.0,
           "cost_basis": 100.0, "total_cost": 1000.0, "current_price": 110.0,
           "status": "ACTIVE"}
    trades = [
        {"executed_at": "2026-07-01T13:30:00", "action": "BUY", "shares": 5.0,
         "price": 98.0, "total_value": 490.0},
        {"executed_at": "2026-07-02T13:30:00", "action": "BUY", "shares": 5.0,
         "price": 102.0, "total_value": 510.0},
    ]
    md = notes.position_note(pos, trades)
    assert "## Trade Log" in md
    assert "[date:: 2026-07-01]" in md and "[action:: BUY]" in md
    assert "[price:: 98.00]" in md
    # No trades → no Trade Log section.
    assert "## Trade Log" not in notes.position_note(pos)


def test_position_note_handles_missing_price():
    pos = {"ticker": "XYZ", "sector": "Energy", "shares": 5.0,
           "cost_basis": 20.0, "total_cost": 100.0, "current_price": None,
           "status": "ACTIVE"}
    md = notes.position_note(pos)  # must not raise
    assert "current_price: null" in md
    assert "unrealized_pnl: null" in md


def test_performance_note_latest_in_frontmatter():
    snaps = [
        {"snapshot_date": "2026-06-18", "total_value": 1000.0, "cash": 200.0,
         "invested_value": 800.0, "unrealized_pnl": 0.0, "realized_pnl_ytd": 0.0,
         "n_positions": 2, "regime": "bull", "drawdown_from_peak": 0.0},
        {"snapshot_date": "2026-06-19", "total_value": 1050.0, "cash": 200.0,
         "invested_value": 850.0, "unrealized_pnl": 50.0, "realized_pnl_ytd": 0.0,
         "n_positions": 2, "regime": "bull", "drawdown_from_peak": 0.0},
    ]
    md = notes.performance_note(snaps)
    assert "as_of: 2026-06-19" in md
    assert "total_value: 1050.0" in md


# ── build_all() end-to-end against a temp vault + seeded ledger ──────────────

def test_build_all_end_to_end(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "portfolio.db"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cockpit.sqlite"))
    monkeypatch.setenv("TRADING_MODE", "paper")

    # Seed the paper ledger via the real ledger APIs (the reuse path).
    from auto_trader.state import portfolio_db as pdb
    pdb.initialize_db()
    pdb.upsert_position({
        "ticker": "AAPL", "shares": 5.0, "cost_basis": 190.0, "total_cost": 950.0,
        "current_price": 200.0, "sector": "Technology", "entry_date": "2026-06-01",
        "entry_score": 0.72, "last_score": 0.70, "last_scored_at": "2026-06-18",
        "stop_loss_price": 167.0, "target_allocation": 0.05, "status": "ACTIVE",
        "regime_at_entry": "bull",
    })
    pdb.log_trade({"ticker": "AAPL", "action": "BUY", "shares": 5.0, "price": 190.0,
                   "total_value": 950.0, "cost_basis": 190.0,
                   "trigger_reason": "screener_pick"})
    pdb.log_portfolio_snapshot({
        "total_value": 1050.0, "cash": 100.0, "invested_value": 950.0,
        "unrealized_pnl": 50.0, "realized_pnl_ytd": 0.0, "n_positions": 1,
        "regime": "bull", "benchmark_value": 500.0, "drawdown_from_peak": 0.0,
    })

    from render.build import build_all
    summary = build_all()

    assert (vault / "90 Tracker" / "Dashboard.md").exists()
    assert (vault / "90 Tracker" / "Performance.md").exists()
    pos_note = vault / "90 Tracker" / "Positions" / "AAPL.md"
    assert pos_note.exists()
    assert "unrealized_pnl: 50.0" in pos_note.read_text()
    # One journal note for the trade's date.
    journals = list((vault / "90 Tracker" / "Journal").glob("*.md"))
    assert len(journals) == 1
    assert summary["n_positions"] == 1
    assert summary["had_screener_run"] is False  # no screener seeded


def test_build_all_with_real_screener_run(tmp_path, monkeypatch):
    """Seed a run through the real write_to_sqlite path, then render from the DB."""
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cockpit.sqlite"))
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "portfolio.db"))

    from utils.db import init_db
    from screener.output.results_formatter import write_to_sqlite
    init_db()  # creates screener_results / screener_runs from schema.sql
    write_to_sqlite(FAKE_RESULTS, elapsed_seconds=1.0)

    from render.build import build_all, latest_screener_results
    assert latest_screener_results() is not None  # round-trips from SQLite

    summary = build_all()
    assert summary["had_screener_run"] is True
    run_note = vault / "90 Tracker" / "Screener" / "Run-2026-06-19.md"
    assert run_note.exists()
    assert "AAPL" in run_note.read_text()
    assert (vault / "90 Tracker" / "Regime.md").exists()
    # Dashboard surfaces the top pick.
    assert "AAPL" in (vault / "90 Tracker" / "Dashboard.md").read_text()


def test_build_all_prunes_closed_positions(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "portfolio.db"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cockpit.sqlite"))

    # A stale position note that no longer corresponds to an open position.
    pos_dir = vault / "90 Tracker" / "Positions"
    atomic_write(pos_dir / "OLD.md", "stale")

    from render.build import build_all
    build_all()
    assert not (pos_dir / "OLD.md").exists()  # pruned
