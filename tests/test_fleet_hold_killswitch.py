"""The SPY buy-hold control (fleet._run_hold_cycle) submits directly to the
broker client — the one order path that bypasses order_executor — so it must
still honor the global kill-switch, or a tripped flag wouldn't stop it
(stress-test finding, 2026-07)."""
from __future__ import annotations


def test_hold_cycle_honors_kill_switch(monkeypatch, tmp_path):
    from auto_trader import fleet

    flag = tmp_path / ".halt"
    monkeypatch.setattr("auto_trader.credentials.HALT_FLAG_PATH", flag)
    from auto_trader.credentials import set_halt
    set_halt("stress-test", by="test")

    class _Client:
        def list_positions(self):
            return []

        def get_account(self):
            raise AssertionError("account read must not run under kill-switch")

        def submit_order(self, *a, **k):
            raise AssertionError("submit_order must not run under kill-switch")

    monkeypatch.setattr("auto_trader.broker.alpaca_client.get_client",
                        lambda: _Client())
    out = fleet._run_hold_cycle({"id": "spy", "symbol": "SPY"})
    assert out["status"] == "halted"
