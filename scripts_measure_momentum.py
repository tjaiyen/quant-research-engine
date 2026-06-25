"""Enrich the cached signal panel with a causal 12-1 momentum column, then
IC-gate it through the signal-lab — WITHOUT touching the live WEIGHT_MATRIX.

Cheap by construction: momentum is price-only, so this re-slices the already-
cached price histories per (ticker, rebalance-date) — no ARIMA/GARCH re-run, no
full 15-25 min panel rebuild. Writes momentum back into store/tournament_panel.json
so `track signal-lab` measures it. Promotion into the composite is a SEPARATE,
flagged step taken only if momentum earns its place here.
"""
from __future__ import annotations

import json
import os

import pandas as pd

from screener.signals.momentum_signal import momentum_signal
from screener.signal_lab.lab import analyze_signals

PANEL = os.path.join("store", "tournament_panel.json")


def enrich(panel: dict) -> int:
    from screener.backtest.walk_forward import _ph_for, _slice_history_to
    universe = panel.get("universe") or sorted(
        {r["ticker"] for r in panel.get("rows", [])})
    histories = {t: _ph_for(t) for t in universe}
    n = 0
    for row in panel.get("rows", []):
        ph = histories.get(row["ticker"])
        if ph is None or ph.empty:
            continue
        ph_train = _slice_history_to(ph, pd.Timestamp(row["d0"]))
        score = momentum_signal(row["ticker"], ph_train).get("score")
        if score is not None:
            (row.setdefault("signals", {}))["momentum"] = float(score)
            n += 1
    return n


def main() -> None:
    with open(PANEL) as f:
        panel = json.load(f)
    print(f"panel: {len(panel.get('rows', []))} rows, "
          f"{len(panel.get('segments', []))} rebalances")
    n = enrich(panel)
    print(f"enriched {n} rows with momentum")
    with open(PANEL, "w") as f:
        json.dump(panel, f)

    a = analyze_signals(panel)
    thr = a.get("ir_threshold")
    print(f"\nIR significance bar (Bonferroni / {len(a['signals'])} signals): "
          f"|IR| >= {thr:.2f}\n")
    print(f"{'signal':>12}  {'IC':>7}  {'IR':>6}  {'sig':>4}  "
          f"{'quintile':>9}  verdict")
    for s, d in sorted(a["signals"].items(),
                       key=lambda kv: -(kv[1].get("ic") or -9)):
        ic = d.get("ic"); ir = d.get("ic_ir"); qs = d.get("quintile_spread")
        sig = d.get("ic_significant")
        sigm = "—" if sig is None else ("✓" if sig else "✗")
        print(f"{s:>12}  {('%+.3f'%ic) if ic is not None else '   na':>7}  "
              f"{('%+.2f'%ir) if ir is not None else '  na':>6}  {sigm:>4}  "
              f"{('%+.3f'%qs) if qs is not None else '   na':>9}  {d.get('verdict','')}")

    mom = a["signals"].get("momentum", {})
    print("\n=== momentum verdict ===")
    print(f"  IC {mom.get('ic')}, IR {mom.get('ic_ir')}, "
          f"significant={mom.get('ic_significant')}")
    if mom.get("ic") is not None and a["signals"].get("arima", {}).get("ic") is not None:
        better = mom["ic"] > a["signals"]["arima"]["ic"]
        print(f"  momentum IC {'>' if better else '<='} ARIMA IC "
              f"({mom['ic']:+.3f} vs {a['signals']['arima']['ic']:+.3f}) — "
              f"{'a stronger signal than the current best' if better else 'not beating the current best'}")


if __name__ == "__main__":
    main()
