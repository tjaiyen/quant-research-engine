"""Trim/add suggestion engine.

Combines CompositeScore (Tech/Risk/Valuation), portfolio holdings, and
Component CVaR to produce ranked, explainable suggestions. Outputs are
intentionally conservative — thresholds suppress weak signals, and
language stays 'consider' (not 'recommend').

Governance (section 5.1):
- Weights are explicit constants below, not learned.
- Every suggestion carries the numeric evidence that produced it.
- Empty lists are valid outputs; we don't manufacture suggestions to fill slots.
"""
from __future__ import annotations

from dataclasses import dataclass

from models_portfolio import RiskContribution
from scoring_legacy import CompositeScore

TRIM_THRESHOLD = 0.50
ADD_THRESHOLD = 0.50


@dataclass(frozen=True)
class TrimCandidate:
    ticker: str
    intensity: float            # 0..1
    composite: float            # from scoring
    risk_multiplier: float      # from Component CVaR decomposition
    contribution_pct: float     # share of portfolio tail risk
    reason: str


@dataclass(frozen=True)
class AddCandidate:
    ticker: str
    intensity: float
    composite: float
    valuation: float | None
    reason: str


def _val_bonus(val_score: float | None) -> float:
    if val_score is None:
        return 0.3
    if val_score >= 0.65:
        return 1.0
    if val_score >= 0.35:
        return 0.5
    return 0.0


def _val_label(val_score: float | None) -> str:
    if val_score is None:
        return "val n/a"
    if val_score >= 0.65:
        return "val attractive"
    if val_score >= 0.35:
        return "val fair"
    return "val expensive"


def _trim_intensity(composite: float, risk_multiplier: float) -> float:
    """High when composite is low AND risk multiplier is high."""
    comp_part = (1.0 - composite) * 0.5
    risk_part = min(risk_multiplier / 2.0, 1.0) * 0.5
    return max(0.0, min(1.0, comp_part + risk_part))


def _add_intensity(composite: float, val_score: float | None) -> float:
    """High when composite is high AND valuation is not expensive."""
    return max(0.0, min(1.0, composite * 0.7 + _val_bonus(val_score) * 0.3))


def _trim_reason(c: CompositeScore, mult: float, contrib_pct: float) -> str:
    bucket = "low" if c.composite < 0.45 else "watch" if c.composite < 0.70 else "top"
    mult_label = (
        "overweight risk" if mult >= 1.3
        else "balanced risk" if mult >= 0.9
        else "underweight risk"
    )
    return (
        f"Composite {c.composite*100:.0f} ({bucket}), "
        f"risk multiplier {mult:.2f}x ({mult_label}), "
        f"{contrib_pct*100:.0f}% of tail loss"
    )


def _add_reason(c: CompositeScore) -> str:
    return (
        f"Composite {c.composite*100:.0f}, "
        f"tech {c.technical*100:.0f} · risk-adj {c.risk*100:.0f} · "
        f"{_val_label(c.valuation)}"
    )


def build_trim_candidates(
    scores: list[CompositeScore],
    contribs: list[RiskContribution],
    held_symbols: set[str],
    max_n: int = 3,
) -> list[TrimCandidate]:
    contrib_map = {rc.ticker: rc for rc in contribs}
    score_map = {s.symbol: s for s in scores}

    out: list[TrimCandidate] = []
    for ticker in held_symbols:
        cs = score_map.get(ticker)
        rc = contrib_map.get(ticker)
        if cs is None or rc is None:
            continue
        intensity = _trim_intensity(cs.composite, rc.risk_multiplier)
        if intensity < TRIM_THRESHOLD:
            continue
        out.append(
            TrimCandidate(
                ticker=ticker,
                intensity=intensity,
                composite=cs.composite,
                risk_multiplier=rc.risk_multiplier,
                contribution_pct=rc.contribution_pct,
                reason=_trim_reason(cs, rc.risk_multiplier, rc.contribution_pct),
            )
        )
    out.sort(key=lambda t: t.intensity, reverse=True)
    return out[:max_n]


def build_add_candidates(
    scores: list[CompositeScore],
    held_symbols: set[str],
    max_n: int = 3,
) -> list[AddCandidate]:
    out: list[AddCandidate] = []
    for cs in scores:
        if cs.symbol in held_symbols:
            continue
        intensity = _add_intensity(cs.composite, cs.valuation)
        if intensity < ADD_THRESHOLD:
            continue
        out.append(
            AddCandidate(
                ticker=cs.symbol,
                intensity=intensity,
                composite=cs.composite,
                valuation=cs.valuation,
                reason=_add_reason(cs),
            )
        )
    out.sort(key=lambda a: a.intensity, reverse=True)
    return out[:max_n]
