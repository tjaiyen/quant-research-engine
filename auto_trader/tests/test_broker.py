"""Phase K — broker-layer invariants (Gates 4, 14, 16, plus order_executor checks)."""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Mock-broker isolation: every test resets the alpaca_client singleton
# and forces ALPACA_USE_MOCK=true.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    monkeypatch.setenv("ALPACA_USE_MOCK", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    from auto_trader.broker import alpaca_client
    alpaca_client.reset_client()
    yield
    alpaca_client.reset_client()


# ---------------------------------------------------------------------------
# GATE 14 — mock broker WACC accumulation + partial-sell preserve remainder
# ---------------------------------------------------------------------------
def test_gate14_mock_broker_position_accumulation():
    from mock_broker import MockAlpacaClient

    client = MockAlpacaClient(cash=10_000)

    # Buy 10 → 10 shares
    client.submit_order("AAPL", "buy", qty=10)
    assert client._positions["AAPL"]["qty"] == 10.0

    # Buy 5 more → 15 shares (WACC same since same MOCK_PRICE)
    client.submit_order("AAPL", "buy", qty=5)
    assert client._positions["AAPL"]["qty"] == 15.0

    # Sell 7 — partial sell, 8 remain
    client.submit_order("AAPL", "sell", qty=7)
    assert client._positions["AAPL"]["qty"] == 8.0

    # Sell remaining 8 — position deleted
    client.submit_order("AAPL", "sell", qty=8)
    assert "AAPL" not in client._positions


# ---------------------------------------------------------------------------
# GATE 4 — Alpaca paper connection (mocked)
# ---------------------------------------------------------------------------
def test_gate4_alpaca_paper_connection_mocked():
    from auto_trader.broker.alpaca_client import get_account_info

    acct = get_account_info()
    assert acct["status"] == "ACTIVE"
    assert acct["cash"] >= 0
    assert "buying_power" in acct
    assert "trading_blocked" in acct


# ---------------------------------------------------------------------------
# GATE 16 — MOO window boundaries (H7) honor config constants
# ---------------------------------------------------------------------------
def test_gate16_moo_window_boundaries():
    import pytz
    from datetime import datetime

    from auto_trader.broker.market_calendar import is_moo_submission_window
    from auto_trader.config import MOO_SUBMIT_MINUTE_END, MOO_SUBMIT_MINUTE_START

    et = pytz.timezone("America/New_York")

    with patch("auto_trader.broker.market_calendar.get_et_time") as m:
        # Before window
        m.return_value = et.localize(
            datetime(2026, 1, 5, 9, MOO_SUBMIT_MINUTE_START - 1)
        )
        assert not is_moo_submission_window(), "before window"

        # At window start
        m.return_value = et.localize(datetime(2026, 1, 5, 9, MOO_SUBMIT_MINUTE_START))
        assert is_moo_submission_window(), "at start"

        # At window end
        m.return_value = et.localize(datetime(2026, 1, 5, 9, MOO_SUBMIT_MINUTE_END))
        assert is_moo_submission_window(), "at end"

        # After window
        m.return_value = et.localize(datetime(2026, 1, 5, 9, MOO_SUBMIT_MINUTE_END + 1))
        assert not is_moo_submission_window(), "after window"


# ---------------------------------------------------------------------------
# GATE 12 — monthly cycle date resolves to next trading day
# ---------------------------------------------------------------------------
def test_gate12_monthly_cycle_date_walks_forward():
    from auto_trader.broker.market_calendar import get_monthly_cycle_date

    # Mock today() so the test is independent of when it runs.
    # The code only calls is_trading_day() through a path that itself
    # checks Alpaca's calendar; we patch is_trading_day directly here.
    import datetime as dt
    from unittest.mock import patch

    fake_today = dt.date(2026, 1, 1)  # Jan 1
    with patch("auto_trader.broker.market_calendar.date") as md:
        md.today.return_value = fake_today
        md.side_effect = lambda *a, **k: dt.date(*a, **k)
        with patch("auto_trader.broker.market_calendar.is_trading_day") as it:
            # Jan 1 is closed; Jan 2 (Friday) trades.
            it.side_effect = lambda d=None: (d.weekday() < 5 and d.day != 1)
            result = get_monthly_cycle_date(target_day=1, window=3)
            assert result == dt.date(2026, 1, 2)


