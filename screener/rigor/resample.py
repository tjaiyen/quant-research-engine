"""Permutation max-drawdown test + bootstrap Sharpe CI (U29).

Two resampling checks on the strategy's per-rebalance segment returns,
complementing DSR (multiple-trial deflation) and CPCV (path robustness):

- ``permutation_test`` — shuffle the ORDER of segment returns and recompound.
  Null: the observed max drawdown is no worse than a random ordering of the
  same returns. Mean/std (and hence Sharpe) are exactly order-invariant, so a
  permutation of returns can only speak to PATH shape — the max-drawdown
  p-value is the one meaningful output, and no Sharpe p-value is reported.
  A LOW p_value_max_dd means the realised ordering drew down *worse* than
  random orderings of the same returns (clustered losses); a HIGH value means
  the path was benign relative to its own return set.

- ``bootstrap_sharpe_ci`` — resample segment returns with replacement to get
  the sampling distribution of the per-period Sharpe: CI bounds and
  P(Sharpe > 0). DSR asks "is the best-of-N trials real?"; the bootstrap asks
  "how stable is THIS estimate under resampling?".

Pure numpy, seeded ``default_rng`` — deterministic, no new deps. Algorithm
shape adapted from HKUDS/Vibe-Trading ``agent/backtest/validation.py`` (MIT),
re-based onto segment returns instead of trade PnLs.
"""
from __future__ import annotations

import numpy as np

MIN_SEGMENTS = 5


def _clean(seg_returns) -> np.ndarray:
    return np.asarray([x for x in seg_returns if x is not None], dtype=float)


def _max_drawdown(rets: np.ndarray) -> float:
    """Max drawdown (negative fraction) of the compounded equity path.

    Equity starts at 1.0 BEFORE the first return — without that baseline a
    losing streak at the very start would be invisible to the running peak.
    """
    equity = np.concatenate(([1.0], np.cumprod(1.0 + rets)))
    peak = np.maximum.accumulate(equity)
    return float(((equity - peak) / peak).min())


def permutation_test(seg_returns, n_sims: int = 1000, seed: int = 42) -> dict:
    """Shuffle segment-return order → max-drawdown significance.

    Returns {observed_max_dd, p_value_max_dd, sim_max_dd_median, sim_max_dd_p5,
    n_sims, n_obs}; an {error, ...} dict when there are too few segments to
    judge (never a fabricated p-value).
    """
    r = _clean(seg_returns)
    if r.size < MIN_SEGMENTS:
        return {"error": f"need at least {MIN_SEGMENTS} segment returns",
                "n_obs": int(r.size)}
    observed = _max_drawdown(r)
    rng = np.random.default_rng(seed)
    sims = np.empty(n_sims)
    worse = 0
    for i in range(n_sims):
        sims[i] = _max_drawdown(rng.permutation(r))
        if sims[i] <= observed:            # more negative = worse than observed
            worse += 1
    return {
        "observed_max_dd": observed,
        # P(random ordering draws down at least as badly as the real path)
        "p_value_max_dd": worse / n_sims,
        "sim_max_dd_median": float(np.median(sims)),
        "sim_max_dd_p5": float(np.percentile(sims, 5)),
        "n_sims": int(n_sims),
        "n_obs": int(r.size),
    }


def bootstrap_sharpe_ci(seg_returns, n_boot: int = 1000,
                        confidence: float = 0.95, seed: int = 42) -> dict:
    """Bootstrap CI for the per-period Sharpe of the segment returns.

    Returns {observed_sharpe, ci_lower, ci_upper, median_sharpe, prob_positive,
    confidence, n_boot, n_obs} (per-period units — the same unit the DSR
    machinery uses); an {error, ...} dict on degenerate input.
    """
    r = _clean(seg_returns)
    if r.size < MIN_SEGMENTS:
        return {"error": f"need at least {MIN_SEGMENTS} segment returns",
                "n_obs": int(r.size)}
    sd = float(r.std(ddof=1))
    if sd < 1e-12:
        return {"error": "zero-variance returns", "n_obs": int(r.size)}
    observed = float(r.mean()) / sd
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        s = rng.choice(r, size=r.size, replace=True)
        bsd = float(s.std(ddof=1))
        boots[i] = float(s.mean()) / bsd if bsd > 1e-12 else 0.0
    alpha = (1.0 - confidence) / 2.0
    return {
        "observed_sharpe": observed,
        "ci_lower": float(np.percentile(boots, alpha * 100)),
        "ci_upper": float(np.percentile(boots, (1.0 - alpha) * 100)),
        "median_sharpe": float(np.median(boots)),
        "prob_positive": float(np.mean(boots > 0)),
        "confidence": confidence,
        "n_boot": int(n_boot),
        "n_obs": int(r.size),
    }


__all__ = ["permutation_test", "bootstrap_sharpe_ci"]
