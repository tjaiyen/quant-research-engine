"""A/B experiment: composite (live) vs return-based regime labeling.

Builds the experimental return-mode panel, runs the tournament on both panels,
and prints the comparison for the DEFAULT strategy: total return, vs SPY, and
the up/down-quarter split (does the less-defensive variant beat SPY in the bull
while keeping downside protection?). Does NOT change the live default.
"""
import json
import os
from pathlib import Path

import utils.config  # noqa: F401 — load .env

from screener.tournament.attribution import attribute
from screener.tournament.panel import build_signal_panel
from screener.tournament.run import run_tournament
from screener.tournament.variants import default_variants

ROOT = Path(__file__).resolve().parent
BASELINE = ROOT / "store" / "tournament_panel.json"            # composite (live)
EXPERIMENT = ROOT / "store" / "tournament_panel_return.json"   # return mode


def _summary(panel: dict) -> dict:
    tour = run_tournament(panel, default_variants())
    attr = attribute(tour, panel)
    res = {r["label"]: r for r in tour["results"]}
    d = res["Regime-blended (default)"]["full"]
    dirn = attr.get("direction", {})
    return {
        "winner": attr.get("winner"),
        "default_total": d.get("total_return"),
        "default_vs_spy": d.get("excess"),
        "up": dirn.get("up", {}),
        "down": dirn.get("down", {}),
        "ranking_has_signal": attr.get("ranking_has_signal"),
        "character": attr.get("character"),
    }


def main() -> None:
    print("Loading BASELINE panel (composite / live regime)…")
    base = json.loads(BASELINE.read_text())

    print("Building EXPERIMENT panel (return-mode regime; ~15-25 min)…")
    os.environ["REGIME_LABEL_MODE"] = "return"
    exp = build_signal_panel(years=base["key"]["years"],
                             rebalance=base["key"]["rebalance"],
                             max_per_sector=base["key"]["max_per_sector"],
                             cache_path=EXPERIMENT)

    b, e = _summary(base), _summary(exp)
    print("\n================ A/B: DEFAULT STRATEGY ================")
    for name, s in (("COMPOSITE (live)", b), ("RETURN (experiment)", e)):
        up, dn = s["up"], s["down"]
        print(f"\n[{name}]  winner={s['winner']}")
        print(f"  default total: {s['default_total']*100:+.1f}%   vs SPY: {s['default_vs_spy']*100:+.1f}%")
        if up.get("n"):
            print(f"  UP   quarters (n={up['n']}): excess {up['excess']*100:+.2f}%/q")
        if dn.get("n"):
            print(f"  DOWN quarters (n={dn['n']}): excess {dn['excess']*100:+.2f}%/q")
        print(f"  ranking_has_signal: {s['ranking_has_signal']}")
    print("\n================ VERDICT ================")
    print(f"  default total return:  composite {b['default_total']*100:+.1f}%  →  return {e['default_total']*100:+.1f}%")
    print(f"  default vs SPY:        composite {b['default_vs_spy']*100:+.1f}%  →  return {e['default_vs_spy']*100:+.1f}%")
    du = e["up"].get("excess", 0) - b["up"].get("excess", 0)
    dd = e["down"].get("excess", 0) - b["down"].get("excess", 0)
    print(f"  up-quarter excess Δ:   {du*100:+.2f}%/q   down-quarter excess Δ: {dd*100:+.2f}%/q")


if __name__ == "__main__":
    main()