# ---------------------------------------------------------------------------
# H6 — order_executor mutual exclusion (notional vs shares)
# ---------------------------------------------------------------------------
def test_h6_submit_order_rejects_both_notional_and_shares():
    from auto_trader.broker.order_executor import submit_order

    with pytest.raises(ValueError, match="Cannot specify both"):
        submit_order(
            "AAPL", "buy",
            notional=100, shares=1,
            time_in_force="day",
        )


def test_h6_submit_order_requires_one_of_notional_or_shares():
    from auto_trader.broker.order_executor import submit_order

    with pytest.raises(ValueError, match="Must specify"):
        submit_order("AAPL", "buy", time_in_force="day")


def test_h6_submit_order_requires_explicit_tif():
    from auto_trader.broker.order_executor import submit_order

    with pytest.raises(ValueError, match="time_in_force must be explicit"):
        submit_order("AAPL", "buy", notional=100)


# ---------------------------------------------------------------------------
# Halt flag blocks orders
# ---------------------------------------------------------------------------
def test_halt_flag_blocks_orders(tmp_path, monkeypatch):
    """When the halt flag is set, submit_order returns None (rejected)."""
    monkeypatch.setattr(
        "auto_trader.credentials.HALT_FLAG_PATH",
        tmp_path / ".halt",
    )
    from auto_trader.credentials import clear_halt, set_halt

    set_halt("test")
    try:
        from auto_trader.broker.order_executor import submit_order

        # Patch the halt check inside order_executor too
        with patch("auto_trader.broker.order_executor.is_halted", return_value=True):
            result = submit_order(
                "AAPL", "buy",
                notional=100, time_in_force="day",
            )
            assert result is None
    finally:
        clear_halt()


# ---------------------------------------------------------------------------
# July-1 ledger bug — the ORDER must report the same real fill the position
# book used (executor reads filled_qty/filled_avg_price into trade_history +
# the positions DB; a divergent $100 placeholder corrupts the ledger).
# ---------------------------------------------------------------------------
def test_mtm_order_reports_real_fill_price():
    import mock_broker
    from mock_broker import MockAlpacaClient

    client = MockAlpacaClient(cash=10_000, mark_to_market=True)
    with patch.object(mock_broker, "_market_price", return_value=250.0):
        # Notional buy: shares AND price on the order must use the real fill.
        o = client.submit_order("XYZ", "buy", notional=1_000.0)
        assert float(o.filled_avg_price) == 250.0
        assert float(o.filled_qty) == pytest.approx(4.0)
        assert client._positions["XYZ"]["qty"] == pytest.approx(4.0)
        assert client._positions["XYZ"]["cost"] == 250.0
        # Sell: order price matches the cash actually credited.
        o2 = client.submit_order("XYZ", "sell", qty=4.0)
        assert float(o2.filled_avg_price) == 250.0
        assert client._cash == pytest.approx(10_000.0)


def test_non_mtm_order_keeps_flat_mock_price():
    # Unit-test path (no mark_to_market) stays at the flat $100 placeholder.
    from mock_broker import MOCK_PRICE, MockAlpacaClient

    client = MockAlpacaClient(cash=10_000)
    o = client.submit_order("AAPL", "buy", notional=500.0)
    assert float(o.filled_avg_price) == MOCK_PRICE
    assert float(o.filled_qty) == pytest.approx(5.0)


def test_full_sell_sweeps_float_dust_husk():
    # A full sell computed at slightly different precision must not leave a
    # 3e-07-share husk position behind (July-1 GD/KMI residue).
    from mock_broker import MockAlpacaClient

    client = MockAlpacaClient(cash=10_000)
    client.submit_order("GD", "buy", qty=1.1721743398827156)
    client.submit_order("GD", "sell", qty=1.1721743398827153)  # dust short
    assert "GD" not in client._positions
