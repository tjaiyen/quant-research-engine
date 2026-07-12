"""pytest config: ensure project root is on sys.path so tests can import modules
without `python -m`, and isolate LIVE paper state from every test.

The isolation fixture mirrors auto_trader/tests/conftest.py: without it a test
that touches the ledger through the real writers pollutes ``store/portfolio.db``
(the stray-AAA leak this fixture was added after — same class as the July-1
stray-BBB incident). Tests that set their own paths via monkeypatch still win:
their setenv overrides this one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_paper_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "test_portfolio.db"))
    monkeypatch.setenv("MOCK_BROKER_STATE", str(tmp_path / "test_broker.json"))
    # DB_PATH too: its default is the LIVE store/cockpit.sqlite, and utils.db
    # writers (set_setting/upsert_*) otherwise hit it — an upsert nets zero
    # row-count change, so even the canary below can't see it. The tmp DB gets
    # the schema (empty-but-schema'd, like the old db/ decoy) so data-
    # conditional tests SKIP gracefully instead of crashing on missing tables.
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test_cockpit.sqlite"))
    # Force the mock broker: now that real Alpaca paper keys live in .env,
    # any test reaching get_client() would otherwise talk to the LIVE paper
    # API (found 2026-07-12 — a reconciler test hit the real account).
    monkeypatch.setenv("ALPACA_USE_MOCK", "true")
    from utils.db import init_db
    init_db()
    yield


def _live_row_counts() -> dict:
    """Per-table row counts of the LIVE stores (read-only; {} if absent)."""
    import sqlite3

    out: dict = {}
    for db in ("store/cockpit.sqlite", "store/portfolio.db"):
        p = ROOT / db
        if not p.exists():
            continue
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            for (t,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"):
                out[f"{db}:{t}"] = conn.execute(
                    f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        finally:
            conn.close()
    return out


@pytest.fixture(autouse=True, scope="session")
def _live_store_write_canary():
    """Fail LOUDLY if any test writes rows into the live stores.

    DB_PATH now defaults to the real store/cockpit.sqlite, and some
    data-conditional tests deliberately READ it — so blanket isolation would
    cost coverage. This canary keeps reads open but turns any write leak
    (the stray-BBB/AAA incident class) into a session-level failure.
    """
    before = _live_row_counts()
    yield
    after = _live_row_counts()
    drift = {k: (before.get(k), after.get(k))
             for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert not drift, (
        f"LIVE store row counts changed during the test session: {drift} — "
        "a test wrote to the real cockpit/ledger; isolate its DB_PATH/"
        "TRADER_DB_PATH."
    )
