"""The mock broker must persist paper holdings across processes (the autonomous
loop runs cycle/monitor/report in separate `track` invocations) — otherwise the
monitor reconciles against an empty broker and closes every fresh position.
"""
from __future__ import annotations

from mock_broker import MockAlpacaClient


def test_persists_across_instances(tmp_path):
    p = str(tmp_path / "mb.json")
    b1 = MockAlpacaClient(cash=10_000.0, state_path=p)
    b1.submit_order("AAPL", "buy", qty=5)
    b1.submit_order("MSFT", "buy", qty=2)

    # A brand-new client (simulating the next process) loads the saved state.
    b2 = MockAlpacaClient(cash=10_000.0, state_path=p)
    held = {x.symbol: float(x.qty) for x in b2.list_positions()}
    assert held == {"AAPL": 5.0, "MSFT": 2.0}
    # cash carried over (10000 - 7*100)
    assert abs(float(b2.get_account().cash) - 9_300.0) < 1e-6


def test_sell_persists(tmp_path):
    p = str(tmp_path / "mb.json")
    MockAlpacaClient(state_path=p).submit_order("AAPL", "buy", qty=5)
    MockAlpacaClient(state_path=p).submit_order("AAPL", "sell", qty=5)
    b = MockAlpacaClient(state_path=p)
    assert b.list_positions() == []  # fully closed, persisted


def test_in_memory_when_no_path():
    # Tests/default construction must stay isolated (no shared on-disk state).
    MockAlpacaClient().submit_order("AAPL", "buy", qty=3)
    assert MockAlpacaClient().list_positions() == []
