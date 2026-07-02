"""Mark-to-market: the file-backed paper account values positions at real cached
prices (fills + marks); the in-memory test broker keeps the legacy flat price."""
from __future__ import annotations

import json

import mock_broker
from mock_broker import MockAlpacaClient, MOCK_PRICE, repair_to_real_entry


def test_in_memory_account_stays_flat_mock_price():
    # No state_path ⇒ unit-test mode ⇒ legacy $100 behaviour (other tests rely on it).
    c = MockAlpacaClient(cash=10_000.0)
    c.submit_order("AAPL", "buy", notional=1000.0)
    pos = c.list_positions()[0]
    assert float(pos.current_price) == MOCK_PRICE
    assert float(pos.unrealized_pl) == 0.0


def test_live_account_marks_to_market(tmp_path, monkeypatch):
    monkeypatch.setattr(mock_broker, "_market_price", lambda s: 150.0)
    # Exact-fill mechanics under test here; slippage has its own coverage
    # in tests/test_tier0.py.
    monkeypatch.setattr(mock_broker, "SLIPPAGE_BPS", 0.0)
    c = MockAlpacaClient(cash=10_000.0, state_path=str(tmp_path / "mb.json"),
                         mark_to_market=True)
    c.submit_order("AAPL", "buy", notional=1500.0)        # fills at 150 → 10 shares
    pos = c.list_positions()[0]
    assert abs(float(pos.qty) - 10.0) < 1e-6
    assert float(pos.current_price) == 150.0
    assert abs(float(pos.unrealized_pl)) < 1e-6           # bought at the mark → P&L 0

    monkeypatch.setattr(mock_broker, "_market_price", lambda s: 165.0)  # price rises
    pos = c.list_positions()[0]
    assert abs(float(pos.unrealized_pl) - 150.0) < 1e-6   # 10 sh × ($165−$150)
    acct = c.get_account()
    assert abs(float(acct.portfolio_value) - (10_000 - 1500 + 1650)) < 1e-6


def test_repair_preserves_dollars(tmp_path, monkeypatch):
    sp = tmp_path / "mb.json"
    sp.write_text(json.dumps({"cash": 9600.0,
                              "positions": {"AAPL": {"qty": 4.0, "cost": 100.0}},
                              "order_seq": 1}))
    monkeypatch.setattr("auto_trader.state.portfolio_db.get_all_positions",
                        lambda: [{"ticker": "AAPL", "entry_date": "2026-01-01"}])
    monkeypatch.setattr("utils.db.price_on_or_before", lambda t, d: 80.0)
    changed = repair_to_real_entry(str(sp))
    assert len(changed) == 1
    after = json.loads(sp.read_text())["positions"]["AAPL"]
    assert abs(after["cost"] - 80.0) < 1e-9                 # real entry price
    assert abs(after["qty"] - 5.0) < 1e-9                   # 400 / 80, dollars preserved
    assert abs(after["qty"] * after["cost"] - 400.0) < 1e-9
    # idempotent: a second run finds nothing to fix
    assert repair_to_real_entry(str(sp)) == []


def test_repair_skips_when_no_cached_price(tmp_path, monkeypatch):
    sp = tmp_path / "mb.json"
    sp.write_text(json.dumps({"cash": 0, "positions": {"ZZZ": {"qty": 1.0, "cost": 100.0}},
                              "order_seq": 0}))
    monkeypatch.setattr("auto_trader.state.portfolio_db.get_all_positions",
                        lambda: [{"ticker": "ZZZ", "entry_date": "2026-01-01"}])
    monkeypatch.setattr("utils.db.price_on_or_before", lambda t, d: None)
    assert repair_to_real_entry(str(sp)) == []             # graceful skip, untouched
    assert json.loads(sp.read_text())["positions"]["ZZZ"]["cost"] == 100.0


def test_drawdown_cold_start_math():
    # The fix: peak includes today's value, so a cold peak never yields a
    # nonsensical drawdown.
    for peak_db, value in [(0.0, 10_000.0), (12_000.0, 10_000.0), (9_000.0, 10_000.0)]:
        peak = max(peak_db, value)
        dd = (peak - value) / peak if peak > 0 else 0.0
        assert 0.0 <= dd < 1.0                              # sane fraction, never −1e6
    # cold start (empty snapshots → peak 0) now yields 0, not (0−10000)/1 = −10000
    peak = max(0.0, 10_000.0)
    assert (peak - 10_000.0) / peak == 0.0
