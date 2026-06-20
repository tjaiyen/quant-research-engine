"""Tests for the typed Signal contract (U1, surgical boundary)."""
from __future__ import annotations

import pytest

from screener.signal import Signal
from auto_trader.compat.screener_compat import normalize_screener_cache


# ── Signal.from_row coercion + defaults ──────────────────────────────────────

def test_from_row_coerces_string_score():
    s = Signal.from_row({"ticker": "AAPL", "composite_score": "0.73",
                         "passed_veto": 1, "signal_scores": {"arima": 0.6}})
    assert s.composite_score == pytest.approx(0.73)   # str → float
    assert isinstance(s.composite_score, float)
    assert s.passed_veto is True                       # 1 → bool
    assert s.ticker == "AAPL"


def test_from_row_bad_score_defaults_zero():
    s = Signal.from_row({"ticker": "X", "composite_score": "not-a-number",
                         "passed_veto": True, "signal_scores": {}})
    assert s.composite_score == 0.0                    # uncoercible → default, logged


def test_from_row_missing_fields_default():
    s = Signal.from_row({"ticker": "Y"})
    assert s.composite_score == 0.0
    assert s.passed_veto is False
    assert s.signal_scores == {}
    assert s.sector == "UNKNOWN"


def test_from_row_signal_scores_not_dict():
    s = Signal.from_row({"ticker": "Z", "signal_scores": "oops"})
    assert s.signal_scores == {}                       # non-dict → {}


def test_from_row_sector_backfill():
    s = Signal.from_row({"ticker": "T"}, sector="Technology")
    assert s.sector == "Technology"
    # explicit row sector wins over the arg
    s2 = Signal.from_row({"ticker": "T", "sector": "Energy"}, sector="Technology")
    assert s2.sector == "Energy"


def test_from_row_rejects_non_dict():
    with pytest.raises(TypeError):
        Signal.from_row(["not", "a", "dict"])


def test_canonical_fields():
    s = Signal.from_row({"ticker": "AAPL", "composite_score": 0.7,
                         "passed_veto": True, "signal_scores": {"a": 1.0}})
    c = s.canonical()
    assert set(c) == {"ticker", "composite_score", "passed_veto", "signal_scores", "sector"}


# ── boundary integration: normalize coerces + preserves extras ───────────────

def test_normalize_coerces_and_preserves_extras():
    cache = {
        "regime": {"label": "bull", "confidence": 0.9},
        "sectors": {
            "Technology": [
                {"ticker": "AAPL", "composite_score": "0.72",   # string!
                 "passed_veto": 1, "signal_scores": {"arima": 0.6},
                 "veto_detail": {"garch_vol": 0.02},            # extra key
                 "composite_extra": "keep me"},
            ]
        },
        "summary": {}, "generated_at": "2026-06-19T00:00:00Z",
    }
    out = normalize_screener_cache(cache)
    stock = out["sectors"]["Technology"][0]
    assert isinstance(stock["composite_score"], float)   # coerced str → float
    assert stock["composite_score"] == pytest.approx(0.72)
    assert stock["passed_veto"] is True
    assert stock["sector"] == "Technology"               # backfilled
    # Extra keys survive the typed normalization.
    assert stock["veto_detail"] == {"garch_vol": 0.02}
    assert stock["composite_extra"] == "keep me"
