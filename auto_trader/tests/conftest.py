"""Suite-wide isolation for auto_trader tests.

The LIVE paper state must be unreachable from tests. TRADER_DB_PATH was already
isolated per-run (the README's test command), but MOCK_BROKER_STATE was NOT —
so any test that touched the broker singleton wrote orders into the REAL
``store/mock_broker.json`` (the July-1 'BBB 8sh @ $100' pollution, a recurrence
of the 6/24 stray-BBB incident). Every test now gets throwaway paths for BOTH.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_paper_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "test_portfolio.db"))
    monkeypatch.setenv("MOCK_BROKER_STATE", str(tmp_path / "test_broker.json"))
    yield
