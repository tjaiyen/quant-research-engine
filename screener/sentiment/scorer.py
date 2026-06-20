"""News fetch + FinBERT scoring + the opt-in sentiment veto.

Everything degrades gracefully: if `transformers` is absent, the model can't
download, or a ticker has no news, scoring returns label "UNAVAILABLE"/"NEUTRAL"
and the veto fails open (never blocks a pick on missing data).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

VETO_REASON = "SENTIMENT_VETO"
_FINBERT = None          # cached pipeline (or False once load fails)
_MODEL = "ProsusAI/finbert"


def _finbert():
    """Lazy-load the FinBERT pipeline. Returns the pipeline or None (graceful)."""
    global _FINBERT
    if _FINBERT is not None:
        return _FINBERT or None
    try:
        from transformers import pipeline  # heavy; optional
        _FINBERT = pipeline("sentiment-analysis", model=_MODEL, truncation=True)
        logger.info("FinBERT loaded (%s)", _MODEL)
    except Exception as exc:               # transformers absent / download failed
        logger.warning("FinBERT unavailable (%s) — sentiment disabled", exc)
        _FINBERT = False
    return _FINBERT or None


def _headline_ts(item: dict) -> datetime | None:
    """Best-effort publish timestamp from a yfinance news item (UTC)."""
    c = item.get("content") if isinstance(item.get("content"), dict) else item
    pub = c.get("pubDate") or c.get("displayTime") or item.get("providerPublishTime")
    if pub is None:
        return None
    try:
        if isinstance(pub, (int, float)):          # epoch seconds (older yfinance)
            return datetime.fromtimestamp(pub, tz=timezone.utc)
        return datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
    except Exception:
        return None


def _headline_title(item: dict) -> str | None:
    c = item.get("content") if isinstance(item.get("content"), dict) else item
    t = c.get("title") or item.get("title")
    return str(t).strip() if t else None


def _recent_titles(ticker: str, lookback_days: int, max_articles: int) -> list[str]:
    """Recent headline titles for a ticker via yfinance .news (best-effort)."""
    try:
        import yfinance as yf
        news = yf.Ticker(ticker).news or []
    except Exception as exc:
        logger.debug("news fetch failed for %s: %s", ticker, exc)
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    titles: list[str] = []
    for item in news:
        ts = _headline_ts(item)
        if ts is not None and ts < cutoff:
            continue                                # too old
        title = _headline_title(item)
        if title:
            titles.append(title)
        if len(titles) >= max_articles:
            break
    return titles


def score_ticker_news(ticker: str, lookback_days: int = 14,
                      max_articles: int = 10) -> dict:
    """Return {sentiment_score∈[-1,1], label, n_headlines, confidence}.

    UNAVAILABLE if FinBERT can't load; NEUTRAL/0 if no recent headlines.
    """
    nlp = _finbert()
    if nlp is None:
        return {"sentiment_score": None, "label": "UNAVAILABLE",
                "n_headlines": 0, "confidence": None}
    titles = _recent_titles(ticker, lookback_days, max_articles)
    if not titles:
        return {"sentiment_score": 0.0, "label": "NEUTRAL",
                "n_headlines": 0, "confidence": None}
    try:
        results = nlp(titles)
    except Exception as exc:
        logger.warning("FinBERT scoring failed for %s: %s", ticker, exc)
        return {"sentiment_score": None, "label": "UNAVAILABLE",
                "n_headlines": 0, "confidence": None}

    signed, confs = [], []
    for r in results:
        lab = str(r.get("label", "")).lower()
        sc = float(r.get("score", 0.0))
        confs.append(sc)
        signed.append(sc if lab == "positive" else -sc if lab == "negative" else 0.0)
    score = sum(signed) / len(signed)
    label = "POSITIVE" if score > 0.15 else "NEGATIVE" if score < -0.15 else "NEUTRAL"
    return {"sentiment_score": round(score, 4), "label": label,
            "n_headlines": len(titles),
            "confidence": round(sum(confs) / len(confs), 4)}


def sentiment_veto(score: float | None, threshold: float) -> tuple[bool, str | None]:
    """(passed, reason). Vetoes only on a known, strongly-negative score (fail-open)."""
    if score is None:
        return (True, None)
    if score <= threshold:
        return (False, VETO_REASON)
    return (True, None)


__all__ = ["score_ticker_news", "sentiment_veto", "VETO_REASON"]
