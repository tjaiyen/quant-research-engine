"""Tests for U11 news-sentiment overlay (pure veto, graceful scoring, wiring)."""
from __future__ import annotations

import pandas as pd

from screener.sentiment import scorer
from render import notes


# ── pure veto ────────────────────────────────────────────────────────────────

def test_sentiment_veto_thresholds():
    assert scorer.sentiment_veto(None, -0.6) == (True, None)        # unknown → pass
    assert scorer.sentiment_veto(-0.9, -0.6) == (False, "SENTIMENT_VETO")
    assert scorer.sentiment_veto(-0.6, -0.6) == (False, "SENTIMENT_VETO")  # boundary
    assert scorer.sentiment_veto(-0.5, -0.6) == (True, None)
    assert scorer.sentiment_veto(0.4, -0.6) == (True, None)


# ── graceful scoring ─────────────────────────────────────────────────────────

def test_score_unavailable_when_no_finbert(monkeypatch):
    monkeypatch.setattr(scorer, "_finbert", lambda: None)
    out = scorer.score_ticker_news("AAPL")
    assert out["label"] == "UNAVAILABLE" and out["sentiment_score"] is None


def test_score_neutral_when_no_news(monkeypatch):
    monkeypatch.setattr(scorer, "_finbert", lambda: (lambda x: []))
    monkeypatch.setattr(scorer, "_recent_titles", lambda *a, **k: [])
    out = scorer.score_ticker_news("AAPL")
    assert out["label"] == "NEUTRAL" and out["n_headlines"] == 0


def test_score_aggregates_negative(monkeypatch):
    fake_pipe = lambda titles: [{"label": "negative", "score": 0.9} for _ in titles]
    monkeypatch.setattr(scorer, "_finbert", lambda: fake_pipe)
    monkeypatch.setattr(scorer, "_recent_titles",
                        lambda *a, **k: ["Co misses earnings badly", "Layoffs announced"])
    out = scorer.score_ticker_news("XYZ")
    assert out["label"] == "NEGATIVE"
    assert out["sentiment_score"] < 0 and out["n_headlines"] == 2


# ── note render ──────────────────────────────────────────────────────────────

def test_sentiment_note_renders():
    data = {"as_of": "x", "veto_enabled": False, "threshold": -0.6,
            "rows": [
                {"ticker": "AAA", "sentiment_score": -0.8, "label": "NEGATIVE",
                 "n_headlines": 5, "confidence": 0.9},
                {"ticker": "BBB", "sentiment_score": 0.5, "label": "POSITIVE",
                 "n_headlines": 3, "confidence": 0.8},
            ]}
    md = notes.sentiment_note(data)
    assert "type: tracker-sentiment" in md
    assert "OFF (opt-in)" in md
    assert "AAA" in md


def test_sentiment_note_empty():
    md = notes.sentiment_note({"rows": [], "as_of": "x"})
    assert "No sentiment cached yet" in md


# ── relaxation skips a sentiment-vetoed name (industry_ranker wiring) ─────────

def test_relaxation_skips_sentiment_veto(monkeypatch):
    from screener.engine import industry_ranker as ir
    df = pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1},
        index=pd.date_range("2025-01-01", periods=260, freq="D"))
    monkeypatch.setattr(ir, "_gather_price_histories", lambda ts: ({t: df for t in ts}, []))
    monkeypatch.setattr(ir, "_is_tradeable", lambda t, *a, **k: True)
    monkeypatch.setattr(ir, "_next_earnings", lambda t: None)
    monkeypatch.setattr(ir, "_cached_sentiment", lambda t: None)

    def fake_score(ticker, regime_data, ph, next_earnings=None, cached_sentiment=None):
        return {"ticker": ticker, "composite_score": 0.6, "passed_veto": False,
                "earnings_veto": False, "sentiment_veto": ticker == "BADNEWS",
                "veto_detail": {"garch_vol": 0.01, "mc_loss_prob": 0.10}}
    monkeypatch.setattr(ir, "score_stock", fake_score)

    result = ir.rank_industry("Technology", ["BADNEWS", "OKAY"], {"regime": "bear"})
    passed = [p["ticker"] for p in result["passed"]]
    assert "BADNEWS" not in passed     # sentiment veto never relaxed
    assert "OKAY" in passed
