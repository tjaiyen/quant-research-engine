"""Earnings history — actual vs estimate EPS + surprise, per quarter (Phase 21b).

Pulls yfinance `.get_earnings_dates()` (the FROZEN price provider is OHLCV-only),
keeps the REPORTED quarters, and derives a beat/miss verdict. Graceful: missing /
patchy data → [] (yfinance earnings data lags or is absent for some names).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_BEAT, _MISS = 1.0, -1.0   # surprise % thresholds for the verdict


def verdict(surprise_pct) -> str:
    """beat / miss / in-line from a surprise percentage (None → 'n/a')."""
    if surprise_pct is None:
        return "n/a"
    if surprise_pct > _BEAT:
        return "beat"
    if surprise_pct < _MISS:
        return "miss"
    return "in-line"


def _surprise(actual, est):
    if actual is None or est in (None, 0):
        return None
    try:
        return round((float(actual) - float(est)) / abs(float(est)) * 100.0, 2)
    except Exception:
        return None


def fetch_earnings_history(ticker: str, limit: int = 8) -> list[dict]:
    """Recent REPORTED quarters for `ticker` → list of dicts (most recent first).

    Each: {report_date, eps_estimate, eps_actual, surprise_pct, verdict}.
    Never raises — returns [] on any failure / missing data.
    """
    try:
        import math
        import yfinance as yf
        df = yf.Ticker(ticker).get_earnings_dates(limit=max(limit * 2, 12))
    except Exception as exc:
        logger.warning("earnings history: yfinance failed for %s (%s)", ticker, exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []

    def _num(v):
        try:
            f = float(v)
            return None if (f != f) else f   # drop NaN
        except Exception:
            return None

    out = []
    for idx, row in df.iterrows():
        actual = _num(row.get("Reported EPS"))
        if actual is None:                    # future / unreported quarter — skip
            continue
        est = _num(row.get("EPS Estimate"))
        surp = _num(row.get("Surprise(%)"))
        if surp is None:
            surp = _surprise(actual, est)
        try:
            report_date = str(idx)[:10]       # ISO date from the DatetimeIndex
        except Exception:
            continue
        out.append({"report_date": report_date, "eps_estimate": est,
                    "eps_actual": actual, "surprise_pct": surp,
                    "verdict": verdict(surp)})
        if len(out) >= limit:
            break
    return out


__all__ = ["fetch_earnings_history", "verdict"]
