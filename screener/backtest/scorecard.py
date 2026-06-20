"""Forward-pick scorecard — "are our predictions working?" (DB-only).

Grades PAST screener picks against what prices ACTUALLY did, vs SPY. No signal
re-runs, no live fetch — pure reads from the cache (`screener_results` + `prices`)
plus the paper ledger's SPY benchmark. Accumulates honesty over time: it can only
measure a pick once enough days have elapsed.

`summarize()` is pure and unit-tested; `compute_scorecard()` does the DB reads.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Forward windows we grade picks over (calendar days).
HORIZONS_DAYS = (7, 28, 84)


def summarize(graded: list[dict]) -> dict:
    """Aggregate graded picks. Each row needs 'forward_return' and 'alpha'.

    Returns hit_rate (% alpha > 0), up_rate (% return > 0), averages, and n.
    """
    n = len(graded)
    if n == 0:
        return {"n": 0, "hit_rate": None, "up_rate": None,
                "avg_return": None, "avg_alpha": None}
    rets = [g["forward_return"] for g in graded]
    alphas = [g["alpha"] for g in graded]
    return {
        "n": n,
        "hit_rate": sum(1 for a in alphas if a > 0) / n,   # beat SPY
        "up_rate": sum(1 for r in rets if r > 0) / n,       # went up
        "avg_return": sum(rets) / n,
        "avg_alpha": sum(alphas) / n,
    }


def _grade_pick(ticker: str, start_iso: str, end_iso: str, price_fn) -> dict | None:
    """Return {forward_return, spy_return, alpha} or None if prices are missing."""
    p0 = price_fn(ticker, start_iso)
    p1 = price_fn(ticker, end_iso)
    s0 = price_fn("SPY", start_iso)
    s1 = price_fn("SPY", end_iso)
    if not p0 or not p1 or not s0 or not s1:
        return None
    fr = p1 / p0 - 1.0
    sr = s1 / s0 - 1.0
    return {"forward_return": fr, "spy_return": sr, "alpha": fr - sr}


def compute_scorecard(horizons_days: tuple[int, ...] = HORIZONS_DAYS,
                      today: date | None = None) -> dict:
    """Grade every past run's passed picks over each elapsed horizon, vs SPY."""
    from utils.db import (
        fetch_screener_picks,
        list_screener_runs,
        price_on_or_before,
    )

    today = today or datetime.now(timezone.utc).date()
    runs = list_screener_runs(limit=200)

    keys = [f"{h}d" for h in horizons_days] + ["to_date"]
    buckets: dict[str, list[dict]] = {k: [] for k in keys}
    top_buckets: dict[str, list[dict]] = {f"{h}d": [] for h in horizons_days}
    attempts: dict[str, int] = {k: 0 for k in keys}   # picks we tried to grade (coverage)

    n_graded_runs = 0
    for run in runs:
        run_at = str(run["run_at"])
        try:
            run_date = datetime.strptime(run_at[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (today - run_date).days
        picks = [p for p in fetch_screener_picks(run_at) if p.get("passed_veto")]
        if not picks:
            continue
        graded_any = False

        # Fixed horizons (only once enough days have elapsed).
        for h in horizons_days:
            if age < h:
                continue
            key = f"{h}d"
            end_iso = (run_date + timedelta(days=h)).isoformat()
            for p in picks:
                attempts[key] += 1
                g = _grade_pick(p["ticker"], run_at[:10], end_iso, price_on_or_before)
                if g is None:
                    continue  # missing start/end price — counted as a coverage miss
                graded_any = True
                buckets[key].append(g)
                if p.get("top_overall_rank"):
                    top_buckets[key].append(g)

        # "To date" — return from the run to the latest price (always available,
        # but ~0 for very recent runs). Useful as an early, noisy read.
        end_iso = today.isoformat()
        for p in picks:
            attempts["to_date"] += 1
            g = _grade_pick(p["ticker"], run_at[:10], end_iso, price_on_or_before)
            if g is not None:
                graded_any = True
                buckets["to_date"].append(g)
        if graded_any:
            n_graded_runs += 1

    def _with_coverage(key: str) -> dict:
        s = summarize(buckets[key])
        att = attempts[key]
        s["attempted"] = att
        s["coverage"] = (s["n"] / att) if att else None
        return s

    horizons_out = {k: _with_coverage(k) for k in keys}
    top_out = {k: summarize(v) for k, v in top_buckets.items()}

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "n_runs": len(runs),
        "n_graded_runs": n_graded_runs,
        "horizons": horizons_out,          # all passed picks, per horizon
        "top_horizons": top_out,           # just the top-overall picks
        "paper": paper_vs_spy(),
    }


def paper_vs_spy() -> dict:
    """Paper portfolio cumulative return vs SPY, from the equity snapshots."""
    try:
        from auto_trader.state.portfolio_db import get_portfolio_snapshots
        snaps = [s for s in get_portfolio_snapshots(days=3650)
                 if s.get("total_value") and s.get("benchmark_value")]
    except Exception as exc:
        logger.debug("paper_vs_spy unavailable: %s", exc)
        return {"status": "no_data"}

    if len(snaps) < 2:
        return {"status": "no_data"}

    v0, vN = float(snaps[0]["total_value"]), float(snaps[-1]["total_value"])
    b0, bN = float(snaps[0]["benchmark_value"]), float(snaps[-1]["benchmark_value"])
    port_ret = vN / v0 - 1.0 if v0 else 0.0
    spy_ret = bN / b0 - 1.0 if b0 else 0.0
    has_positions = any(s.get("n_positions") for s in snaps)
    return {
        "status": "ok" if has_positions else "cash_only",
        "n_days": len(snaps),
        "start_date": snaps[0].get("snapshot_date"),
        "port_return": port_ret,
        "spy_return": spy_ret,
        "excess": port_ret - spy_ret,
    }


__all__ = ["summarize", "compute_scorecard", "paper_vs_spy", "HORIZONS_DAYS"]
