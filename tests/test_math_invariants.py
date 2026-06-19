"""Five core math invariants. If any of these break, scoring is wrong.

Run:
    pytest tests/test_math_invariants.py -v
    pytest tests/test_math_invariants.py -v -k normalize_div_yield   # one test
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from fundamental import _normalize_div_yield, compute_dcf, compute_ddm
from models_portfolio import (
    _simulate_from_stats,
    compute_position_attributions,
    compute_risk_contributions,
    simulate_portfolio,
)
from scoring_ars import compute_ars
from scoring_legacy import score_ticker
from models_technical import compute_technical
from models_quant import compute_risk
from models_fundamental import build_peer_context, compute_valuation
from utils.db import (
    fetch_latest_fundamentals,
    fetch_prices,
    init_db,
    list_tickers,
)


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_db():
    init_db()


# ----------------------------------------------------------------------------
# Invariant 1: legacy CompositeScore values must lie in [0, 1].
# ----------------------------------------------------------------------------
def test_legacy_composite_score_in_unit_interval():
    syms = list_tickers()["symbol"].tolist()
    if not syms:
        pytest.skip("no tickers in DB; run tasks.refresh_prices first")

    snapshots = {s: (fetch_latest_fundamentals(s) or {}) for s in syms}
    peer_ctx = build_peer_context(snapshots)

    for sym in syms:
        df = fetch_prices(sym)
        if df.empty:
            continue
        t = compute_technical(df)
        r = compute_risk(df)
        v = compute_valuation(snapshots.get(sym) or None, peer_context=peer_ctx)
        s = score_ticker(sym, t, r, v)
        assert 0.0 <= s.technical <= 1.0, f"{sym} technical={s.technical}"
        assert 0.0 <= s.risk <= 1.0, f"{sym} risk={s.risk}"
        if s.valuation is not None:
            assert 0.0 <= s.valuation <= 1.0, f"{sym} valuation={s.valuation}"
        assert 0.0 <= s.composite <= 1.0, f"{sym} composite={s.composite}"
        assert s.bucket in ("top_candidate", "watch", "avoid"), (
            f"{sym} bad bucket: {s.bucket}"
        )


# ----------------------------------------------------------------------------
# Invariant 2: ARS composite values must lie in [0, 100].
# ----------------------------------------------------------------------------
def test_ars_composite_in_zero_to_hundred():
    syms = list_tickers()["symbol"].tolist()
    if not syms:
        pytest.skip("no tickers in DB")
    for sym in syms:
        a = compute_ars(sym)
        assert a is not None, f"compute_ars returned None for {sym}"
        assert 0.0 <= a.composite <= 100.0, f"{sym} composite={a.composite}"
        assert a.bucket in ("strong_buy", "buy", "hold", "reduce", "avoid"), (
            f"{sym} bad bucket: {a.bucket}"
        )
        for c in a.components:
            if c.score == c.score:  # not NaN
                assert 0.0 <= c.score <= 100.0, f"{sym} {c.name}={c.score}"


# ----------------------------------------------------------------------------
# Invariant 3: Component CVaR sums to portfolio CVaR (Euler identity).
# ----------------------------------------------------------------------------
def test_component_cvar_euler_identity():
    sim = simulate_portfolio(horizon_days=21, n_sims=2000, lookback_days=252)
    if sim is None:
        pytest.skip("portfolio simulation unavailable (no holdings or no data)")
    contribs = compute_risk_contributions(sim)
    assert contribs, "compute_risk_contributions returned empty list"
    total = sum(c.component_cvar for c in contribs)
    rel_err = abs(total - sim.cvar_95) / max(sim.cvar_95, 1.0)
    assert rel_err < 1e-6, (
        f"Component CVaR {total:.6f} != portfolio CVaR {sim.cvar_95:.6f} "
        f"(rel_err={rel_err:.2e})"
    )
    # Contribution percentages must sum to 1.
    pct_sum = sum(c.contribution_pct for c in contribs)
    assert abs(pct_sum - 1.0) < 1e-6, f"contribution_pct sum = {pct_sum}"


# ----------------------------------------------------------------------------
# Invariant 4: SPY self-regression — beta≈1, alpha≈0, R²≈1.
# Built-in unit test on the CAPM regression; if math breaks, this fails.
# ----------------------------------------------------------------------------
def test_spy_self_regression():
    attrs = compute_position_attributions(benchmark="SPY", lookback_days=252)
    if not attrs:
        pytest.skip("attribution unavailable (no holdings)")
    spy = next((a for a in attrs if a.ticker == "SPY"), None)
    if spy is None:
        pytest.skip("SPY not in current holdings")
    assert abs(spy.beta - 1.0) < 0.01, f"SPY beta={spy.beta}, expected ~1.0"
    assert abs(spy.alpha_ann) < 0.005, f"SPY alpha_ann={spy.alpha_ann}, expected ~0"
    assert spy.r_squared > 0.999, f"SPY R²={spy.r_squared}, expected ~1.0"


# ----------------------------------------------------------------------------
# Invariant 5: _normalize_div_yield disambiguates decimal vs percent.
# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        (0.005, 0.005),    # decimal in correct form: pass through
        (0.5, 0.005),      # percent stored as number: divide by 100
        (50.0, 0.5),       # extreme percent: /100 = 50% (still possible for synthetic)
        (None, None),
        (0, None),
        (-0.01, None),
    ],
)
def test_normalize_div_yield(raw, expected):
    out = _normalize_div_yield(raw)
    if expected is None:
        assert out is None, f"_normalize_div_yield({raw}) = {out}, expected None"
    else:
        assert math.isclose(out, expected, rel_tol=1e-9, abs_tol=1e-9), (
            f"_normalize_div_yield({raw}) = {out}, expected {expected}"
        )


# ----------------------------------------------------------------------------
# Bonus invariant 6: deterministic Monte Carlo (seed=42).
# Same inputs => same outputs across runs.
# ----------------------------------------------------------------------------
def test_simulate_portfolio_determinism():
    a = simulate_portfolio(horizon_days=21, n_sims=500, lookback_days=252)
    b = simulate_portfolio(horizon_days=21, n_sims=500, lookback_days=252)
    if a is None or b is None:
        pytest.skip("simulate_portfolio unavailable (no holdings or no data)")
    assert a.var_95 == b.var_95, f"VaR drift: {a.var_95} vs {b.var_95}"
    assert a.cvar_95 == b.cvar_95, f"CVaR drift: {a.cvar_95} vs {b.cvar_95}"
    assert np.array_equal(a.sim_paths, b.sim_paths), "sim_paths drifted"


# ----------------------------------------------------------------------------
# Bonus invariant 7: DCF terminal-growth cap.
# When WACC <= terminal_growth, DCF must auto-cap (not blow up).
# ----------------------------------------------------------------------------
def test_dcf_unstable_wacc_capped():
    # WACC=2%, terminal=2.5% → unstable; should cap and warn
    r = compute_dcf(
        base_fcf=1_000_000, shares_out=1_000_000, sector="Utilities",
        wacc=0.02, terminal_growth=0.025, current_price=100,
    )
    assert r.intrinsic_per_share is not None, "DCF returned None on unstable WACC"
    assert r.intrinsic_per_share > 0, "DCF returned non-positive intrinsic"
    assert any("WACC" in w and "terminal" in w for w in r.warnings), (
        f"Expected WACC<terminal warning, got: {list(r.warnings)}"
    )


# ----------------------------------------------------------------------------
# Bonus invariant 8: DDM zero-dividend skip.
# ----------------------------------------------------------------------------
def test_ddm_zero_dividend_skip():
    r = compute_ddm(
        last_dividend_per_share=0.0, div_yield=0.0,
        sector="Technology", current_price=100,
    )
    assert r.intrinsic_per_share is None, (
        f"DDM should skip zero-dividend; got {r.intrinsic_per_share}"
    )
    assert any("no dividend" in w for w in r.warnings)


# ----------------------------------------------------------------------------
# Bonus invariant 9: ARS bucket thresholds can be overridden via user_settings.
# ----------------------------------------------------------------------------
def test_glossary_completeness():
    """Every glossary entry must have all four required fields filled in."""
    from glossary import GLOSSARY, Term
    assert len(GLOSSARY) >= 25, f"Expected ≥25 terms, got {len(GLOSSARY)}"
    for key, term in GLOSSARY.items():
        assert isinstance(term, Term)
        assert term.short and len(term.short) > 10, f"{key}: short def too brief"
        assert term.full and len(term.full) > 80, f"{key}: full explanation too brief"
        assert term.matters, f"{key}: missing 'matters' field"
        assert term.watch, f"{key}: missing 'watch' field"
    # Critical terms must be present.
    must_have = {"dcf", "var", "cvar", "beta", "alpha", "sharpe", "ars",
                 "rrg", "trim_signal", "add_signal"}
    missing = must_have - set(GLOSSARY.keys())
    assert not missing, f"Glossary missing critical terms: {missing}"


# NOTE: test_narratives_render_without_crashing and
# test_explainer_components_handle_unknown_terms were removed in the
# Obsidian-native rebuild — they exercised the deleted Dash UI layer
# (ui.narratives / ui.explainers). The narrative content itself is now
# rendered as Markdown by render/ and covered by its own tests.


def test_ars_threshold_override_via_user_settings():
    """User-set thresholds in user_settings table take precedence over const defaults."""
    from utils.db import set_setting, delete_setting
    from scoring_ars import _bucket_for

    delete_setting("ars_thresholds")
    # Default thresholds: 75/60/45/30
    assert _bucket_for(80) == "strong_buy"
    assert _bucket_for(50) == "hold"

    # Override: bump strong_buy floor to 90
    set_setting("ars_thresholds", {"strong_buy": 90, "buy": 70, "hold": 50, "reduce": 30})
    try:
        assert _bucket_for(80) == "buy", "80 should drop to 'buy' under stricter threshold"
        assert _bucket_for(95) == "strong_buy"
        assert _bucket_for(45) == "reduce", "45 should drop to 'reduce' (hold floor is now 50)"
    finally:
        delete_setting("ars_thresholds")
    # Verify cleanup restores default behavior
    assert _bucket_for(80) == "strong_buy"
