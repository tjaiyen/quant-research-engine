"""Phase K — execution-layer invariants."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_setup(monkeypatch):
    monkeypatch.setenv("ALPACA_USE_MOCK", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("TRADER_DB_PATH", str(Path(tmp) / "test.db"))
        monkeypatch.setattr(
            "auto_trader.credentials.HALT_FLAG_PATH",
            Path(tmp) / ".halt",
        )
        from auto_trader.broker import alpaca_client
        alpaca_client.reset_client()
        from auto_trader.state.portfolio_db import initialize_db
        initialize_db()
        yield
        alpaca_client.reset_client()


def _instr(ticker, action, amount, **kw):
    from auto_trader.allocator.delta_engine import TradeInstruction

    return TradeInstruction(
        ticker=ticker,
        action=action,
        amount_usd=amount,
        trigger_reason=kw.get("trigger", "NEW_BUY" if action == "BUY" else "REBALANCE_SELL"),
        sector=kw.get("sector", "Tech"),
        score=kw.get("score", 0.7),
        shares=kw.get("shares"),
    )


# ---------------------------------------------------------------------------
# order_builder
# ---------------------------------------------------------------------------
def test_order_builder_sells_use_day_tif_with_qty():
    from auto_trader.execution.order_builder import to_submit_kwargs

    instr = _instr("AAPL", "SELL", 1500.0, shares=10.0)
    kwargs = to_submit_kwargs(instr)
    assert kwargs["side"] == "sell"
    assert kwargs["time_in_force"] == "day"
    assert kwargs["shares"] == 10.0
    assert "notional" not in kwargs


def test_order_builder_buys_use_opg_tif_with_notional():
    from auto_trader.execution.order_builder import to_submit_kwargs

    instr = _instr("AAPL", "BUY", 100.0)
    kwargs = to_submit_kwargs(instr)
    assert kwargs["side"] == "buy"
    assert kwargs["time_in_force"] == "opg"
    assert kwargs["notional"] == 100.0
    assert "shares" not in kwargs


# ---------------------------------------------------------------------------
# order_scheduler
# ---------------------------------------------------------------------------
def test_wait_for_moo_window_returns_immediately_when_open():
    from auto_trader.execution.order_scheduler import wait_for_moo_window

    with patch(
        "auto_trader.execution.order_scheduler.is_moo_submission_window",
        return_value=True,
    ):
        assert wait_for_moo_window(timeout_seconds=10) is True


def test_wait_for_moo_window_times_out():
    """When the window is never open, should return False after timeout."""
    from auto_trader.execution.order_scheduler import wait_for_moo_window

    with patch(
        "auto_trader.execution.order_scheduler.is_moo_submission_window",
        return_value=False,
    ):
        # Use a tight timeout so the test is fast
        assert wait_for_moo_window(timeout_seconds=1, poll_interval=0) is False


# ---------------------------------------------------------------------------
# order_sequencer end-to-end on the mock broker
# ---------------------------------------------------------------------------
def test_execute_sequence_sells_then_buys_persisted(monkeypatch):
    import mock_broker
    # Exact-fill accounting under test; slippage covered in tests/test_tier0.py.
    monkeypatch.setattr(mock_broker, "SLIPPAGE_BPS", 0.0)
    from auto_trader.execution.order_sequencer import execute_sequence
    from auto_trader.state.portfolio_db import (
        get_position,
        get_trade_history,
        upsert_position,
    )
    from auto_trader.utils import now_iso, today_iso

    # Seed a position to sell
    upsert_position({
        "ticker": "AAA", "shares": 5.0, "cost_basis": 90.0, "total_cost": 450.0,
        "current_price": 100.0, "sector": "Tech", "entry_date": today_iso(),
        "entry_score": 0.5, "last_score": 0.5, "last_scored_at": now_iso(),
        "stop_loss_price": 80.0, "target_allocation": 200.0,
        "status": "ACTIVE", "regime_at_entry": "bull",
    })

    instructions = [
        _instr("AAA", "SELL", 500.0, shares=5.0, trigger="REBALANCE_SELL"),
        _instr("BBB", "BUY", 200.0, score=0.85, trigger="NEW_BUY"),
    ]

    summary = execute_sequence(instructions, current_positions=[], regime="bull")

    assert summary["n_sells_submitted"] == 1
    assert summary["n_sells_filled"] == 1
    assert summary["n_buys_submitted"] == 1
    assert summary["n_buys_filled"] == 1

    # Sold position closed
    aaa = get_position("AAA")
    assert aaa is not None
    assert aaa["status"] == "CLOSED"

    # Bought position created
    bbb = get_position("BBB")
    assert bbb is not None
    assert bbb["status"] == "ACTIVE"
    assert bbb["shares"] > 0
    # MockOrder fills at MOCK_PRICE=100, $200 notional → 2 shares
    assert abs(bbb["shares"] - 2.0) < 1e-6

    # trade_history rows landed
    trades = get_trade_history()
    actions = sorted(t["action"] for t in trades)
    assert actions == ["BUY", "SELL"]


def test_execute_sequence_logs_cost_basis_for_sells(monkeypatch):
    """SELL row in trade_history should carry cost_basis from positions table."""
    import mock_broker
    # Exact-fill accounting under test; slippage covered in tests/test_tier0.py.
    monkeypatch.setattr(mock_broker, "SLIPPAGE_BPS", 0.0)
    from auto_trader.execution.order_sequencer import execute_sequence
    from auto_trader.state.portfolio_db import (
        compute_realized_pnl_ytd,
        get_trade_history,
        upsert_position,
    )
    from auto_trader.utils import now_iso, today_iso

    upsert_position({
        "ticker": "AAA", "shares": 10.0, "cost_basis": 80.0, "total_cost": 800.0,
        "current_price": 100.0, "sector": "Tech", "entry_date": today_iso(),
        "entry_score": 0.5, "last_score": 0.5, "last_scored_at": now_iso(),
        "stop_loss_price": 70.0, "target_allocation": 1000.0,
        "status": "ACTIVE", "regime_at_entry": "bull",
    })

    instructions = [
        _instr("AAA", "SELL", 1000.0, shares=10.0, trigger="REBALANCE_SELL"),
    ]
    summary = execute_sequence(instructions, current_positions=[], regime="sideways")
    assert summary["n_sells_filled"] == 1

    trades = get_trade_history(ticker="AAA")
    sell = next(t for t in trades if t["action"] == "SELL")
    assert sell["cost_basis"] == 80.0
    # P&L = (100 - 80) * 10 = $200
    pnl = compute_realized_pnl_ytd()
    assert abs(pnl - 200.0) < 0.01
