"""Why did the winner win — and is it real? Attribution + honesty checks.

Attribution: which signals actually predicted returns (panel-wide IC), the
winner's sector tilt, its regime-conditional returns, and turnover. Honesty:
did it beat SPY and random, did it hold up out-of-sample, and is the field
spread wide enough that the winner isn't just noise.
"""
from __future__ import annotations

from collections import defaultdict

from screener.tournament.variants import SIGNALS


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    try:
        from screener.backtest.signal_ic import _spearman as ic
        return ic(xs, ys)
    except Exception:
        pass
    if len(xs) < 5:
        return None
    import numpy as np
    rx = np.argsort(np.argsort(np.asarray(xs, float)))
    ry = np.argsort(np.argsort(np.asarray(ys, float)))
    if rx.std() < 1e-12 or ry.std() < 1e-12:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def _panel_signal_ic(panel: dict) -> dict:
    """Spearman IC of each signal vs realized forward return, across all rows."""
    out = {}
    for sig in SIGNALS:
        xs, ys = [], []
        for r in panel.get("rows", []):
            v = (r.get("signals") or {}).get(sig)
            fwd = r.get("fwd_return")
            if v is not None and fwd is not None:
                xs.append(v); ys.append(fwd)
        out[sig] = _spearman(xs, ys)
    return out


def _ticker_sector(panel: dict) -> dict:
    return {r["ticker"]: r.get("sector") for r in panel.get("rows", [])}


def _sector_tilt(winner: dict, panel: dict) -> list[dict]:
    sec_of = _ticker_sector(panel)
    counts: dict = defaultdict(int)
    total = 0
    for held in winner.get("holdings", []):
        for t in held:
            counts[sec_of.get(t, "?")] += 1; total += 1
    if not total:
        return []
    return sorted(({"sector": s, "pct": n / total} for s, n in counts.items()),
                  key=lambda d: -d["pct"])[:6]


def _regime_conditional(winner: dict, panel: dict) -> list[dict]:
    segs = panel.get("segments", [])
    rets = winner.get("seg_returns", [])
    by_reg: dict = defaultdict(list)
    for seg, r in zip(segs, rets):
        by_reg[seg.get("regime") or "?"].append(r)
    return [{"regime": k, "avg_return": sum(v) / len(v), "n": len(v)}
            for k, v in by_reg.items()]


def _turnover(winner: dict) -> float | None:
    holds = [set(h) for h in winner.get("holdings", [])]
    if len(holds) < 2:
        return None
    ch = []
    for a, b in zip(holds, holds[1:]):
        denom = max(len(a | b), 1)
        ch.append(len(a ^ b) / denom)
    return sum(ch) / len(ch) if ch else None


def attribute(tour: dict, panel: dict) -> dict:
    results = tour.get("results", [])
    ranked = tour.get("ranked", [])
    if not ranked:
        return {"verdict": "No results.", "winner": None}
    winner = ranked[0]
    by_ctl = {r["spec"].get("control"): r for r in results if r["group"] == "control"}
    spy = by_ctl.get("spy")
    rnd = by_ctl.get("random")
    wtot = winner["full"].get("total_return", 0.0)
    beat_spy = wtot - (spy["full"].get("total_return", 0.0) if spy else 0.0)
    beat_random = wtot - (rnd["full"].get("total_return", 0.0) if rnd else 0.0)

    oos_sorted = sorted(results, key=lambda r: r["out_sample"].get("total_return", 0.0),
                        reverse=True)
    oos_rank = next((i + 1 for i, r in enumerate(oos_sorted)
                     if r["label"] == winner["label"]), None)

    strat_totals = [r["full"].get("total_return", 0.0) for r in results
                    if r["group"] != "control"]
    spread = (max(strat_totals) - min(strat_totals)) if strat_totals else 0.0

    # diagnostic: did the worst-ranked (inverse) variant actually underperform?
    inverse = next((r for r in results if r["spec"].get("pick") == "bottom"), None)
    default = next((r for r in results if r["label"].startswith("Regime-blended")), None)
    ranking_has_signal = None
    if inverse and default:
        ranking_has_signal = (default["full"].get("total_return", 0.0)
                              > inverse["full"].get("total_return", 0.0))

    oos_holds = bool(oos_rank and oos_rank <= max(3, len(results) // 4))
    verdict = _verdict(winner, beat_spy, beat_random, oos_holds, spread, ranking_has_signal)

    return {
        "winner": winner["label"],
        "beat_spy": beat_spy, "beat_random": beat_random,
        "oos_rank": oos_rank, "oos_holds": oos_holds,
        "field_spread": spread, "ranking_has_signal": ranking_has_signal,
        "signal_ic": _panel_signal_ic(panel),
        "sector_tilt": _sector_tilt(winner, panel),
        "regime_conditional": _regime_conditional(winner, panel),
        "turnover": _turnover(winner),
        "verdict": verdict,
    }


def _verdict(winner, beat_spy, beat_random, oos_holds, spread, ranking_has_signal) -> str:
    name = winner["label"]
    if beat_random <= 0:
        return (f"⚠ No real edge: the best strategy (**{name}**) did not beat a "
                f"random 20-stock basket. Treat the ranking as noise, not skill.")
    if beat_spy <= 0:
        return (f"**{name}** beat random but **not** SPY — the screener adds some "
                f"selection value, but buy-and-hold SPY would have done better.")
    if not oos_holds:
        return (f"**{name}** led in-sample and beat SPY (+{beat_spy*100:.1f}%), but "
                f"it did **not** hold up out-of-sample — likely curve-fit. Hypothesis only.")
    tail = "" if ranking_has_signal is not False else \
        " (Caution: the inverse 'worst-ranked' variant didn't clearly underperform, so the ranking signal is weak.)"
    return (f"**{name}** won, beat SPY by +{beat_spy*100:.1f}% and random by "
            f"+{beat_random*100:.1f}%, and **held up out-of-sample** — a credible "
            f"hypothesis worth forward-testing in paper (not proof).{tail}")


__all__ = ["attribute"]
