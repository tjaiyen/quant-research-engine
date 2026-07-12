"""Per-period signal lifecycle: alive / reversed / dead (U31).

The lab's headline IC averages over the whole panel window, which hides decay:
a signal that worked in 2023 and died in 2025 still shows a positive pooled
IC. This buckets the panel per calendar year and categorises each signal per
bucket, so SignalLab.md shows the lifecycle (``2023 alive → 2025 dead``).

Thresholds ported from HKUDS/Vibe-Trading ``agent/src/factors/bench_runner.py``
(MIT): alive = mean IC > 0.02 ∧ positive-ratio ≥ 0.55 ∧ |t| > 2;
reversed = mean IC < −0.02 ∧ |t| > 2; else dead. "insufficient" (a bucket
with fewer than MIN_DATES IC observations) is reported as such, never forced
into a verdict.
"""
from __future__ import annotations

import math
from collections import defaultdict

from screener.signal_lab.lab import _ic_series, _rows_by_date, _signal_keys

MIN_DATES = 4          # fewer IC observations than this → "insufficient"

IC_ALIVE = 0.02
IC_REVERSED = -0.02
POS_RATIO_ALIVE = 0.55
T_CRIT = 2.0


def t_stat(ic_mean: float, ic_std: float, n: int) -> float:
    """Two-sided t-statistic of an IC series."""
    if not (n > 0 and ic_std > 0 and math.isfinite(ic_std)):
        return 0.0
    return ic_mean / (ic_std / math.sqrt(n))


def categorise(ics: list[float]) -> dict:
    """Categorise one bucket's IC series → {category, ic, t, pos_ratio, n}."""
    n = len(ics)
    if n < MIN_DATES:
        return {"category": "insufficient", "ic": None, "t": None,
                "pos_ratio": None, "n": n}
    mean = sum(ics) / n
    var = sum((x - mean) ** 2 for x in ics) / (n - 1)
    std = math.sqrt(var)
    pos = sum(1 for x in ics if x > 0) / n
    # Zero-variance series = maximal consistency, not absent evidence (the
    # upstream t_stat guard would return 0 and mislabel a perfect signal dead).
    t = (math.copysign(math.inf, mean) if std < 1e-12 and mean != 0
         else t_stat(mean, std, n))
    if mean > IC_ALIVE and pos >= POS_RATIO_ALIVE and abs(t) > T_CRIT:
        cat = "alive"
    elif mean < IC_REVERSED and abs(t) > T_CRIT:
        cat = "reversed"
    else:
        cat = "dead"
    return {"category": cat, "ic": mean, "t": t, "pos_ratio": pos, "n": n}


def signal_lifecycle(panel: dict) -> dict:
    """Per-year lifecycle for every signal column in the panel.

    Returns {years: [..], signals: {sig: {year: bucket-dict}}} — years sorted
    ascending, so a note can render one row per signal, one column per year.
    """
    rbd = _rows_by_date(panel)
    by_year: dict[str, dict] = defaultdict(dict)
    for d, rows in rbd.items():
        by_year[str(d)[:4]][d] = rows
    years = sorted(by_year)
    out: dict = {"years": years, "signals": {}}
    for sig in _signal_keys(panel):
        out["signals"][sig] = {
            y: categorise(_ic_series(by_year[y], sig)) for y in years
        }
    return out


__all__ = ["signal_lifecycle", "categorise", "t_stat", "MIN_DATES"]
