"""Phase K — risk-layer invariants."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    """Each test gets a fresh DB so guard 1 doesn't see stale snapshots."""
    monkeypatch.setenv("ALPACA_USE_MOCK", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("TRADER_DB_PATH", str(Path(tmp) / "test.db"))
        # Reset Alpaca singleton + halt flag
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


@pytest.fixture()
def patch_account(monkeypatch):
    """Allow tests to set the buying_power Guard 3 sees.

    Guard 3 imports ``get_account_info`` *inside* the function body via
    ``from auto_trader.broker.alpaca_client import get_account_info``, so
    we patch the source module — not the consumer.
    """
    def _patch(buying_power: float, cash: float | None = None):
        monkeypatch.setattr(
            "auto_trader.broker.alpaca_client.get_account_info",
            lambda: {
                "cash": cash if cash is not None else buying_power,
                "buying_power": buying_power,
                "portfolio_value": buying_power,
                "status": "ACTIVE",
                "pattern_day_trader": False,
                "trading_blocked": False,
            },
            raising=False,
        )
    return _patch


def _instr(ticker, action, amount, trigger="NEW_BUY", score=0.7, sector="Tech"):
    from auto_trader.allocator.delta_engine import TradeInstruction

    return TradeInstruction(
        ticker=ticker, action=action, amount_usd=amount,
        trigger_reason=trigger, sector=sector, score=score,
    )


# ---------------------------------------------------------------------------
# Drawdown circuit
# ---------------------------------------------------------------------------
def test_drawdown_circuit_no_history():
    from auto_trader.risk.drawdown_circuit import current_drawdown_pct, is_halted

    assert is_halted(10_000.0) is False
    assert current_drawdown_pct(10_000.0) == 0.0


def test_drawdown_circuit_trips_at_threshold():
    from auto_trader.risk.drawdown_circuit import is_halted
    from auto_trader.state.portfolio_db import log_portfolio_snapshot

    log_portfolio_snapshot({
        "total_value": 10_000.0, "cash": 1000.0, "invested_value": 9000.0,
        "unrealized_pnl": 0, "realized_pnl_ytd": 0, "n_positions": 0,
        "regime": "bull", "benchmark_value": None, "drawdown_from_peak": 0.0,
    })
    # 16% drawdown should trip (threshold 15%)
    assert is_halted(8_400.0) is True
    # 5% drawdown should not trip
    assert is_halted(9_500.0) is False


# ---------------------------------------------------------------------------
# Guard 1 — halt flag blocks all buys, lets sells through
# ---------------------------------------------------------------------------
def test_guard1_halt_blocks_buys_keeps_sells():
    from auto_trader.credentials import clear_halt, set_halt
    from auto_trader.risk.exposure_guard import run_all_guards

    set_halt("test")
    try:
        instr = [
            _instr("BUY1", "BUY", 100),
            _instr("SELL1", "SELL", 100, trigger="REBALANCE_SELL"),
        ]
        regime = {"regime": "bull", "confidence": 0.9}
        # cash + portfolio_value high so no other guards trip
        out = run_all_guards(instr, [], 100_000.0, 50_000.0, regime)
        assert all(i.action == "SELL" for i in out)
    finally:
        clear_halt()


# ---------------------------------------------------------------------------
# Guard 2 — bear regime blocks NEW_BUY but allows REBALANCE_BUY
# ---------------------------------------------------------------------------
def test_guard2_bear_regime_blocks_new_buys_only(patch_account):
    from auto_trader.risk.exposure_guard import run_all_guards

    patch_account(buying_power=50_000.0)
    instr = [
        _instr("NEW", "BUY", 100, trigger="NEW_BUY", score=0.7),
        _instr("REB", "BUY", 50, trigger="REBALANCE_BUY", score=0.7),
    ]
    regime = {"regime": "bear", "confidence": 0.85}  # > 0.70 threshold
    out = run_all_guards(instr, [], 100_000.0, 10_000.0, regime)
    actions = [(i.ticker, i.trigger_reason) for i in out]
    assert ("NEW", "NEW_BUY") not in actions
    assert ("REB", "REBALANCE_BUY") in actions


def test_guard2_bear_low_confidence_passes(patch_account):
    """Bear regime with low confidence shouldn't trip the guard."""
    from auto_trader.risk.exposure_guard import run_all_guards

    patch_account(buying_power=50_000.0)
    instr = [_instr("NEW", "BUY", 100, trigger="NEW_BUY", score=0.7)]
    regime = {"regime": "bear", "confidence": 0.50}  # below 0.70 threshold
    out = run_all_guards(instr, [], 100_000.0, 10_000.0, regime)
    assert any(i.ticker == "NEW" for i in out)


# ---------------------------------------------------------------------------
# Guard 4 — single-position cap clips amount_usd
# ---------------------------------------------------------------------------
def test_guard4_single_position_cap(patch_account):
    from auto_trader.config import MAX_SINGLE_STOCK_PCT
    from auto_trader.risk.exposure_guard import run_all_guards

    portfolio_value = 100_000.0
    expected_cap = portfolio_value * MAX_SINGLE_STOCK_PCT  # $6000
    patch_account(buying_power=portfolio_value)

    instr = [_instr("BIG", "BUY", 50_000.0, score=0.7)]
    regime = {"regime": "bull", "confidence": 0.9}
    out = run_all_guards(instr, [], portfolio_value, 60_000.0, regime)
    big = next(i for i in out if i.ticker == "BIG")
    assert big.amount_usd <= expected_cap


# ---------------------------------------------------------------------------
# Guard 5 — sector exposure cap drops over-budget buys
# ---------------------------------------------------------------------------
def test_guard5_sector_exposure_drops_overbudget(patch_account):
    from auto_trader.config import MAX_SECTOR_PCT
    from auto_trader.risk.exposure_guard import run_all_guards

    portfolio_value = 10_000.0
    patch_account(buying_power=portfolio_value)
    # Existing position uses $1500 of Tech sector exposure (10 × 150 = $1500)
    current = [
        {
            "ticker": "EXIST", "shares": 10, "current_price": 150,
            "cost_basis": 100, "sector": "Tech",
        }
    ]
    instr = [
        _instr("A", "BUY", 800, score=0.9, sector="Tech"),  # would exceed cap
    ]
    regime = {"regime": "bull", "confidence": 0.9}
    out = run_all_guards(instr, current, portfolio_value, 5000.0, regime)
    # The BUY should be dropped because 1500 + 800 > 2000 cap
    assert all(i.ticker != "A" for i in out)


# ---------------------------------------------------------------------------
# Guard 6 — max-order-size clip
# ---------------------------------------------------------------------------
def test_guard6_max_order_size_clip(patch_account):
    from auto_trader.config import MAX_ORDER_SIZE_USD
    from auto_trader.risk.exposure_guard import run_all_guards

    patch_account(buying_power=1_000_000.0)
    instr = [_instr("BIG", "BUY", 9999.0, score=0.7)]
    regime = {"regime": "bull", "confidence": 0.9}
    out = run_all_guards(instr, [], 1_000_000.0, 1_000_000.0, regime)
    big = next(i for i in out if i.ticker == "BIG")
    assert big.amount_usd <= MAX_ORDER_SIZE_USD


# ---------------------------------------------------------------------------
# Guard 7 — minimum composite score
# ---------------------------------------------------------------------------
def test_guard7_min_score_drops_low_buys(patch_account):
    from auto_trader.risk.exposure_guard import run_all_guards

    patch_account(buying_power=50_000.0)
    instr = [
        _instr("HIGH", "BUY", 100, score=0.80),
        _instr("LOW",  "BUY", 100, score=0.30),  # below MIN_COMPOSITE_TO_BUY
    ]
    regime = {"regime": "bull", "confidence": 0.9}
    out = run_all_guards(instr, [], 100_000.0, 10_000.0, regime)
    tickers = {i.ticker for i in out}
    assert "HIGH" in tickers
    assert "LOW" not in tickers


# ---------------------------------------------------------------------------
# stop_loss_monitor
# ---------------------------------------------------------------------------
def test_stop_loss_monitor_triggers_when_price_below_stop():
    from auto_trader.risk.stop_loss_monitor import scan_stop_losses
    from auto_trader.state.portfolio_db import upsert_position
    from auto_trader.utils import now_iso, today_iso

    upsert_position({
        "ticker": "AAPL", "shares": 10, "cost_basis": 150, "total_cost": 1500,
        "current_price": 130, "sector": "Tech", "entry_date": today_iso(),
        "entry_score": 0.8, "last_score": 0.8, "last_scored_at": now_iso(),
        "stop_loss_price": 132,  # > current price 130 → should trigger
        "target_allocation": 200, "status": "ACTIVE", "regime_at_entry": "bull",
    })
    hits = scan_stop_losses({"AAPL": 130.0})
    assert len(hits) == 1
    assert hits[0]["ticker"] == "AAPL"


def test_stop_loss_monitor_quiet_when_above_stop():
    from auto_trader.risk.stop_loss_monitor import scan_stop_losses
    from auto_trader.state.portfolio_db import upsert_position
    from auto_trader.utils import now_iso, today_iso

    upsert_position({
        "ticker": "AAPL", "shares": 10, "cost_basis": 150, "total_cost": 1500,
        "current_price": 145, "sector": "Tech", "entry_date": today_iso(),
        "entry_score": 0.8, "last_score": 0.8, "last_scored_at": now_iso(),
        "stop_loss_price": 132,
        "target_allocation": 200, "status": "ACTIVE", "regime_at_entry": "bull",
    })
    hits = scan_stop_losses({"AAPL": 145.0})
    assert hits == []


# ---------------------------------------------------------------------------
# risk_report
# ---------------------------------------------------------------------------
def test_risk_snapshot_shape():
    from auto_trader.risk.risk_report import generate_risk_snapshot

    s = generate_risk_snapshot(10_000.0, 1000.0)
    for k in (
        "portfolio_value", "cash", "cash_pct", "invested_value", "n_positions",
        "peak_value", "drawdown_pct", "halt_flag", "circuit_breaker",
        "largest_position_ticker", "largest_position_pct", "limits",
    ):
        assert k in s, f"missing risk_snapshot key: {k}"
