"""Phase 29 — accounting invariants: the permanent correctness floor.

Property-style checks driven through the REAL code paths (mock broker,
portfolio_db, reconciler, render), so a future change that breaks any P&L
identity fails here before it ships. Every test isolates its own state
(tmp TRADER_DB_PATH / in-memory broker) — the live book is never touched.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import mock_broker
from mock_broker import MockAlpacaClient

RENDER_DIR = Path(__file__).resolve().parent.parent / "render"


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "portfolio.db"))
    from auto_trader.state.portfolio_db import initialize_db
    initialize_db()
    return tmp_path


# ── I1: conservation — buy then sell at the same price restores cash ────────

def test_conservation_round_trip():
    c = MockAlpacaClient(cash=10_000.0)          # in-memory, flat $100 marks
    c.submit_order("AAA", "buy", qty=7)
    c.submit_order("AAA", "sell", qty=7)
    assert c._cash == pytest.approx(10_000.0, abs=1e-9)
    assert c.list_positions() == []


# ── I2: WACC preserves total invested dollars across multi-buys ─────────────

def test_wacc_preserves_invested_dollars(monkeypatch):
    c = MockAlpacaClient(cash=10_000.0)
    monkeypatch.setattr(mock_broker, "MOCK_PRICE", 100.0)
    c.submit_order("BBB", "buy", qty=5)          # $500 @ 100
    monkeypatch.setattr(mock_broker, "MOCK_PRICE", 110.0)
    c.submit_order("BBB", "buy", qty=5)          # $550 @ 110
    pos = c._positions["BBB"]
    assert pos["qty"] == 10
    assert pos["cost"] == pytest.approx(105.0)   # (500+550)/10
    # cash out == qty × WACC (invested dollars preserved)
    assert 10_000.0 - c._cash == pytest.approx(pos["qty"] * pos["cost"])


# ── M1: negative-cash guard — a real broker rejects an overdraft ────────────

def test_buy_overdraft_rejected():
    c = MockAlpacaClient(cash=500.0)
    with pytest.raises(ValueError, match="insufficient cash"):
        c.submit_order("CCC", "buy", qty=6)      # $600 > $500
    assert c._cash == 500.0                      # nothing debited
    assert "CCC" not in c._positions
    # notional == cash (the SPY-hold pattern) must still pass exactly
    c.submit_order("SPY", "buy", notional=500.0)
    assert c._cash == pytest.approx(0.0, abs=0.01)


# ── M3: oversell is clamped LOUDLY, never silently ──────────────────────────

def test_oversell_clamped_loudly(caplog):
    c = MockAlpacaClient(cash=10_000.0)
    c.submit_order("DDD", "buy", qty=5)
    with caplog.at_level("WARNING", logger="mock_broker"):
        c.submit_order("DDD", "sell", qty=50)    # only 5 held
    assert "oversell clamped" in caplog.text
    assert "DDD" not in c._positions             # sold everything held
    assert c._cash == pytest.approx(10_000.0)    # only 5 sh credited back


# ── M2: NULL cost_basis contributes $0 to realized P&L — but loudly ─────────

def test_realized_pnl_null_cost_basis_is_loud(temp_db, caplog):
    from auto_trader.state.portfolio_db import (compute_realized_pnl_ytd,
                                                log_trade)
    log_trade({"ticker": "EEE", "action": "SELL", "shares": 3, "price": 120.0,
               "total_value": 360.0, "cost_basis": 100.0})
    log_trade({"ticker": "FFF", "action": "SELL", "shares": 2, "price": 50.0,
               "total_value": 100.0, "cost_basis": None})   # the ledger gap
    with caplog.at_level("WARNING"):
        realized = compute_realized_pnl_ytd()
    assert realized == pytest.approx(60.0)       # only EEE: (120−100)×3
    assert "NULL cost_basis" in caplog.text and "FFF" in caplog.text


# ── I3: the reconciler — clean book passes, tampering is pinpointed ─────────

def _seed_book(tmp_path, monkeypatch):
    """A tiny consistent book: ledger + broker json + positions table."""
    from auto_trader.state.portfolio_db import log_trade, upsert_position
    log_trade({"ticker": "GGG", "action": "BUY", "shares": 10, "price": 100.0,
               "total_value": 1000.0, "cost_basis": 100.0})
    log_trade({"ticker": "GGG", "action": "SELL", "shares": 4, "price": 110.0,
               "total_value": 440.0, "cost_basis": 100.0})
    broker = {"cash": 10_000.0 - 1000.0 + 440.0,
              "positions": {"GGG": {"qty": 6.0, "cost": 100.0}},
              "orders": {}, "order_seq": 2}
    bpath = tmp_path / "broker.json"
    bpath.write_text(json.dumps(broker))
    monkeypatch.setenv("MOCK_BROKER_STATE", str(bpath))
    upsert_position({"ticker": "GGG", "shares": 6.0, "cost_basis": 100.0,
                     "total_cost": 600.0, "current_price": 105.0,
                     "sector": "Tech", "entry_date": "2026-01-02",
                     "entry_score": 0.7, "last_score": 0.7,
                     "last_scored_at": None, "stop_loss_price": 90.0,
                     "target_allocation": 0.1, "status": "ACTIVE",
                     "regime_at_entry": "bull"})
    return bpath


def test_reconciler_clean_book_passes(temp_db, monkeypatch):
    _seed_book(temp_db, monkeypatch)
    from auto_trader.monitor.reconciler import reconcile
    r = reconcile()
    assert r["ok"], r["discrepancies"]
    assert r["n_checks"] >= 4


def test_reconciler_pinpoints_tampered_field(temp_db, monkeypatch):
    bpath = _seed_book(temp_db, monkeypatch)
    d = json.loads(bpath.read_text())
    d["cash"] += 123.45                          # inject drift
    bpath.write_text(json.dumps(d))
    from auto_trader.monitor.reconciler import reconcile
    r = reconcile()
    assert not r["ok"]
    fields = [x["field"] for x in r["discrepancies"]]
    assert fields == ["cash(broker vs ledger)"]
    assert r["discrepancies"][0]["delta"] == pytest.approx(123.45)


def test_reconciler_replay_realized_matches_column(temp_db, monkeypatch):
    _seed_book(temp_db, monkeypatch)
    from auto_trader.monitor.reconciler import _replay_ledger
    from auto_trader.state.portfolio_db import compute_realized_pnl_ytd
    replay = _replay_ledger()
    assert replay["realized_ytd"] == pytest.approx(40.0)   # (110−100)×4
    assert compute_realized_pnl_ytd() == pytest.approx(replay["realized_ytd"])
    assert replay["cash"] == pytest.approx(9440.0)


# ── A2 regression: KPI/verdict return-% base is the $10k constant ───────────

def test_kpi_return_base_is_starting_capital():
    from render import html
    # total − pnl = 10,400 here: the old base formula would print 5.8%,
    # the starting-capital base prints 6.0%.
    snap = {"total_value": 11_000.0, "unrealized_pnl": 500.0,
            "realized_pnl_ytd": 100.0, "cash": 1000.0, "n_positions": 3}
    out = html._kpis({"latest_snapshot": snap, "positions": []})
    assert "6.0%" in out and "5.8%" not in out


# ── A1 regression: ROE is stored as a fraction and rendered as one ──────────

def test_roe_renders_as_percent_not_hundredth():
    from render import html
    rows = [{"ticker": "GD", "health_label": "STRONG", "health_score": 0.8,
             "floors_passed": 4, "floors_total": 5, "roe": 0.18,
             "debt_to_equity": 0.38, "pe": 20.0, "valuation_verdict": "fair",
             "next_earnings": None, "last_surprise_pct": None,
             "last_verdict": None}]
    out = html._company_health_section(rows)
    assert "18.0%" in out
    assert ">0.2%<" not in out


# ── A3 guard: pct() must never receive a pre-multiplied percentage ──────────

def test_pct_convention_static_gate():
    """pct(x) formats a FRACTION (×100 inside). Any call site handing it an
    already-×100 value silently renders 2500% — ban the pattern statically."""
    bad: list[str] = []
    pat = re.compile(r"pct\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
    for src in RENDER_DIR.glob("*.py"):
        for i, line in enumerate(src.read_text().splitlines(), 1):
            for arg in pat.findall(line):
                if re.search(r"\*\s*100(?:\.0)?\b|\b100(?:\.0)?\s*\*", arg) \
                        or re.search(r"\w+_pct\b", arg):
                    bad.append(f"{src.name}:{i}: pct({arg})")
    assert not bad, "pct() given a pre-multiplied %:\n" + "\n".join(bad)


# ── I5: rendered positions table — Σ %Port == 100, value/pnl identities ─────

def test_positions_port_pct_sums_to_100():
    from render import html
    positions = [
        {"ticker": "AAA", "sector": "Tech", "shares": 10, "cost_basis": 100.0,
         "current_price": 110.0, "status": "ACTIVE"},
        {"ticker": "MRK", "sector": "Healthcare", "shares": 5, "cost_basis": 80.0,
         "current_price": 90.0, "status": "ACTIVE"},
        {"ticker": "NUE", "sector": "Materials", "shares": 2, "cost_basis": 150.0,
         "current_price": 140.0, "status": "ACTIVE"},
    ]
    out = html._positions_section(positions)
    ports = [float(m) for m in re.findall(r'data-port="([\d.]+)"', out)]
    assert sum(ports) == pytest.approx(100.0, abs=0.1)
    vals = [float(m) for m in re.findall(r'data-val="([\d.]+)"', out)]
    assert sum(vals) == pytest.approx(10*110 + 5*90 + 2*140)


# ── I7: bounds on rendered surfaces ─────────────────────────────────────────

def test_snapshot_identity_total_equals_cash_plus_invested(temp_db, monkeypatch):
    """The daily snapshot's own identity, checked by the reconciler."""
    _seed_book(temp_db, monkeypatch)
    from auto_trader.state.portfolio_db import log_portfolio_snapshot
    from auto_trader.monitor.reconciler import reconcile
    # A snapshot whose fields all agree with the seeded book.
    log_portfolio_snapshot({
        "total_value": 9440.0 + 6 * 105.0, "cash": 9440.0,
        "invested_value": 6 * 105.0, "unrealized_pnl": 6 * 5.0,
        "realized_pnl_ytd": 40.0, "n_positions": 1, "regime": "bull",
        "benchmark_value": None, "drawdown_from_peak": 0.0})
    r = reconcile()
    assert r["ok"], r["discrepancies"]
    # Now a snapshot violating total = cash + invested must be flagged.
    log_portfolio_snapshot({
        "total_value": 10_500.0, "cash": 9440.0,      # ≠ 9440 + 630
        "invested_value": 6 * 105.0, "unrealized_pnl": 30.0,
        "realized_pnl_ytd": 40.0, "n_positions": 1, "regime": "bull",
        "benchmark_value": None, "drawdown_from_peak": 0.0})
    r2 = reconcile()
    assert not r2["ok"]
    assert any("identity" in d["field"] for d in r2["discrepancies"])
