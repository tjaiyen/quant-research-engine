"""Rigor-cluster tests (Phase 15): transaction costs, Deflated Sharpe, CPCV,
multiple-testing-corrected signal significance.
"""
from __future__ import annotations

import math

from screener.rigor.costs import turnover, cost_haircut
from screener.rigor.stats import (deflated_sharpe, expected_max_sharpe,
                                   per_period_sharpe)
from screener.rigor.cpcv import cpcv_splits, cpcv_distribution


# ── U27 transaction costs ────────────────────────────────────────────────────

def test_turnover_fraction_newly_bought():
    assert turnover([], ["A", "B"]) == 1.0            # first buy = full turnover
    assert turnover(["A", "B"], ["A", "B"]) == 0.0    # unchanged = no turnover
    assert turnover(["A", "B"], ["B", "C"]) == 0.5    # one of two names new
    assert turnover(["A"], []) == 0.0                 # empty book = no cost


def test_cost_haircut_scales_with_turnover_and_bps():
    # full-turnover rebalance at 20bps → 0.0020 drag
    assert math.isclose(cost_haircut([], ["A", "B"], 20), 0.0020)
    # half turnover → half the drag
    assert math.isclose(cost_haircut(["A", "B"], ["B", "C"], 20), 0.0010)
    # zero bps = frictionless (legacy behaviour preserved)
    assert cost_haircut([], ["A", "B"], 0) == 0.0


# ── U26 Deflated Sharpe ──────────────────────────────────────────────────────

def test_expected_max_rises_with_more_trials():
    few = expected_max_sharpe([0.1, 0.2, 0.3])
    many = expected_max_sharpe([0.1, 0.2, 0.3] * 10)   # same spread, more trials
    assert many > few > 0                              # luckier max with more draws


def test_deflated_sharpe_drops_as_trials_grow():
    # A genuinely good return stream; deflating against more trials must lower DSR.
    rets = [0.04, 0.05, 0.03, 0.06, 0.04, 0.05, 0.04, 0.05]
    trials_few = [0.2, 0.4, 0.6]
    trials_many = [0.2, 0.4, 0.6, 0.5, 0.3, 0.55, 0.45, 0.35, 0.25, 0.65]
    dsr_few = deflated_sharpe(rets, trials_few)["dsr"]
    dsr_many = deflated_sharpe(rets, trials_many)["dsr"]
    assert 0.0 <= dsr_many <= dsr_few <= 1.0


def test_deflated_sharpe_too_few_obs_is_nan():
    out = deflated_sharpe([0.01, 0.02], [0.1, 0.2, 0.3])
    assert out["dsr"] != out["dsr"]                    # NaN
    assert per_period_sharpe([0.01]) is None


# ── U25 CPCV ─────────────────────────────────────────────────────────────────

def test_cpcv_splits_are_purged_and_disjoint():
    splits = cpcv_splits(12, n_groups=6, k_test=2, embargo=1)
    assert splits, "expected non-empty splits for 12 segments"
    for sp in splits:
        train, test = set(sp["train"]), set(sp["test"])
        assert not (train & test)                     # disjoint
        # embargo: no train index sits directly adjacent to a test index
        for ti in test:
            assert ti - 1 not in train and ti + 1 not in train


def test_cpcv_too_few_segments_empty():
    assert cpcv_splits(3) == []


def test_cpcv_distribution_shape():
    seg = [0.05, -0.02, 0.03, 0.01, 0.04, -0.01, 0.02, 0.03]
    spy = [0.01] * len(seg)
    splits = cpcv_splits(len(seg), n_groups=4, k_test=1, embargo=1)
    dist = cpcv_distribution(seg, spy, splits)
    assert dist["n_folds"] == len(splits) > 0
    assert 0.0 <= dist["frac_positive"] <= 1.0
    assert dist["folds"] and all("excess" in f for f in dist["folds"])


# ── U29 permutation max-DD + bootstrap Sharpe CI ─────────────────────────────

def test_permutation_deterministic_and_bounded():
    from screener.rigor.resample import permutation_test
    rets = [0.03, -0.02, 0.04, -0.05, 0.02, 0.01, -0.03, 0.05, 0.02, -0.01]
    a = permutation_test(rets, n_sims=200, seed=7)
    b = permutation_test(rets, n_sims=200, seed=7)
    assert a == b                                      # seeded → reproducible
    assert 0.0 <= a["p_value_max_dd"] <= 1.0
    assert a["observed_max_dd"] <= 0.0
    assert a["n_obs"] == len(rets)


def test_permutation_monotone_winner_has_benign_path():
    # All-positive returns → zero drawdown in EVERY ordering, so every
    # permutation draws down "at least as badly" (equally) → p = 1.0.
    from screener.rigor.resample import permutation_test
    out = permutation_test([0.02, 0.03, 0.01, 0.04, 0.02, 0.03], n_sims=100)
    assert out["observed_max_dd"] == 0.0
    assert out["p_value_max_dd"] == 1.0


def test_permutation_front_loaded_crash_is_flagged():
    # Losses clustered up front → realised DD worse than most reshuffles → small p.
    from screener.rigor.resample import permutation_test
    rets = [-0.10, -0.10, -0.10] + [0.02] * 12
    out = permutation_test(rets, n_sims=500, seed=3)
    assert out["p_value_max_dd"] < 0.5                 # not benign
    # and the observed DD equals the compounded front-loss (≈ -27.1%)
    assert abs(out["observed_max_dd"] - (0.9 ** 3 - 1)) < 1e-9


def test_permutation_too_few_segments_errors():
    from screener.rigor.resample import permutation_test
    out = permutation_test([0.01, 0.02])
    assert "error" in out and "p_value_max_dd" not in out


def test_bootstrap_ci_contains_observed_and_is_deterministic():
    from screener.rigor.resample import bootstrap_sharpe_ci
    rets = [0.04, -0.01, 0.03, 0.02, -0.02, 0.05, 0.01, 0.03, -0.01, 0.02]
    a = bootstrap_sharpe_ci(rets, n_boot=300, seed=11)
    b = bootstrap_sharpe_ci(rets, n_boot=300, seed=11)
    assert a == b
    assert a["ci_lower"] <= a["observed_sharpe"] <= a["ci_upper"]
    assert 0.0 <= a["prob_positive"] <= 1.0


def test_bootstrap_degenerate_inputs_error():
    from screener.rigor.resample import bootstrap_sharpe_ci
    assert "error" in bootstrap_sharpe_ci([0.01, 0.02, 0.03])          # too few
    assert "error" in bootstrap_sharpe_ci([0.01] * 10)                 # zero var


# ── U28 multiple-testing-corrected signal significance ───────────────────────

def test_signal_significance_flag_uses_corrected_threshold():
    from screener.signal_lab.lab import _bonferroni_threshold, _ic_significant
    thr = _bonferroni_threshold(5)                    # ≈ 2.576
    assert 2.5 < thr < 2.7
    assert _ic_significant(3.0, thr) is True          # clears the bar
    assert _ic_significant(2.1, thr) is False         # suggestive, not significant
    assert _ic_significant(-3.0, thr) is True         # magnitude, not sign
    assert _ic_significant(None, thr) is None
