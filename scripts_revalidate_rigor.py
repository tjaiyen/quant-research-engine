"""Re-validate the live ARIMA+Sharpe candidate under the rigor cluster (Phase 15).

Does the edge survive (1) transaction costs, (2) the Deflated Sharpe multiple-
comparisons correction, and (3) CPCV out-of-sample spread? One-off harness over
the cached panel; prints a plain verdict. No live default change here — that
decision is surfaced from this output.
"""
from __future__ import annotations

import json
import os

from screener.tournament.variants import default_variants
from screener.tournament.run import run_tournament
from screener.rigor.stats import deflated_sharpe, per_period_sharpe
from screener.rigor.cpcv import cpcv_splits, cpcv_distribution

PANEL = os.path.join("store", "tournament_panel.json")
CAND = {"arima": 0.62, "sharpe": 0.38}   # the live candidate weighting


def _cand_variant():
    return {"label": "CANDIDATE ARIMA+Sharpe", "group": "weighting",
            "kind": "strategy", "weights": CAND, "guards": True,
            "top_n": 2, "sizing": "equal", "pick": "top"}


def main() -> None:
    with open(PANEL) as f:
        panel = json.load(f)
    print(f"panel: {len(panel.get('segments', []))} segments, "
          f"{len(panel.get('rows', []))} rows, rebalance={panel.get('rebalance')}")

    variants = default_variants() + [_cand_variant()]

    print("\n=== Transaction-cost stress (net total return, full window) ===")
    print(f"{'cost_bps':>8} | {'CANDIDATE':>10} {'default':>10} {'SPY':>8} "
          f"{'cand-SPY':>9} {'cand-deflt':>10}")
    rows_by_cost = {}
    for bps in (0, 10, 20, 40):
        res = run_tournament(panel, variants, cost_bps=bps)
        rows_by_cost[bps] = res
        by = {r["label"]: r for r in res["results"]}
        c = by["CANDIDATE ARIMA+Sharpe"]["full"]
        d = by["Regime-blended (default)"]["full"]
        spy = by["SPY buy-hold"]["full"]
        print(f"{bps:>8} | {c['total_return']*100:>9.1f}% "
              f"{d['total_return']*100:>9.1f}% {spy['total_return']*100:>7.1f}% "
              f"{(c['total_return']-spy['total_return'])*100:>8.1f}% "
              f"{(c['total_return']-d['total_return'])*100:>9.1f}%")

    print("\n=== Out-of-sample (held-out third), net of 20bps ===")
    res20 = rows_by_cost[20]
    by = {r["label"]: r for r in res20["results"]}
    for lbl in ("CANDIDATE ARIMA+Sharpe", "Regime-blended (default)",
                "SPY buy-hold", "Random 20 (seed)"):
        m = by[lbl]["out_sample"]
        print(f"  {lbl:>26}: OOS total {m['total_return']*100:>6.1f}%  "
              f"excess {m['excess']*100:>6.1f}%  Sharpe "
              f"{(m['sharpe'] if m['sharpe'] is not None else float('nan')):>5.2f}")

    print("\n=== Deflated Sharpe (multiple-comparison correction, net 20bps) ===")
    # Per-period Sharpes across ALL variants = the trial set.
    trial_sr = [per_period_sharpe(r["seg_returns"]) for r in res20["results"]]
    cand_seg = by["CANDIDATE ARIMA+Sharpe"]["seg_returns"]
    dsr = deflated_sharpe(cand_seg, trial_sr)
    print(f"  observed per-period SR : {dsr['observed_sr']:.3f}")
    print(f"  expected-max SR (null) : {dsr['expected_max_sr']:.3f}  "
          f"(n_trials={dsr['n_trials']})")
    print(f"  DSR = P(true SR>0 after deflation) : {dsr['dsr']:.3f}  "
          f"(n_obs={dsr['n_obs']})")

    print("\n=== CPCV out-of-sample distribution (net 20bps) ===")
    spy_rets = [s.get("spy_return") or 0.0 for s in panel["segments"]]
    splits = cpcv_splits(len(cand_seg), n_groups=6, k_test=2, embargo=1)
    dist = cpcv_distribution(cand_seg, spy_rets, splits)
    print(f"  folds={dist['n_folds']}  mean excess-vs-SPY "
          f"{dist['mean_excess']*100:.1f}%  std {dist['std_excess']*100:.1f}%  "
          f"frac_positive {dist['frac_positive']*100:.0f}%")

    print("\n=== VERDICT ===")
    c20 = by["CANDIDATE ARIMA+Sharpe"]["full"]["total_return"]
    spy20 = by["SPY buy-hold"]["full"]["total_return"]
    d20 = by["Regime-blended (default)"]["full"]["total_return"]
    # OOS-vs-random is the honest bar (the plan's controls): beating SPY is weak
    # when even random beat SPY this window.
    cand_oos = by["CANDIDATE ARIMA+Sharpe"]["out_sample"]["total_return"]
    rand_oos = by["Random 20 (seed)"]["out_sample"]["total_return"]
    survives_cost = c20 > spy20 and c20 > d20
    survives_dsr = (dsr["dsr"] == dsr["dsr"]) and dsr["dsr"] >= 0.95
    survives_cpcv = (dist["frac_positive"] == dist["frac_positive"]
                     and dist["frac_positive"] >= 0.6 and dist["mean_excess"] > 0)
    beats_random = cand_oos > rand_oos
    margin = (cand_oos - rand_oos) * 100
    print(f"  survives costs (beats SPY & default net 20bps): {survives_cost}")
    print(f"  survives DSR (>=0.95)                          : {survives_dsr}")
    print(f"  survives CPCV (mean>0 & >=60% folds positive)  : {survives_cpcv}")
    print(f"  beats RANDOM control OOS                        : {beats_random} "
          f"(by {margin:+.1f}pp — thin if small)")
    full_pass = survives_cost and survives_dsr and survives_cpcv and beats_random
    verdict = ("SURVIVES the rigor cluster — keep candidate live (moderate "
               "confidence: thin margin over random, favourable window)" if full_pass
               else "DOES NOT fully survive — review / consider reverting to current")
    print(f"  >>> {verdict}")


if __name__ == "__main__":
    main()
