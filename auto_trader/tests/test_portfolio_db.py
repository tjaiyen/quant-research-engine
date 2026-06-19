"""Phase K — portfolio_db invariants (Gates 3, 11, 17, 18) + compat shim."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def tmp_db(monkeypatch):
    """Spin up a fresh DB in a temp dir; returns the path."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setenv("TRADER_DB_PATH", str(db_path))
        monkeypatch.delenv("DB_PATH", raising=False)
        yield db_path


def _sample_position(ticker: str = "AAPL") -> dict:
    from auto_trader.utils import now_iso, today_iso

    return {
        "ticker": ticker,
        "shares": 10.0,
        "cost_basis": 150.0,
        "total_cost": 1500.0,
        "current_price": 155.0,
        "sector": "Technology",
        "entry_date": today_iso(),
        "entry_score": 0.75,
        "last_score": 0.75,
        "last_scored_at": now_iso(),
        "stop_loss_price": 132.0,
        "target_allocation": 300.0,
        "status": "ACTIVE",
        "regime_at_entry": "bull",
    }


# ---------------------------------------------------------------------------
# GATE 3 — DB schema + migration + cost_basis column
# ---------------------------------------------------------------------------
def test_gate3_schema_creates_all_tables(tmp_db):
    from auto_trader.state.portfolio_db import SCHEMA_VERSION, initialize_db

    initialize_db()

    conn = sqlite3.connect(str(tmp_db))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    cols_th = [
        r[1]
        for r in conn.execute("PRAGMA table_info(trade_history)").fetchall()
    ]
    conn.close()

    for required in (
        "positions",
        "trade_history",
        "signal_history",
        "portfolio_snapshots",
        "system_events",
    ):
        assert required in tables, f"Missing table: {required}"
    assert "cost_basis" in cols_th, "trade_history missing cost_basis column (H4)"
    assert version == SCHEMA_VERSION, (
        f"PRAGMA user_version = {version}, expected {SCHEMA_VERSION}"
    )


# ---------------------------------------------------------------------------
# GATE 11 — created_at NOT overwritten on update; current_price IS updated
# ---------------------------------------------------------------------------
def test_gate11_created_at_preserved_on_upsert(tmp_db):
    from auto_trader.state.portfolio_db import (
        get_position,
        initialize_db,
        upsert_position,
    )

    initialize_db()
    upsert_position(_sample_position())
    t1 = get_position("AAPL")["created_at"]

    time.sleep(0.05)
    pos2 = {**_sample_position(), "current_price": 160.0}
    upsert_position(pos2)

    rec = get_position("AAPL")
    assert rec["created_at"] == t1, (
        f"created_at changed: {t1} → {rec['created_at']}"
    )
    assert rec["current_price"] == 160.0, "current_price not updated"


# ---------------------------------------------------------------------------
# GATE 17 — DB migration v0 → v2 adds cost_basis column without data loss
# ---------------------------------------------------------------------------
def test_gate17_migration_v0_to_v2(tmp_db):
    """Simulate a pre-cost_basis trade_history table; expect migration to add the column."""
    # Manually create the legacy v0 schema (no cost_basis)
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("PRAGMA user_version = 0")
    conn.execute(
        """
        CREATE TABLE trade_history (
            trade_id INTEGER PRIMARY KEY,
            ticker TEXT, action TEXT,
            shares REAL, price REAL, total_value REAL
        )
        """
    )
    conn.commit()
    conn.close()

    from auto_trader.state.portfolio_db import SCHEMA_VERSION, initialize_db

    initialize_db()

    conn = sqlite3.connect(str(tmp_db))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_history)").fetchall()]
    conn.close()

    assert version == SCHEMA_VERSION, (
        f"After migration: version = {version}, expected {SCHEMA_VERSION}"
    )
    assert "cost_basis" in cols, "Migration didn't add cost_basis column"


# ---------------------------------------------------------------------------
# GATE 18 — H4: cost_basis used directly in compute_realized_pnl_ytd
# ---------------------------------------------------------------------------
def test_gate18_realized_pnl_uses_cost_basis(tmp_db):
    from auto_trader.state.portfolio_db import (
        compute_realized_pnl_ytd,
        initialize_db,
        log_trade,
    )

    initialize_db()
    log_trade(
        {
            "ticker": "AAPL",
            "action": "SELL",
            "shares": 10.0,
            "price": 120.0,
            "total_value": 1200.0,
            "cost_basis": 100.0,
            "trigger_reason": "SIGNAL_EXIT",
            "composite_score_at": 0.44,
            "regime_at_trade": "sideways",
        }
    )
    pnl = compute_realized_pnl_ytd()
    assert abs(pnl - 200.0) < 0.01, f"Expected $200.00 P&L, got ${pnl:.2f}"


# ---------------------------------------------------------------------------
# Trade history is append-only
# ---------------------------------------------------------------------------
def test_trade_history_is_append_only(tmp_db):
    """No public delete or update API for trade_history."""
    from auto_trader.state import portfolio_db

    public_names = [n for n in dir(portfolio_db) if not n.startswith("_")]
    forbidden = ("delete_trade", "update_trade", "remove_trade", "edit_trade")
    for name in forbidden:
        assert name not in public_names, (
            f"trade_history must be append-only; found public {name}"
        )


# ---------------------------------------------------------------------------
# Compat shim — Gate 7
# ---------------------------------------------------------------------------
def test_gate7_compat_normalize_with_missing_keys():
    from auto_trader.compat.screener_compat import (
        normalize_screener_cache,
        validate_cache_contract,
    )

    raw = {"sectors": {}, "generated_at": "2026-01-01T00:00:00Z"}
    cache = normalize_screener_cache(raw)
    assert cache["regime"]["label"] == "unknown"
    assert isinstance(cache["sectors"], dict)
    valid, errors = validate_cache_contract(cache)
    assert isinstance(valid, bool)
    # Unknown is acceptable in the normalized contract
    assert valid is True


def test_compat_backfills_sector_from_parent_key():
    from auto_trader.compat.screener_compat import normalize_screener_cache

    raw = {
        "regime": {"label": "bull", "confidence": 0.9},
        "sectors": {
            "Technology": [
                {
                    "ticker": "AAPL",
                    "composite_score": 0.7,
                    "passed_veto": True,
                    "signal_scores": {},
                    # NOTE: no 'sector' key — common case from cockpit Phase J
                }
            ]
        },
    }
    cache = normalize_screener_cache(raw)
    assert cache["sectors"]["Technology"][0]["sector"] == "Technology"


def test_compat_validate_rejects_bad_regime():
    from auto_trader.compat.screener_compat import validate_cache_contract

    bad = {"sectors": {}, "regime": {"label": "totally_made_up"}}
    valid, errors = validate_cache_contract(bad)
    assert valid is False
    assert any("Invalid regime label" in e for e in errors)
