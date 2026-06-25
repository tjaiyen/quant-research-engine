"""Deflated Sharpe Ratio / probability-of-backtest-overfit (U26).

The tournament tries ~20 variants and crowns the best. With that many trials the
top Sharpe is *expected* to be inflated even if every variant is pure noise — the
classic multiple-comparisons trap the institutional brief flags. De Prado's
Deflated Sharpe Ratio (DSR) corrects for exactly this: it discounts the observed
Sharpe by the Sharpe you'd expect the *luckiest* of N independent noise trials to
post, given the spread of Sharpes actually observed, then returns the probability
the strategy's true Sharpe is still > 0 after that deflation.

DSR ≈ 1.0 → robust; DSR < 0.5 → the "edge" is plausibly just the best draw from
the trial set. Pure numpy + scipy (already a hmmlearn dependency); no new deps.

References: Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014);
"The Probability of Backtest Overfitting" (2015).
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm, skew, kurtosis

_GAMMA = 0.5772156649015329  # Euler–Mascheroni


def expected_max_sharpe(trial_sharpes) -> float:
    """E[max Sharpe] under the null across N independent trials (per-period SR).

    Bailey–López de Prado closed form: sqrt(Var(SR)) · [(1-γ)·Z⁻¹(1-1/N) +
    γ·Z⁻¹(1-1/(N·e))]. Uses the cross-sectional variance of the trial Sharpes as
    the noise scale — so a tight field of Sharpes deflates less than a wide one.
    """
    sr = np.asarray([s for s in trial_sharpes if s is not None], dtype=float)
    n = sr.size
    if n < 2:
        return 0.0
    var_sr = float(sr.var(ddof=1))
    if var_sr <= 0:
        return 0.0
    e = np.e
    z1 = norm.ppf(1.0 - 1.0 / n)
    z2 = norm.ppf(1.0 - 1.0 / (n * e))
    return float(np.sqrt(var_sr) * ((1.0 - _GAMMA) * z1 + _GAMMA * z2))


def _psr(sr: float, sr_star: float, n_obs: int, sk: float, ku: float) -> float:
    """Probabilistic Sharpe Ratio: P(true per-period SR > sr_star)."""
    if n_obs < 3:
        return float("nan")
    # Non-normality adjustment (ku is the NON-excess kurtosis; 3 for a normal).
    denom = np.sqrt(max(1e-12, 1.0 - sk * sr + (ku - 1.0) / 4.0 * sr * sr))
    return float(norm.cdf((sr - sr_star) * np.sqrt(n_obs - 1) / denom))


def deflated_sharpe(seg_returns, trial_sharpes) -> dict:
    """Deflate a variant's Sharpe by the N-trial expected-max benchmark.

    `seg_returns` — the variant's per-rebalance returns (the thing under test).
    `trial_sharpes` — per-period Sharpes of EVERY variant in the tournament (the
    multiple-comparison set). Returns {dsr, observed_sr, expected_max_sr,
    n_trials, n_obs}; dsr is NaN when there are too few rebalances to judge.
    """
    r = np.asarray([x for x in seg_returns if x is not None], dtype=float)
    n_obs = r.size
    out = {"dsr": float("nan"), "observed_sr": float("nan"),
           "expected_max_sr": 0.0,
           "n_trials": len([s for s in trial_sharpes if s is not None]),
           "n_obs": n_obs}
    if n_obs < 3:
        return out
    sd = float(r.std(ddof=1))
    if sd < 1e-12:
        return out
    sr = float(r.mean()) / sd            # per-period Sharpe
    sk = float(skew(r))
    ku = float(kurtosis(r, fisher=False))  # non-excess
    sr_star = expected_max_sharpe(trial_sharpes)
    out.update(observed_sr=sr, expected_max_sr=sr_star,
               dsr=_psr(sr, sr_star, n_obs, sk, ku))
    return out


def per_period_sharpe(seg_returns) -> float | None:
    """Per-period (NOT annualised) Sharpe — the unit the DSR machinery expects."""
    r = np.asarray([x for x in seg_returns if x is not None], dtype=float)
    if r.size < 2:
        return None
    sd = float(r.std(ddof=1))
    if sd < 1e-12:
        return None
    return float(r.mean()) / sd


__all__ = ["deflated_sharpe", "expected_max_sharpe", "per_period_sharpe"]
