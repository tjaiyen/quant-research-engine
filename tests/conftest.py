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
    yield
