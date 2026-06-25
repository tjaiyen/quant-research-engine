"""Combinatorial Purged Cross-Validation (U25) over the panel's rebalance dates.

The tournament reports ONE in-sample→out-of-sample split (last ~third held out).
A single split is itself a lucky/unlucky draw. CPCV (De Prado; skfolio) instead
partitions the rebalance segments into groups and holds out every k-group
combination in turn — yielding a *distribution* of out-of-sample results rather
than one number. Adjacent segments are purged/embargoed so a held-out window
isn't contaminated by a directly neighbouring train window.

Scope honesty: the candidate's signal weights are FIXED (derived once from the
signal-lab IC), so there is no per-fold refitting — each fold simply re-scores
the SAME candidate on a different held-out window. The value is the *spread* of
the candidate's OOS excess-vs-SPY across many windows: a tight, positive spread
is real; a mean that flips sign across folds is fragile. We implement directly
over the ~10–60 segments (no heavy dep), citing skfolio's CPCV as the reference.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np


def _contiguous_groups(n: int, n_groups: int) -> list[list[int]]:
    """Split range(n) into `n_groups` near-equal contiguous index blocks."""
    n_groups = max(2, min(n_groups, n))
    out, base, rem, start = [], n // n_groups, n % n_groups, 0
    for g in range(n_groups):
        size = base + (1 if g < rem else 0)
        out.append(list(range(start, start + size)))
        start += size
    return [g for g in out if g]


def cpcv_splits(n_segments: int, n_groups: int = 6, k_test: int = 2,
                embargo: int = 1) -> list[dict]:
    """All (train, test) index splits: every k_test-of-n_groups held out, purged.

    A train index is dropped if it lies within `embargo` segments of any test
    index (purge + embargo). Returns [] when there are too few segments to form
    a meaningful split.
    """
    if n_segments < 4:
        return []
    groups = _contiguous_groups(n_segments, n_groups)
    g = len(groups)
    k_test = max(1, min(k_test, g - 1))
    splits = []
    for combo in combinations(range(g), k_test):
        test_idx = sorted(i for gi in combo for i in groups[gi])
        test_set = set(test_idx)
        purged = set()
        for ti in test_idx:
            for e in range(1, embargo + 1):
                purged.add(ti - e)
                purged.add(ti + e)
        train_idx = [i for i in range(n_segments)
                     if i not in test_set and i not in purged]
        if train_idx and test_idx:
            splits.append({"train": train_idx, "test": test_idx})
    return splits


def _compound(rets) -> float:
    eq = 1.0
    for r in rets:
        eq *= (1.0 + (r or 0.0))
    return eq - 1.0


def cpcv_distribution(seg_returns, spy_returns, splits) -> dict:
    """Candidate OOS excess-vs-SPY across every CPCV fold → mean ± spread.

    For each split, compound the candidate's returns over the held-out test
    segments and SPY over the same segments; the fold's edge is the difference.
    Returns {n_folds, mean_excess, std_excess, frac_positive, folds:[…]}.
    """
    folds = []
    for sp in splits:
        idx = sp["test"]
        cand = _compound(seg_returns[i] for i in idx)
        spy = _compound(spy_returns[i] for i in idx)
        folds.append({"n": len(idx), "candidate": cand, "spy": spy,
                      "excess": cand - spy})
    if not folds:
        return {"n_folds": 0, "mean_excess": float("nan"),
                "std_excess": float("nan"), "frac_positive": float("nan"),
                "folds": []}
    exc = np.asarray([f["excess"] for f in folds], dtype=float)
    return {
        "n_folds": len(folds),
        "mean_excess": float(exc.mean()),
        "std_excess": float(exc.std(ddof=1)) if exc.size > 1 else 0.0,
        "frac_positive": float((exc > 0).mean()),
        "folds": folds,
    }


__all__ = ["cpcv_splits", "cpcv_distribution"]
