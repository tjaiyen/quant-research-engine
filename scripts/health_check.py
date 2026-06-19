"""End-to-end health check.

Runs every layer of the pipeline against the current DB state and makes
assertions that encode our numerical governance. Exits non-zero on any
failure so this can be wired into a cron check later.

Usage:
    python scripts/health_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly via `python scripts/health_check.py`.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------- Check implementations ----------

def _ok(msg: str) -> None:
    print(f"  \u2713 {msg}")


def _fail(msg: str) -> None:
    print(f"  \u2717 {msg}")
    raise AssertionError(msg)


def check_imports() -> None:
    print("\n[1/6] Module imports")
    import scoring_legacy  # noqa: F401
    import suggestions  # noqa: F401
    import models_technical  # noqa: F401
    import models_quant  # noqa: F401
    import models_fundamental  # noqa: F401
    import models_portfolio  # noqa: F401
    from data_providers import yfinance_provider  # noqa: F401
    from utils import config, db, logging_setup  # noqa: F401
    _ok("all modules import cleanly")


def check_db() -> None:
    print("\n[2/6] Database state")
    from utils.db import init_db, list_tickers, list_holdings
    init_db()
    tickers = list_tickers()
    if tickers.empty:
        _fail("no tickers in DB — run tasks.refresh_prices first")
    _ok(f"{len(tickers)} tickers in DB")
    holdings = list_holdings()
    _ok(f"{len(holdings)} holdings")


def check_signals() -> None:
    print("\n[3/6] Per-ticker signals (first ticker)")
    from utils.db import fetch_prices, list_tickers, fetch_latest_fundamentals
    from models_technical import compute_technical
    from models_quant import compute_risk
    from models_fundamental import compute_valuation

    ticker = list_tickers()["symbol"].iloc[0]
    df = fetch_prices(ticker)
    if df.empty:
        _fail(f"no price data for {ticker}")

    t = compute_technical(df)
    r = compute_risk(df)
    v = compute_valuation(fetch_latest_fundamentals(ticker))

    if t.trend_regime not in ("bullish", "neutral", "bearish"):
        _fail(f"bad trend regime: {t.trend_regime}")
    if r.risk_regime not in ("low", "moderate", "elevated", "high"):
        _fail(f"bad risk regime: {r.risk_regime}")
    if v.bucket not in ("attractive", "fair", "expensive", "no_data"):
        _fail(f"bad valuation bucket: {v.bucket}")
    _ok(f"{ticker} signals valid · trend={t.trend_regime}, risk={r.risk_regime}, val={v.bucket}")


def check_scoring() -> None:
    print("\n[4/6] Scoring range + properties")
    from utils.db import fetch_prices, list_tickers, fetch_latest_fundamentals
    from models_technical import compute_technical
    from models_quant import compute_risk
    from models_fundamental import compute_valuation
    from scoring_legacy import score_ticker

    for sym in list_tickers()["symbol"].tolist():
        df = fetch_prices(sym)
        if df.empty:
            continue
        t = compute_technical(df)
        r = compute_risk(df)
        v = compute_valuation(fetch_latest_fundamentals(sym))
        s = score_ticker(sym, t, r, v)
        for name, val in (("technical", s.technical), ("risk", s.risk), ("composite", s.composite)):
            if not (0.0 <= val <= 1.0):
                _fail(f"{sym} {name}={val} out of [0,1]")
        if s.valuation is not None and not (0.0 <= s.valuation <= 1.0):
            _fail(f"{sym} valuation={s.valuation} out of [0,1]")
        if s.bucket not in ("top_candidate", "watch", "avoid"):
            _fail(f"{sym} bad bucket: {s.bucket}")
    _ok("all composite scores in [0,1], buckets valid")


def check_portfolio() -> None:
    print("\n[5/6] Portfolio aggregation + simulation")
    from models_portfolio import (
        build_portfolio,
        simulate_portfolio,
        compute_risk_contributions,
        compute_benchmark_comparison,
        compute_position_attributions,
    )
    s = build_portfolio()
    if s.name_count == 0:
        _ok("no holdings — skipping portfolio checks")
        return

    # Weights sum ≈ 1.
    total_w = sum((p.weight or 0) for p in s.positions)
    if abs(total_w - 1.0) > 1e-6:
        _fail(f"weights sum to {total_w}, expected 1.0")
    _ok(f"weights sum to 1.0 (ε={abs(total_w-1.0):.2e})")

    sim = simulate_portfolio(horizon_days=21, n_sims=2000)  # smaller n for speed
    if sim is None:
        _fail("simulation returned None")
    # CVaR > VaR95, VaR99 > VaR95.
    if not (sim.cvar_95 > sim.var_95):
        _fail(f"CVaR {sim.cvar_95} not > VaR95 {sim.var_95}")
    if not (sim.var_99 > sim.var_95):
        _fail(f"VaR99 {sim.var_99} not > VaR95 {sim.var_95}")
    _ok(f"VaR95={sim.var_pct_95*100:.2f}% < CVaR95={sim.cvar_pct_95*100:.2f}%, VaR99 > VaR95 ✓")

    # Component CVaR Euler identity.
    contribs = compute_risk_contributions(sim)
    total_c = sum(c.component_cvar for c in contribs)
    if abs(total_c - sim.cvar_95) / max(sim.cvar_95, 1.0) > 0.005:
        _fail(f"Σ component CVaR {total_c} ≠ portfolio CVaR {sim.cvar_95}")
    _ok(f"Component CVaR sums to portfolio CVaR (Euler identity holds)")

    # Benchmark comparison exists.
    bench = compute_benchmark_comparison(benchmark="SPY", lookback_days=252)
    if bench is None:
        _fail("benchmark comparison returned None (need SPY in DB)")
    if not (0.0 <= bench.r_squared <= 1.0):
        _fail(f"R² out of range: {bench.r_squared}")
    _ok(f"Benchmark comp: portfolio {bench.portfolio_return*100:+.1f}% vs SPY {bench.benchmark_return*100:+.1f}%, β={bench.beta:.2f}")

    # Position attribution — SPY self-regression.
    attrs = compute_position_attributions(benchmark="SPY", lookback_days=252)
    spy_row = next((a for a in attrs if a.ticker == "SPY"), None)
    if spy_row is not None:
        if abs(spy_row.beta - 1.0) > 0.01:
            _fail(f"SPY self-regression beta={spy_row.beta}, expected ~1.0")
        if abs(spy_row.alpha_ann) > 0.005:
            _fail(f"SPY self-regression alpha={spy_row.alpha_ann}, expected ~0")
        if spy_row.r_squared < 0.999:
            _fail(f"SPY self-regression R²={spy_row.r_squared}, expected ~1.0")
        _ok("SPY self-regression unit test passes (β≈1, α≈0, R²≈1)")

    # Stress scenarios — every regime should produce VaR >= current baseline.
    from models_portfolio import compute_stress_scenarios
    stress = compute_stress_scenarios(horizon_days=21, n_sims=500)  # small n for speed
    baseline = next((r for r in stress if r.label == "Current" and r.status == "ok"), None)
    if baseline is None:
        _fail("Stress baseline 'Current' missing or failed")
    ok_regimes = [r for r in stress if r.status == "ok" and r.label != "Current"]
    if not ok_regimes:
        _fail("No historical stress regimes produced results")
    for r in ok_regimes:
        # Monotone sanity: stress regimes should have >= baseline VaR in %.
        if r.var_pct_95 < baseline.var_pct_95 * 0.8:
            _fail(f"{r.label} VaR {r.var_pct_95:.3f} suspiciously below current {baseline.var_pct_95:.3f}")
    _ok(f"Stress scenarios: {len(ok_regimes)} regimes, all VaR ≥ 0.8x baseline")


def check_suggestions() -> None:
    print("\n[6/6] Suggestions engine")
    from utils.db import fetch_prices, list_tickers, list_holdings, fetch_latest_fundamentals
    from models_technical import compute_technical
    from models_quant import compute_risk
    from models_fundamental import compute_valuation
    from models_portfolio import simulate_portfolio, compute_risk_contributions
    from scoring_legacy import score_ticker
    from suggestions import build_trim_candidates, build_add_candidates

    scores = []
    for sym in list_tickers()["symbol"].tolist():
        df = fetch_prices(sym)
        if df.empty:
            continue
        t = compute_technical(df)
        r = compute_risk(df)
        v = compute_valuation(fetch_latest_fundamentals(sym))
        scores.append(score_ticker(sym, t, r, v))

    held = set(list_holdings()["ticker"].tolist()) if not list_holdings().empty else set()

    trims = []
    if held:
        sim = simulate_portfolio(horizon_days=21, n_sims=2000)
        if sim is not None:
            contribs = compute_risk_contributions(sim)
            trims = build_trim_candidates(scores, contribs, held)

    adds = build_add_candidates(scores, held)

    # Intensities must be in [0, 1].
    for t in trims + adds:
        if not (0.0 <= t.intensity <= 1.0):
            _fail(f"intensity out of [0,1]: {t}")
    _ok(f"trim candidates: {len(trims)}, add candidates: {len(adds)}")


# ---------- Runner ----------

CHECKS = [
    check_imports,
    check_db,
    check_signals,
    check_scoring,
    check_portfolio,
    check_suggestions,
]


def main() -> int:
    print("=" * 60)
    print("Quant Cockpit · Health Check")
    print("=" * 60)
    failures = 0
    for fn in CHECKS:
        try:
            fn()
        except AssertionError:
            failures += 1
        except Exception as e:
            print(f"  ! unexpected error in {fn.__name__}: {e}")
            failures += 1

    print("\n" + "=" * 60)
    if failures == 0:
        print("All checks passed.")
        return 0
    print(f"{failures} check(s) failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
