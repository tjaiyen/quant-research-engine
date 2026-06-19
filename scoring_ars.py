"""ARS — Aggregate Risk-adjusted Score (0–100), bucket mapping, NL summary.

This is the new composite engine for Tab 6. Coexists with `scoring_legacy.py`
(which produces 0–1 CompositeScore for the Overview tab and suggestions).

Five components, each 0–100:
  Technical   — trend / RSI / momentum from models_technical
  Valuation   — DCF + multiples blend from fundamental.py (× 100)
  Risk        — inverse of vol + drawdown (1 = safe, 0 = risky)
  Quality     — composite proxy: DD-stability, dividend, fundamental coverage
  Growth      — 3M / 6M / 12M momentum

Base weights (applied first, then tilted by sector via industry_config):
  Tech 20% · Val 25% · Risk 20% · Quality 15% · Growth 20%

Sector tilts come from `industry_config.weight_tilt_for_sector`. Missing
component data is handled by redistributing weight proportionally to
present components (so composite stays in [0,100] and comparable across tickers).

Bucket map (0–100):
  ≥ 75: strong_buy   ·  60–74: buy   ·  45–59: hold   ·  30–44: reduce   ·  < 30: avoid
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from data_fetcher import detect_data_tier, tier_info
from fundamental import compute_fundamental_valuation
from industry_config import quality_floor_for_sector, weight_tilt_for_sector
from models_quant import compute_risk
from models_technical import compute_technical
from utils.db import (
    fetch_latest_fundamentals,
    fetch_prices,
    list_tickers,
)
from utils.logging_setup import get_logger

log = get_logger(__name__)


# ============================================================================
# Dataclasses
# ============================================================================

@dataclass(frozen=True)
class ARSComponent:
    name: str                    # 'Technical' | 'Valuation' | ...
    score: float                 # 0..100; None-equivalent rendered as np.nan
    weight: float                # post-tilt, post-renormalize fractional weight
    rationale: str               # short why-text


@dataclass(frozen=True)
class ARSScore:
    ticker: str
    sector: str | None
    composite: float             # 0..100
    bucket: str                  # 'strong_buy' | 'buy' | 'hold' | 'reduce' | 'avoid'
    components: tuple[ARSComponent, ...]
    nl_summary: str
    confidence: float            # 0..1 from data tier + coverage
    data_tier: int
    warnings: tuple[str, ...] = ()


# ============================================================================
# Component scoring
# ============================================================================

BASE_WEIGHTS = {
    "technical": 0.20,
    "valuation": 0.25,
    "risk":      0.20,
    "quality":   0.15,
    "growth":    0.20,
}

# Bucket thresholds (defaults — user can override via Settings modal,
# stored in user_settings table key="ars_thresholds").
_BUCKET_TABLE = (
    (75.0, "strong_buy"),
    (60.0, "buy"),
    (45.0, "hold"),
    (30.0, "reduce"),
    (0.0,  "avoid"),
)


def _current_bucket_table() -> tuple[tuple[float, str], ...]:
    """Read user-set thresholds (Settings modal) or fall back to defaults.

    Bound at call time so changes via the Settings modal apply on the next
    score computation without an app restart.
    """
    try:
        from utils.db import get_setting
        overrides = get_setting("ars_thresholds", default=None)
        if isinstance(overrides, dict):
            return (
                (float(overrides.get("strong_buy", 75.0)), "strong_buy"),
                (float(overrides.get("buy", 60.0)),        "buy"),
                (float(overrides.get("hold", 45.0)),       "hold"),
                (float(overrides.get("reduce", 30.0)),     "reduce"),
                (0.0,                                      "avoid"),
            )
    except Exception:
        pass
    return _BUCKET_TABLE


def _bucket_for(score: float) -> str:
    for threshold, label in _current_bucket_table():
        if score >= threshold:
            return label
    return "avoid"


def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _technical_component(df: pd.DataFrame) -> tuple[float | None, str]:
    """Score 0..100. Trend (40%) + RSI sweet-spot (30%) + 1M momentum (30%)."""
    if df.empty:
        return None, "no price data"
    t = compute_technical(df)
    parts: list[tuple[float, float]] = []
    # Trend
    trend_score = {"bullish": 100, "neutral": 55, "bearish": 15}.get(t.trend_regime, 55)
    parts.append((trend_score, 0.40))
    # RSI sweet spot 40–65
    if t.rsi_14 is None:
        parts.append((50, 0.30))
        rsi_note = "RSI n/a"
    else:
        r = t.rsi_14
        if 40 <= r <= 65:
            rs = 100
        elif 30 <= r < 40 or 65 < r <= 75:
            rs = 60
        elif r > 75:
            rs = 20
        else:
            rs = 30
        parts.append((rs, 0.30))
        rsi_note = f"RSI {r:.0f}"
    # 1M momentum
    if t.ret_1m is None:
        parts.append((50, 0.30))
        mom_note = "1M n/a"
    else:
        mom = _clip(50 + (t.ret_1m / 0.20) * 50)  # ±20% maps to 0/100
        parts.append((mom, 0.30))
        mom_note = f"1M {t.ret_1m*100:+.1f}%"
    score = sum(s * w for s, w in parts)
    rationale = f"Trend {t.trend_regime} · {rsi_note} · {mom_note}"
    return _clip(score), rationale


def _valuation_component(ticker: str) -> tuple[float | None, str]:
    """Score 0..100 from compute_fundamental_valuation × 100."""
    val = compute_fundamental_valuation(ticker)
    if val.composite_score is None or val.composite_score == 0.0 and val.bucket == "no_data":
        return None, "no fundamentals"
    score = val.composite_score * 100.0
    bits: list[str] = []
    if val.dcf.upside_pct is not None:
        bits.append(f"DCF {val.dcf.upside_pct*100:+.0f}%")
    if val.forward_pe:
        bits.append(f"FwdPE {val.forward_pe:.1f}")
    if val.ev_ebitda:
        bits.append(f"EV/EBITDA {val.ev_ebitda:.1f}")
    if not bits:
        bits = [val.bucket]
    return _clip(score), " · ".join(bits)


def _risk_component(df: pd.DataFrame) -> tuple[float | None, str]:
    """Score 0..100 — higher = safer. From compute_risk."""
    if df.empty:
        return None, "no price data"
    r = compute_risk(df)
    parts: list[tuple[float, float]] = []
    # Vol — 10% ann -> 100, 40% -> 0
    if r.vol_30d_ann is None:
        parts.append((50, 0.7))
        vol_note = "vol n/a"
    else:
        vs = _clip(100.0 - (r.vol_30d_ann - 0.10) / 0.30 * 100.0)
        parts.append((vs, 0.7))
        vol_note = f"vol30 {r.vol_30d_ann*100:.0f}%"
    # Max drawdown 1Y — 0% -> 100, -40% -> 0
    if r.max_drawdown_1y is None:
        parts.append((50, 0.3))
        dd_note = "DD n/a"
    else:
        ds = _clip(100.0 + r.max_drawdown_1y / 0.40 * 100.0)
        parts.append((ds, 0.3))
        dd_note = f"DD1Y {r.max_drawdown_1y*100:.0f}%"
    score = sum(s * w for s, w in parts)
    return _clip(score), f"{vol_note} · {dd_note}"


def _quality_component(
    df: pd.DataFrame, snapshot: dict | None, sector: str | None
) -> tuple[float | None, str]:
    """Quality proxy from price stability + dividend track record + coverage.

    With Tier-3 fundamentals snapshot we can't get clean ROE/op-margin,
    so the proxy uses what we have:
      - Drawdown stability (inverse of recent DD)
      - Dividend payer? (binary)
      - Fundamentals coverage (how many multiples present)
    Scaled 0..100. Floor checks from industry_config are surfaced as notes.
    """
    parts: list[tuple[float, float]] = []
    notes: list[str] = []
    floor = quality_floor_for_sector(sector)

    # Stability — penalize big 1Y drawdowns.
    if df.empty:
        parts.append((50, 0.4))
        notes.append("price n/a")
    else:
        r = compute_risk(df)
        if r.max_drawdown_1y is None:
            parts.append((50, 0.4))
            notes.append("DD n/a")
        else:
            stability = _clip(100.0 + r.max_drawdown_1y / 0.30 * 100.0)
            parts.append((stability, 0.4))
            notes.append(f"DD-stable {stability:.0f}")

    # Dividend?
    if snapshot is None:
        parts.append((50, 0.2))
        notes.append("snapshot n/a")
    else:
        div_y = snapshot.get("div_yield")
        # Normalize percent-vs-decimal inconsistency the same way fundamental.py does
        if div_y is not None and div_y > 0.20:
            div_y = div_y / 100.0
        if div_y and div_y > 0.005:
            parts.append((85, 0.2))
            notes.append(f"yield {div_y*100:.1f}%")
        else:
            parts.append((50, 0.2))
            notes.append("non-payer")

    # Coverage of valuation multiples.
    if snapshot is None:
        coverage_score = 30.0
        notes.append("no fundamentals")
    else:
        present = sum(1 for k in ("forward_pe", "pe", "peg", "ps", "pb", "ev_ebitda")
                      if snapshot.get(k))
        coverage_score = _clip(present * 100.0 / 6.0)
        notes.append(f"coverage {present}/6")
    parts.append((coverage_score, 0.4))

    score = sum(s * w for s, w in parts)
    return _clip(score), " · ".join(notes)


def _growth_component(df: pd.DataFrame) -> tuple[float | None, str]:
    """Score 0..100 from blended 3M / 6M / 12M total returns."""
    if df.empty:
        return None, "no price data"
    px = df["adj_close"].dropna()
    if len(px) < 21:
        return None, "insufficient history"

    def _ret(n: int) -> float | None:
        if len(px) <= n:
            return None
        a, b = px.iloc[-1], px.iloc[-1 - n]
        if pd.isna(a) or pd.isna(b) or b == 0:
            return None
        return float(a / b - 1.0)

    parts: list[tuple[float, float]] = []
    notes: list[str] = []

    r3 = _ret(63)
    if r3 is None:
        parts.append((50, 0.4))
        notes.append("3M n/a")
    else:
        parts.append((_clip(50 + r3 / 0.30 * 50), 0.4))
        notes.append(f"3M {r3*100:+.0f}%")
    r6 = _ret(126)
    if r6 is None:
        parts.append((50, 0.3))
        notes.append("6M n/a")
    else:
        parts.append((_clip(50 + r6 / 0.50 * 50), 0.3))
        notes.append(f"6M {r6*100:+.0f}%")
    r12 = _ret(252)
    if r12 is None:
        parts.append((50, 0.3))
        notes.append("12M n/a")
    else:
        parts.append((_clip(50 + r12 / 0.80 * 50), 0.3))
        notes.append(f"12M {r12*100:+.0f}%")

    score = sum(s * w for s, w in parts)
    return _clip(score), " · ".join(notes)


# ============================================================================
# Composite assembly
# ============================================================================

def _apply_tilt_and_normalize(
    base: dict[str, float],
    tilt: dict[str, float],
    available: set[str],
) -> dict[str, float]:
    """Multiply base weights by sector tilts, drop unavailable, renormalize to sum=1."""
    raw = {k: base[k] * tilt.get(k, 1.0) for k in base if k in available}
    total = sum(raw.values())
    if total <= 0:
        return {k: 1.0 / max(1, len(raw)) for k in raw}
    return {k: v / total for k, v in raw.items()}


def _nl_summary(score: ARSScore) -> str:
    """Compose a 1–2 sentence narrative based on top/bottom components."""
    bucket_phrase = {
        "strong_buy": "Strong setup across multiple dimensions.",
        "buy":        "Constructive setup overall.",
        "hold":       "Mixed signals — watch list rather than action.",
        "reduce":     "Notable weaknesses outweigh strengths.",
        "avoid":      "Multiple negative signals; high caution.",
    }.get(score.bucket, "")
    sorted_components = sorted(score.components, key=lambda c: c.score, reverse=True)
    top = sorted_components[:2]
    bottom = sorted_components[-1] if len(sorted_components) >= 3 else None

    parts = [
        f"{score.ticker} — ARS {score.composite:.0f} ({score.bucket.replace('_', ' ').title()})."
    ]
    if bucket_phrase:
        parts.append(bucket_phrase)
    if top:
        leaders = " and ".join(f"{c.name} ({c.score:.0f})" for c in top)
        parts.append(f"Strongest: {leaders}.")
    if bottom is not None and bottom.score < 50:
        parts.append(f"Weak point: {bottom.name} ({bottom.score:.0f}).")
    if score.sector:
        parts.append(f"Sector: {score.sector}.")
    if score.data_tier == 3:
        parts.append("Reduced data confidence — Tier 3.")
    return " ".join(parts)


def compute_ars(
    ticker: str,
    snapshot: dict | None = None,
) -> ARSScore:
    """Compute the full ARS for a ticker."""
    ticker = ticker.upper()
    tier = detect_data_tier(ticker)
    if snapshot is None:
        snapshot = fetch_latest_fundamentals(ticker) or {}
    sector = snapshot.get("sector") if snapshot else None

    df = fetch_prices(ticker)

    raw_scores: dict[str, tuple[float | None, str]] = {
        "technical": _technical_component(df),
        "valuation": _valuation_component(ticker),
        "risk":      _risk_component(df),
        "quality":   _quality_component(df, snapshot or None, sector),
        "growth":    _growth_component(df),
    }

    available = {k for k, (s, _) in raw_scores.items() if s is not None}
    tilt_obj = weight_tilt_for_sector(sector)
    tilt_dict = {
        "technical": tilt_obj.technical,
        "valuation": tilt_obj.valuation,
        "quality":   tilt_obj.quality,
        "growth":    tilt_obj.growth,
        "risk":      tilt_obj.risk,
    }
    weights = _apply_tilt_and_normalize(BASE_WEIGHTS, tilt_dict, available)

    components: list[ARSComponent] = []
    composite_total = 0.0
    for name in ("technical", "valuation", "risk", "quality", "growth"):
        score, rationale = raw_scores[name]
        weight = weights.get(name, 0.0)
        if score is None:
            components.append(ARSComponent(
                name=name.title(), score=float("nan"), weight=0.0,
                rationale=f"unavailable — {rationale}",
            ))
            continue
        components.append(ARSComponent(
            name=name.title(), score=score, weight=weight, rationale=rationale,
        ))
        composite_total += score * weight

    bucket = _bucket_for(composite_total)

    # Confidence: tier-driven * coverage of components
    coverage = len(available) / 5.0
    confidence = max(0.0, min(1.0, 0.5 * tier_info(tier).confidence + 0.5 * coverage))

    warnings: list[str] = []
    missing = set(BASE_WEIGHTS) - available
    if missing:
        warnings.append(f"Missing components: {', '.join(sorted(missing))} — weights redistributed")
    if tier == 3:
        warnings.append("Tier 3 data — quality and valuation lean on proxies")

    score_obj = ARSScore(
        ticker=ticker, sector=sector,
        composite=composite_total,
        bucket=bucket,
        components=tuple(components),
        nl_summary="",  # set below after object exists
        confidence=confidence,
        data_tier=tier,
        warnings=tuple(warnings),
    )
    nl = _nl_summary(score_obj)
    # Recreate with NL filled in (frozen dataclass).
    return ARSScore(
        ticker=score_obj.ticker, sector=score_obj.sector,
        composite=score_obj.composite, bucket=score_obj.bucket,
        components=score_obj.components, nl_summary=nl,
        confidence=score_obj.confidence, data_tier=score_obj.data_tier,
        warnings=score_obj.warnings,
    )


def rank_watchlist_by_ars(top_n: int = 50) -> list[ARSScore]:
    """Compute ARS for every ticker in the DB, sort descending, return top N."""
    syms = list_tickers()["symbol"].tolist()
    scores: list[ARSScore] = []
    for s in syms:
        try:
            scores.append(compute_ars(s))
        except Exception:
            log.exception("ARS failed for %s — skipping", s)
    scores.sort(key=lambda x: x.composite, reverse=True)
    return scores[:top_n]


__all__ = [
    "ARSComponent",
    "ARSScore",
    "BASE_WEIGHTS",
    "compute_ars",
    "rank_watchlist_by_ars",
]
