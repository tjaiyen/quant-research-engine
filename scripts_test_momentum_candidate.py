"""Does ADDING momentum to the live ARIMA+Sharpe candidate improve the portfolio
out-of-sample, net of costs? Momentum's rank-IC is weak but its quintile spread is
the best of the set + it should decorrelate from mean-reverting ARIMA — so it may
help the top-pick portfolio more than its IC implies. IC-gate by OOS, not by hope.
"""
from __future__ import annotations

import json
import os

from screener.signal_lab.lab import analyze_signals
from screener.tournament.variants import default_variants
from screener.tournament.run import run_tournament
from screener.rigor.stats import deflated_sharpe, per_period_sharpe
from screener.rigor.cpcv import cpcv_splits, cpcv_distribution

PANEL = os.path.join("store", "tournament_panel.json")
LIVE = {"arima": 0.62, "sharpe": 0.38}                  # current live candidate


def _variant(label, weights):
    return {"label": label, "group": "weighting", "kind": "strategy",
            "weights": weights, "guards": True, "top_n": 2,
            "sizing": "equal", "pick": "top"}


def main() -> None:
    panel = json.load(open(PANEL))
    a = analyze_signals(panel)["signals"]

    # Correlation of momentum to the live candidate's signals (diversification).
    corr = analyze_signals(panel)["correlation"]
    print("momentum correlation: "
          f"arima {corr['momentum']['arima']:+.2f}, "
          f"sharpe {corr['momentum']['sharpe']:+.2f}")

    # Candidate-with-momentum: weights ∝ positive IC across {arima, sharpe, momentum}.
    ics = {s: max(0.0, a[s]["ic"] or 0.0) for s in ("arima", "sharpe", "momentum")}
    tot = sum(ics.values())
    cand_mom = {s: round(v / tot, 3) for s, v in ics.items()}
    print(f"\ncandidate+momentum weights (∝ IC): {cand_mom}")

    variants = default_variants() + [
        _variant("LIVE ARIMA+Sharpe", LIVE),
        _variant("CAND +momentum", cand_mom),
    ]
    res = run_tournament(panel, variants, cost_bps=20)
    by = {r["label"]: r for r in res["results"]}

    print(f"\n{'variant':>22} | {'full':>7} {'OOS':>7} {'OOS-excess':>10} "
          f"{'OOS-Sharpe':>10} {'DSR':>6} {'CPCV+%':>7}")
    spy = [s.get("spy_return") or 0.0 for s in panel["segments"]]
    trial_sr = [per_period_sharpe(r["seg_returns"]) for r in res["results"]]
    for lbl in ("LIVE ARIMA+Sharpe", "CAND +momentum",
                "Regime-blended (default)", "SPY buy-hold", "Random 20 (seed)"):
        r = by[lbl]; f, o = r["full"], r["out_sample"]
        dsr = deflated_sharpe(r["seg_returns"], trial_sr)["dsr"]
        splits = cpcv_splits(len(r["seg_returns"]), n_groups=6, k_test=2, embargo=1)
        cp = cpcv_distribution(r["seg_returns"], spy, splits)["frac_positive"]
        osh = o["sharpe"]
        print(f"{lbl:>22} | {f['total_return']*100:>6.1f}% {o['total_return']*100:>6.1f}% "
              f"{o['excess']*100:>9.1f}% "
              f"{(osh if osh is not None else float('nan')):>10.2f} "
              f"{dsr:>6.3f} {cp*100:>6.0f}%")

    live = by["LIVE ARIMA+Sharpe"]["out_sample"]["total_return"]
    mom = by["CAND +momentum"]["out_sample"]["total_return"]
    print(f"\n=== VERDICT ===")
    print(f"  adding momentum changes OOS total by {(mom-live)*100:+.1f}pp "
          f"({live*100:.1f}% → {mom*100:.1f}%)")
    print("  >>> "
          + ("PROMOTE — momentum improves the OOS portfolio" if mom > live + 0.005
             else "DO NOT promote — momentum doesn't improve the OOS portfolio; "
                  "keep it measured-only"))


if __name__ == "__main__":
    main()
