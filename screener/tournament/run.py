"""Run the tournament: simulate each variant over the panel, rank, split OOS.

Cheap by construction — each variant is a re-weight/re-select/re-size pass over
the pre-computed panel rows (no re-scoring, no price lookups). The winner is
picked on the IN-SAMPLE window and its OUT-OF-SAMPLE result is reported, so a
variant that only won by curve-fitting is exposed.
"""
from __future__ import annotations

from collections import defaultdict

from screener.backtest.portfolio_backtest import _cagr, _max_drawdown, _sharpe
from screener.rigor.costs import cost_haircut

_STEP = {"month": 30, "quarter": 91}


def _row_score(row: dict, weights: dict | None) -> float:
    if not weights:
        return row.get("composite") or 0.0
    sig = row.get("signals") or {}
    den = sum(weights.values()) or 1.0
    return sum(w * (sig.get(k) or 0.0) for k, w in weights.items()) / den


def _select(seg_rows: list[dict], spec: dict) -> list[dict]:
    cand = seg_rows if not spec.get("guards") else [r for r in seg_rows if r["passed_veto"]]
    bysec: dict = defaultdict(list)
    for r in cand:
        bysec[r["sector"]].append(r)
    rev = spec.get("pick", "top") == "top"
    picks: list[dict] = []
    for rs in bysec.values():
        rs2 = sorted(rs, key=lambda r: _row_score(r, spec.get("weights")), reverse=rev)
        picks.extend(rs2[: spec.get("top_n", 2)])
    return picks


def _weights(picks: list[dict], spec: dict) -> list[float]:
    if spec.get("sizing") == "score":
        sc = [max(_row_score(p, spec.get("weights")), 0.0) for p in picks]
        tot = sum(sc) or 1.0
        return [s / tot for s in sc]
    n = len(picks) or 1
    return [1.0 / n] * len(picks)


def _segment(seg: dict, seg_rows: list[dict], spec: dict) -> tuple[float, list[str]]:
    """Return (segment return, held tickers) for a variant in one segment."""
    kind = spec.get("kind")
    if kind == "control":
        ctl = spec.get("control")
        if ctl == "spy":
            return (seg.get("spy_return") or 0.0, ["SPY"])
        if ctl == "universe":
            rets = [r["fwd_return"] for r in seg_rows if r.get("fwd_return") is not None]
            return (sum(rets) / len(rets) if rets else 0.0, [r["ticker"] for r in seg_rows])
        if ctl == "random":
            pool = sorted(r["ticker"] for r in seg_rows)
            if not pool:
                return (0.0, [])
            # Proper unbiased sample of up to 20 names, deterministic per segment
            # (seeded by the date string — reproducible, but a fair benchmark).
            import random as _random
            held = _random.Random(str(seg.get("d0", ""))).sample(pool, min(20, len(pool)))
            rmap = {r["ticker"]: r["fwd_return"] for r in seg_rows}
            rets = [rmap[t] for t in held if rmap.get(t) is not None]
            return (sum(rets) / len(rets) if rets else 0.0, held)
        return (0.0, [])
    picks = _select(seg_rows, spec)
    if not picks:
        return (0.0, [])
    w = _weights(picks, spec)
    ret = sum(wi * (p.get("fwd_return") or 0.0) for wi, p in zip(w, picks))
    return (ret, [p["ticker"] for p in picks])


def _metrics(seg_rets: list[float], spy_rets: list[float], ppy: float) -> dict:
    if not seg_rets:
        return {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0,
                "sharpe": None, "win_rate": None, "excess": 0.0, "n": 0}
    eq, curve = 1.0, [100.0]
    for r in seg_rets:
        eq *= (1.0 + r); curve.append(eq * 100.0)
    total = eq - 1.0
    spy_eq = 1.0
    for r in spy_rets:
        spy_eq *= (1.0 + r)
    years = len(seg_rets) / ppy if ppy else 1.0
    return {
        "total_return": total, "spy_total": spy_eq - 1.0,
        "excess": total - (spy_eq - 1.0),
        "cagr": _cagr(total, years if years > 0 else 1.0),
        "max_drawdown": _max_drawdown(curve),
        "sharpe": _sharpe(seg_rets, ppy),
        "win_rate": sum(1 for r in seg_rets if r > 0) / len(seg_rets),
        "n": len(seg_rets),
    }


def run_tournament(panel: dict, variants: list[dict], oos_frac: float = 0.34,
                   cost_bps: float = 0.0) -> dict:
    segs = panel.get("segments", [])
    rows_by_date: dict = defaultdict(list)
    for r in panel.get("rows", []):
        rows_by_date[r["d0"]].append(r)
    ppy = 365.25 / _STEP.get(panel.get("rebalance", "quarter"), 91)
    spy_rets = [s.get("spy_return") or 0.0 for s in segs]

    n_is = max(2, int(round(len(segs) * (1.0 - oos_frac)))) if segs else 0

    results = []
    for spec in variants:
        seg_rets, holds = [], []
        prev_held: list[str] = []
        for seg in segs:
            ret, held = _segment(seg, rows_by_date.get(seg["d0"], []), spec)
            # U27 transaction-cost stress: haircut the gross return by
            # turnover × round-trip bps. Uniform across variants — the
            # leaderboard + the candidate A/B become net-of-cost.
            ret -= cost_haircut(prev_held, held, cost_bps)
            prev_held = held
            seg_rets.append(ret); holds.append(held)
        results.append({
            "label": spec["label"], "group": spec["group"], "spec": spec,
            "seg_returns": seg_rets, "holdings": holds,
            "curve": _equity_curve(segs, seg_rets),
            "full": _metrics(seg_rets, spy_rets, ppy),
            "in_sample": _metrics(seg_rets[:n_is], spy_rets[:n_is], ppy),
            "out_sample": _metrics(seg_rets[n_is:], spy_rets[n_is:], ppy),
        })

    # Rank by in-sample total return (primary), Sharpe as tiebreak. Return is the
    # robust primary — a low-variance fluke can post an absurd Sharpe (and a
    # noisy control could "win" on Sharpe alone). Risk is policed by the shown
    # Sharpe/maxDD, the controls, and the out-of-sample check, not the rank metric.
    def _key(r):
        m = r["in_sample"]
        return (m.get("total_return", 0.0), m.get("sharpe") or 0.0)
    ranked = sorted(results, key=_key, reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    return {"n_segments": len(segs), "n_in_sample": n_is, "ppy": ppy,
            "cost_bps": cost_bps, "ranked": ranked, "results": results}


def _equity_curve(segs: list[dict], seg_rets: list[float]) -> list[dict]:
    eq, spy = 1.0, 1.0
    out = [{"date": segs[0]["d0"] if segs else "", "v": 100.0, "spy": 100.0}]
    for seg, r in zip(segs, seg_rets):
        eq *= (1.0 + r); spy *= (1.0 + (seg.get("spy_return") or 0.0))
        out.append({"date": seg.get("d1"), "v": round(eq * 100, 2),
                    "spy": round(spy * 100, 2)})
    return out


__all__ = ["run_tournament"]
