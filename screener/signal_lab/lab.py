"""Signal diagnostics + a conservative re-weighting recommendation.

All metrics are cross-sectional (per rebalance date, then averaged) so they
measure "does sorting stocks on this signal predict their forward return."
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from screener.backtest.signal_ic import _spearman
from screener.tournament.variants import SIGNALS


def _rows_by_date(panel: dict) -> dict:
    out: dict = defaultdict(list)
    for r in panel.get("rows", []):
        out[r["d0"]].append(r)
    return out


def _ic_series(rows_by_date: dict, sig: str) -> list[float]:
    """One cross-sectional Spearman IC per date (drops nan/degenerate dates)."""
    ics = []
    for rows in rows_by_date.values():
        pairs = [((r.get("signals") or {}).get(sig), r.get("fwd_return")) for r in rows]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        if len(pairs) >= 5:
            ic = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
            if ic == ic:                       # not nan
                ics.append(ic)
    return ics


def _quintile_spread(rows_by_date: dict, sig: str) -> float | None:
    """Mean (top-quintile fwd return − bottom-quintile), averaged across dates."""
    spreads = []
    for rows in rows_by_date.values():
        v = [((r.get("signals") or {}).get(sig), r.get("fwd_return")) for r in rows]
        v = [(x, y) for x, y in v if x is not None and y is not None]
        if len(v) < 10:
            continue
        v.sort(key=lambda t: t[0])
        q = max(1, len(v) // 5)
        spreads.append(float(np.mean([y for _, y in v[-q:]]) -
                             np.mean([y for _, y in v[:q]])))
    return float(np.mean(spreads)) if spreads else None


def _verdict(ic: float | None) -> str:
    if ic is None:
        return "no data"
    if ic < -0.03:
        return "DROP / FLIP — predicts backwards"
    if abs(ic) < 0.02:
        return "DROP — no edge (~0 IC)"
    if ic < 0.05:
        return "weak keep — small edge"
    return "KEEP — real edge"


def analyze_signals(panel: dict) -> dict:
    rbd = _rows_by_date(panel)
    regime_of = {s["d0"]: s.get("regime") for s in panel.get("segments", [])}
    dates_by_regime: dict = defaultdict(list)
    for d, reg in regime_of.items():
        dates_by_regime[reg].append(d)

    out: dict = {"n_dates": len(rbd), "n_rows": len(panel.get("rows", [])),
                 "signals": {}}
    for sig in SIGNALS:
        ics = _ic_series(rbd, sig)
        ic_mean = float(np.mean(ics)) if ics else None
        ic_std = float(np.std(ics, ddof=1)) if len(ics) > 1 else None
        ir = (ic_mean / ic_std * np.sqrt(len(ics))
              if ic_mean is not None and ic_std and ic_std > 1e-9 else None)
        by_regime = {}
        for reg, dates in dates_by_regime.items():
            sub = {d: rbd[d] for d in dates if d in rbd}
            ri = _ic_series(sub, sig)
            by_regime[reg] = float(np.mean(ri)) if ri else None
        out["signals"][sig] = {
            "ic": ic_mean, "ic_ir": ir, "n_dates": len(ics),
            "quintile_spread": _quintile_spread(rbd, sig),
            "by_regime": by_regime, "verdict": _verdict(ic_mean),
        }

    # pooled signal-signal Spearman correlation (redundancy check)
    vals = {s: [(r.get("signals") or {}).get(s) for r in panel.get("rows", [])]
            for s in SIGNALS}
    corr: dict = {}
    for a in SIGNALS:
        corr[a] = {}
        for b in SIGNALS:
            pairs = [(x, y) for x, y in zip(vals[a], vals[b])
                     if x is not None and y is not None]
            c = _spearman([p[0] for p in pairs], [p[1] for p in pairs]) \
                if len(pairs) >= 5 else float("nan")
            corr[a][b] = None if c != c else c
    out["correlation"] = corr
    return out


def recommend_weights(analysis: dict) -> dict:
    """Conservative candidate: floor negative-IC signals to 0, weight ∝ IC.

    A constrained derivation (not a free optimizer) to limit overfitting. If no
    signal has positive IC, fall back to equal weight (and the verdict will say
    the signals lack edge).
    """
    ics = {s: (analysis["signals"].get(s, {}).get("ic") or 0.0) for s in SIGNALS}
    pos = {s: max(0.0, v) for s, v in ics.items()}
    tot = sum(pos.values())
    if tot <= 0:
        return {s: 1.0 / len(SIGNALS) for s in SIGNALS}
    return {s: pos[s] / tot for s in SIGNALS}


__all__ = ["analyze_signals", "recommend_weights"]
