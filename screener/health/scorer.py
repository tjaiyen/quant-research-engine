"""Company-health scoring — quality metrics graded against the sector floors.

Pulls the quality fields yfinance `.info` exposes (the FROZEN price provider only
does OHLCV + valuation multiples, so — like the sentiment scorer — this module
does its own yfinance call) and scores them against
`industry_config.SECTOR_QUALITY_FLOORS`. Everything degrades gracefully: missing
data → label "UNAVAILABLE", never raises (yfinance Tier-3 quality fields are
often patchy).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Pure grading helpers (no IO) — unit-tested directly.


def _normalise_debt_to_equity(raw):
    """yfinance reports debtToEquity as a PERCENT (e.g. 60.0 → 0.60). Some feeds
    already give a ratio; treat values >5 as a percent and divide by 100."""
    if raw is None:
        return None
    v = float(raw)
    return v / 100.0 if v > 5.0 else v


def grade(metrics: dict, floor) -> dict:
    """Grade a metrics dict against a QualityFloor → score + label + pass counts.

    Each floor with a non-None threshold and a present metric is one test. Score
    = passed / applicable. Floors that don't apply to the sector (None) or whose
    metric is missing are skipped (so banks/REITs aren't penalised for N/A rules).
    """
    checks = [
        ("operating_margin", floor.min_operating_margin, "min"),
        ("roe", floor.min_roe, "min"),
        ("debt_to_equity", floor.max_debt_to_equity, "max"),
        ("current_ratio", floor.min_current_ratio, "max_inv"),  # min threshold
    ]
    passed = total = 0
    for key, thresh, kind in checks:
        if thresh is None:
            continue
        val = metrics.get(key)
        if val is None:
            continue
        total += 1
        ok = (val <= thresh) if kind == "max" else (val >= thresh)
        passed += 1 if ok else 0
    if total == 0:
        return {"health_score": None, "health_label": "UNAVAILABLE",
                "floors_passed": 0, "floors_total": 0}
    score = passed / total
    label = "STRONG" if score >= 0.75 else "FAIR" if score >= 0.45 else "WEAK"
    return {"health_score": round(score, 3), "health_label": label,
            "floors_passed": passed, "floors_total": total}


def _yf_metrics(ticker: str) -> dict | None:
    """Pull quality metrics from yfinance `.info` (graceful → None on failure)."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        logger.warning("health: yfinance info failed for %s (%s)", ticker, exc)
        return None
    if not info:
        return None
    return {
        "roe": info.get("returnOnEquity"),
        "operating_margin": info.get("operatingMargins"),
        "profit_margin": info.get("profitMargins"),
        "debt_to_equity": _normalise_debt_to_equity(info.get("debtToEquity")),
        "current_ratio": info.get("currentRatio"),
        "eps_ttm": info.get("trailingEps"),
        "sector": info.get("sector"),
    }


def score_ticker_health(ticker: str, sector: str | None = None) -> dict:
    """Full health snapshot for a ticker → dict ready for `upsert_health`.

    `sector` (the engine's stored GICS sector) picks the quality floor; falls back
    to yfinance's own sector, then a lenient default.
    """
    from industry_config import SECTOR_QUALITY_FLOORS, _DEFAULT_FLOOR

    metrics = _yf_metrics(ticker)
    if not metrics:
        return {"roe": None, "operating_margin": None, "profit_margin": None,
                "debt_to_equity": None, "current_ratio": None, "eps_ttm": None,
                "health_score": None, "health_label": "UNAVAILABLE",
                "floors_passed": 0, "floors_total": 0}
    sec = sector or metrics.get("sector")
    floor = SECTOR_QUALITY_FLOORS.get(sec, _DEFAULT_FLOOR)
    out = {k: metrics.get(k) for k in ("roe", "operating_margin", "profit_margin",
                                       "debt_to_equity", "current_ratio", "eps_ttm")}
    out.update(grade(metrics, floor))
    return out


__all__ = ["score_ticker_health", "grade", "_normalise_debt_to_equity"]
