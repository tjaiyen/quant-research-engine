"""Composite scoring. Transparent weighted blend over normalized signals.

Design notes (section 4.4):
- Outputs: a 0–1 score, a bucket, and a short human explanation.
- Governance (section 5.1): component weights are explicit, not learned —
  AutoML scoring comes later and must coexist with this simple baseline.
- Scope v2: Technical (45%) + Risk (30%) + Valuation (25%). When valuation
  is 'no_data' (e.g. ETFs), its weight is redistributed to Tech/Risk
  proportionally so the composite stays in [0, 1] and comparable.
"""
from __future__ import annotations

from dataclasses import dataclass

from models_fundamental import ValuationSignals, valuation_explanation
from models_quant import RiskSignals
from models_technical import TechnicalSignals

W_TECH = 0.45
W_RISK = 0.30
W_VAL = 0.25


@dataclass(frozen=True)
class CompositeScore:
    symbol: str
    technical: float          # 0..1
    risk: float               # 0..1 (1 = low risk / safer)
    valuation: float | None   # 0..1 (1 = attractively cheap); None if no data
    composite: float          # 0..1 weighted blend
    bucket: str               # 'top_candidate', 'watch', 'avoid'
    explanation: str          # short why


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _technical_score(t: TechnicalSignals) -> float:
    parts: list[float] = []

    trend_map = {"bullish": 1.0, "neutral": 0.5, "bearish": 0.0}
    parts.append(trend_map.get(t.trend_regime, 0.5) * 0.4)

    if t.ret_1m is not None:
        m = _clip01(0.5 + t.ret_1m / 0.20)
        parts.append(m * 0.3)
    else:
        parts.append(0.5 * 0.3)

    if t.rsi_14 is not None:
        r = t.rsi_14
        if 40 <= r <= 65:
            rsi_score = 1.0
        elif 30 <= r < 40 or 65 < r <= 75:
            rsi_score = 0.6
        elif r > 75:
            rsi_score = 0.2
        else:
            rsi_score = 0.3
        parts.append(rsi_score * 0.3)
    else:
        parts.append(0.5 * 0.3)

    return _clip01(sum(parts))


def _risk_score(r: RiskSignals) -> float:
    parts: list[float] = []
    if r.vol_30d_ann is not None:
        v = _clip01(1.0 - (r.vol_30d_ann - 0.10) / 0.30)
        parts.append(v * 0.7)
    else:
        parts.append(0.5 * 0.7)
    if r.max_drawdown_1y is not None:
        d = _clip01(1.0 + r.max_drawdown_1y / 0.40)
        parts.append(d * 0.3)
    else:
        parts.append(0.5 * 0.3)
    return _clip01(sum(parts))


def _bucket(composite: float) -> str:
    if composite >= 0.70:
        return "top_candidate"
    if composite >= 0.45:
        return "watch"
    return "avoid"


def _explain(t: TechnicalSignals, r: RiskSignals, v: ValuationSignals) -> str:
    bits = [f"Trend {t.trend_regime}"]
    if t.ret_1m is not None:
        bits.append(f"1M {t.ret_1m*100:+.1f}%")
    if t.rsi_14 is not None:
        bits.append(f"RSI {t.rsi_14:.0f}")
    if r.vol_30d_ann is not None:
        bits.append(f"Vol30 {r.vol_30d_ann*100:.0f}%")
    if r.max_drawdown_1y is not None:
        bits.append(f"DD1Y {r.max_drawdown_1y*100:.0f}%")
    val_str = valuation_explanation(v)
    if val_str and val_str != "No fundamentals":
        bits.append(val_str)
    return " · ".join(bits)


def score_ticker(
    symbol: str,
    t: TechnicalSignals,
    r: RiskSignals,
    v: ValuationSignals,
) -> CompositeScore:
    tech = _technical_score(t)
    risk = _risk_score(r)

    if v.score is None:
        # Redistribute valuation weight proportionally to tech + risk.
        total = W_TECH + W_RISK
        composite = (W_TECH / total) * tech + (W_RISK / total) * risk
        valuation_out = None
    else:
        composite = W_TECH * tech + W_RISK * risk + W_VAL * v.score
        valuation_out = v.score

    return CompositeScore(
        symbol=symbol,
        technical=tech,
        risk=risk,
        valuation=valuation_out,
        composite=composite,
        bucket=_bucket(composite),
        explanation=_explain(t, r, v),
    )
