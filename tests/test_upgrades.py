"""Tests for the resilience-core upgrades: U7 (earnings veto), U9 (stale skip),
U8 (Stooq fallback). Pure-logic where possible; monkeypatched where it touches
the network or DB."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest


# ── U7: earnings-blackout guard (pure) ───────────────────────────────────────

from screener.engine.earnings_guard import earnings_blackout


def test_earnings_blackout_within_window_before_and_after():
    today = date(2026, 6, 19)
    # 3 days before and 3 days after → vetoed
    assert earnings_blackout("2026-06-22", today, 5) == (False, "EARNINGS_BLACKOUT")
    assert earnings_blackout("2026-06-16", today, 5) == (False, "EARNINGS_BLACKOUT")
    # exactly on the boundary → vetoed
    assert earnings_blackout("2026-06-24", today, 5)[0] is False


def test_earnings_blackout_outside_window_passes():
    today = date(2026, 6, 19)
    assert earnings_blackout("2026-07-15", today, 5) == (True, None)
    assert earnings_blackout("2026-05-01", today, 5) == (True, None)


def test_earnings_blackout_unknown_date_fails_open():
    today = date(2026, 6, 19)
    assert earnings_blackout(None, today, 5) == (True, None)
    assert earnings_blackout("not-a-date", today, 5) == (True, None)
    assert earnings_blackout("2026-06-22T00:00:00", today, 5)[0] is False  # ISO datetime ok


# ── U7: relaxation never resurrects an earnings-vetoed name ──────────────────

def test_relaxation_skips_earnings_veto(monkeypatch):
    from screener.engine import industry_ranker as ir

    tickers = ["EARN", "OKAY"]
    df = pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1},
        index=pd.date_range("2025-01-01", periods=260, freq="D"),
    )
    monkeypatch.setattr(ir, "_gather_price_histories",
                        lambda ts: ({t: df for t in ts}, []))
    monkeypatch.setattr(ir, "_is_tradeable", lambda t, *a, **k: True)
    monkeypatch.setattr(ir, "_next_earnings", lambda t: None)

    def fake_score(ticker, regime_data, ph, next_earnings=None):
        # Both fail the initial veto; both would PASS relaxation on vol/tail,
        # but EARN carries an earnings veto and must stay out.
        return {
            "ticker": ticker,
            "composite_score": 0.6 if ticker == "EARN" else 0.5,
            "passed_veto": False,
            "earnings_veto": ticker == "EARN",
            "veto_detail": {"garch_vol": 0.01, "mc_loss_prob": 0.10},
        }

    monkeypatch.setattr(ir, "score_stock", fake_score)

    result = ir.rank_industry("Technology", tickers, {"regime": "bear"})
    passed_tickers = [p["ticker"] for p in result["passed"]]
    assert "EARN" not in passed_tickers          # earnings veto never relaxed
    assert "OKAY" in passed_tickers              # ordinary name resurrected


# ── U9: tradeable check ──────────────────────────────────────────────────────

def test_is_tradeable(monkeypatch):
    from screener.engine import industry_ranker as ir
    from datetime import datetime, timedelta

    fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stale = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    cases = {
        "OKAY": ("ok", fresh),
        "DEAD": ("error:not_found", fresh),
        "STALE": ("ok", stale),
        "UNK": (None, None),
    }
    monkeypatch.setattr("utils.db.ticker_status", lambda t: cases[t])

    assert ir._is_tradeable("OKAY", 10) is True
    assert ir._is_tradeable("DEAD", 10) is False
    assert ir._is_tradeable("STALE", 10) is False
    assert ir._is_tradeable("UNK", 10) is True   # fail-open on unknown


# ── U8: Stooq provider parse ─────────────────────────────────────────────────

_STOOQ_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-06-17,100.0,101.0,99.0,100.5,1000000\n"
    "2026-06-18,100.5,102.0,100.0,101.5,1200000\n"
    "2026-06-19,101.5,103.0,101.0,102.5,1100000\n"
)


class _Resp:
    def __init__(self, text):
        self.text = text
    def raise_for_status(self):
        return None


def test_stooq_parses_csv(monkeypatch):
    import data_providers.stooq_provider as sp

    monkeypatch.setattr(sp.requests, "get", lambda *a, **k: _Resp(_STOOQ_CSV))
    df = sp.fetch_daily_adjusted("AAPL", "full")
    assert list(df.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert (df["adj_close"] == df["close"]).all()      # documented limitation
    assert df.index.is_monotonic_increasing
    assert len(df) == 3
    assert df["close"].iloc[-1] == 102.5


def test_stooq_no_data_raises(monkeypatch):
    import data_providers.stooq_provider as sp
    from data_providers.yfinance_provider import TickerNotFound

    monkeypatch.setattr(sp.requests, "get", lambda *a, **k: _Resp("No data\n"))
    with pytest.raises(TickerNotFound):
        sp.fetch_daily_adjusted("ZZZZ")


def test_stooq_symbol_mapping():
    import data_providers.stooq_provider as sp
    assert sp._stooq_symbol("AAPL") == "aapl.us"
    assert sp._stooq_symbol("BRK.B") == "brk-b.us"


# ── U8: data_fetcher falls back to Stooq when yfinance fails ─────────────────

def test_data_fetcher_fallback_to_stooq(monkeypatch):
    import data_fetcher as dfetch
    import data_providers.stooq_provider as sp
    from data_providers.yfinance_provider import ProviderError

    sentinel = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
         "adj_close": [1.0], "volume": [1]},
        index=pd.to_datetime(["2026-06-19"]),
    )

    def boom(symbol, output_size):
        raise ProviderError("yfinance down")

    monkeypatch.setattr(dfetch, "_yf_fetch_daily_adjusted", boom)
    monkeypatch.setattr(sp, "fetch_daily_adjusted", lambda s, o="compact": sentinel)

    out = dfetch.fetch_daily_adjusted("AAPL")
    assert out is sentinel


def test_data_fetcher_no_fallback_reraises(monkeypatch):
    import data_fetcher as dfetch
    from data_providers.yfinance_provider import ProviderError

    def boom(symbol, output_size):
        raise ProviderError("yfinance down")

    monkeypatch.setattr(dfetch, "_yf_fetch_daily_adjusted", boom)
    with pytest.raises(ProviderError):
        dfetch.fetch_daily_adjusted("AAPL", fallback=False)
