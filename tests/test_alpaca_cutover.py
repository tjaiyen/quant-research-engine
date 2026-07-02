"""Alpaca-cutover hardening (found live on the first real paper cycle).

1. confirm_fills must keep polling a partially_filled order — it is still
   working; settling on the partial recorded only part of the fill in the
   trade ledger (the reconciler caught the drift same-day).
2. The reconciler must read the LIVE broker book when the flagship runs on
   real Alpaca paper (the mock state file no longer exists).
"""
from __future__ import annotations

import pytest


# ── 1. confirm_fills keeps polling partials ─────────────────────────────────

def _seq_status(monkeypatch, sequences: dict):
    """get_order_status returns each order's statuses in sequence."""
    from auto_trader.broker import order_executor as oe
    calls: dict = {}

    def fake_status(oid):
        i = calls.get(oid, 0)
        calls[oid] = i + 1
        seq = sequences[oid]
        return dict(seq[min(i, len(seq) - 1)])

    monkeypatch.setattr(oe, "get_order_status", fake_status)
    monkeypatch.setattr(oe, "FILL_CONFIRM_POLL_INTERVAL", 0)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    return oe


def test_partial_that_completes_lands_in_full(monkeypatch):
    oe = _seq_status(monkeypatch, {
        "o1": [{"status": "partially_filled", "symbol": "CSX", "filled_qty": "8"},
               {"status": "filled", "symbol": "CSX", "filled_qty": "10.26"}],
    })
    r = oe.confirm_fills(["o1"], timeout_seconds=5)
    assert [s["filled_qty"] for s in r["full"]] == ["10.26"]
    assert r["partial"] == [] and r["timeout"] == []


def test_partial_at_deadline_reported_as_partial(monkeypatch):
    oe = _seq_status(monkeypatch, {
        "o1": [{"status": "partially_filled", "symbol": "PLD", "filled_qty": "1"}],
    })
    # timeout 0 → loop never runs; last-known state is unknown → timeout bucket
    r0 = oe.confirm_fills(["o1"], timeout_seconds=0)
    assert r0["timeout"] == ["o1"]
    # a short real window: stays partially_filled → partial bucket with the
    # last-known snapshot, so the sequencer persists the shares it DID get
    import time as _t
    real = _t.monotonic
    ticks = iter([0, 0, 0.05, 0.2, 0.2])
    monkeypatch.setattr("time.time", lambda: next(ticks, 0.2))
    r = oe.confirm_fills(["o1"], timeout_seconds=0.1)
    assert [s["filled_qty"] for s in r["partial"]] == ["1"]
    assert r["timeout"] == []
    assert real  # silence unused warning


# ── 2. reconciler reads the live Alpaca book post-cutover ───────────────────

class _P:
    def __init__(self, sym, qty, cost):
        self.symbol, self.qty, self.avg_entry_price = sym, qty, cost


class _Acct:
    cash = "9000.00"


class _Client:
    def get_account(self):
        return _Acct()

    def list_positions(self):
        return [_P("AAA", "10", "100.0")]


def test_reconciler_reads_live_broker_when_not_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "portfolio.db"))
    monkeypatch.setenv("MOCK_BROKER_STATE", str(tmp_path / "absent.json"))
    from auto_trader.state.portfolio_db import (initialize_db, log_trade,
                                                upsert_position)
    initialize_db()
    log_trade({"ticker": "AAA", "action": "BUY", "shares": 10, "price": 100.0,
               "total_value": 1000.0, "cost_basis": 100.0})
    upsert_position({"ticker": "AAA", "shares": 10.0, "cost_basis": 100.0,
                     "total_cost": 1000.0, "current_price": 100.0,
                     "sector": "Tech", "entry_date": "2026-07-02",
                     "entry_score": 0.7, "last_score": 0.7,
                     "last_scored_at": None, "stop_loss_price": 90.0,
                     "target_allocation": 0.1, "status": "ACTIVE",
                     "regime_at_entry": "bull"})
    import auto_trader.monitor.reconciler as rec
    monkeypatch.setattr("auto_trader.credentials.use_mock_broker", lambda: False)
    monkeypatch.setattr("auto_trader.broker.alpaca_client.get_client",
                        lambda: _Client())
    r = rec.reconcile()
    assert any("live from Alpaca" in n for n in r["notes"])
    assert r["ok"], r["discrepancies"]                    # 9000 = 10000 − 1000
    # and a tampered live book is caught
    monkeypatch.setattr(_Acct, "cash", "9500.00")
    r2 = rec.reconcile()
    assert not r2["ok"]
    assert any(d["field"] == "cash(broker vs ledger)" for d in r2["discrepancies"])
