"""Engine trade-quality analysis over the append-only fill ledger.

The deterministic engine made every trade, so this is not investor psychology
— it is a mirror held up to the ENGINE's realised behaviour: does it hold
losers longer than winners (disposition-shaped outcome), churn re-entries
after stops, or trade more often than its own typical holding period implies?

Everything derives from ``trade_history`` rows (H4: ``cost_basis`` lives on
each SELL row, same convention as ``compute_realized_pnl_ytd``; commissions
are excluded from roundtrip P&L for consistency with it). Sample sizes are
reported everywhere — with a couple of months of paper trades these stats are
directional, never significant, and the note must say so.

Attribution arithmetic adapted from HKUDS/Vibe-Trading
``agent/src/shadow_account/backtester.py`` (MIT); the reference "rule band"
there comes from extracted rules — here it is the engine's own p25–p75
holding-day band, so the analysis is fully self-referential.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone

from auto_trader.state.portfolio_db import get_connection

logger = logging.getLogger(__name__)

# Below this many closed roundtrips every bias stat is labelled low-confidence.
MIN_SAMPLE = 10
# Re-buy within this many days of a stop-loss exit counts as stop-churn.
STOP_CHURN_DAYS = 7


def _dt(iso: str) -> datetime:
    """Parse an ISO timestamp to a NAIVE UTC datetime.

    Ledger rows carry +00:00 offsets today, but a future writer could emit a
    local offset or a bare naive string — normalising through UTC keeps every
    pair subtractable (mixed aware/naive raises TypeError).
    """
    d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _fetch_fills() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trade_history ORDER BY executed_at ASC, trade_id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def build_roundtrips(fills: list[dict] | None = None) -> dict:
    """Pair fills into position-open→flat episodes per ticker.

    An episode opens when a ticker's running share count leaves zero and
    closes when it returns to (float-tolerant) zero — so partial sells and
    lot-averaging stay inside one episode instead of producing bogus pairs.
    P&L sums (price − cost_basis)·shares over the episode's SELL rows, the
    same formula as ``compute_realized_pnl_ytd``; SELLs with NULL cost_basis
    are counted in ``n_ledger_gaps`` and contribute $0 (loudly, never wrongly).

    Returns {roundtrips, n_open, n_ledger_gaps}.
    """
    if fills is None:
        fills = _fetch_fills()
    episodes: dict[str, dict] = {}          # ticker → open episode accumulator
    roundtrips: list[dict] = []
    n_gaps = 0

    for f in fills:
        tkr = str(f["ticker"])
        action = str(f["action"]).upper()
        shares = float(f["shares"])
        if action not in ("BUY", "SELL") or shares <= 0:
            continue
        ep = episodes.get(tkr)
        if action == "BUY":
            if ep is None:
                ep = episodes[tkr] = {
                    "ticker": tkr, "entry_at": f["executed_at"], "open_shares": 0.0,
                    "pnl": 0.0, "regime_at_entry": f.get("regime_at_trade"),
                    "exit_reasons": [],
                }
            ep["open_shares"] += shares
            continue
        # SELL
        if ep is None:
            # Sell with no tracked open episode — ledger predates the window
            # or data gap; count it and move on.
            n_gaps += 1
            continue
        if shares > ep["open_shares"] + 1e-9:
            # Oversell vs the tracked episode (ledger gap / data corruption
            # upstream) — clamp to what's actually open so phantom shares
            # can't inflate P&L, and count it loudly.
            n_gaps += 1
            logger.warning("behavior: SELL %s %.4f sh exceeds open %.4f — "
                           "clamped (ledger gap)", tkr, shares, ep["open_shares"])
            shares = ep["open_shares"]
        if f.get("cost_basis") is None:
            n_gaps += 1
            logger.warning("behavior: SELL %s %.4f sh has NULL cost_basis — "
                           "contributes $0 to its roundtrip", tkr, shares)
        else:
            ep["pnl"] += (float(f["price"]) - float(f["cost_basis"])) * shares
        ep["open_shares"] -= shares
        if f.get("trigger_reason"):
            ep["exit_reasons"].append(str(f["trigger_reason"]))
        if ep["open_shares"] <= 1e-9:        # flat → close the episode
            entry, exit_ = _dt(ep["entry_at"]), _dt(f["executed_at"])
            roundtrips.append({
                "ticker": tkr,
                "entry_at": ep["entry_at"],
                "exit_at": f["executed_at"],
                "hold_days": max(0.0, (exit_ - entry).total_seconds() / 86400.0),
                "pnl": ep["pnl"],
                "exit_reason": ep["exit_reasons"][-1] if ep["exit_reasons"] else None,
                "regime_at_entry": ep["regime_at_entry"],
            })
            del episodes[tkr]

    return {"roundtrips": roundtrips, "n_open": len(episodes),
            "n_ledger_gaps": n_gaps}


def _median_hold(rts: list[dict]) -> float | None:
    return statistics.median([r["hold_days"] for r in rts]) if rts else None


def _overtrading(rts: list[dict], median_hold: float) -> dict:
    """Excess-frequency check: engine budget ≈ 1 roundtrip per 2·median_hold.

    Adapted from vibe's ``_overtrading_pnl``: extras are the lowest-|pnl|
    roundtrips beyond the budget; their summed P&L is the noise the extra
    churn bought (positive or negative).
    """
    if median_hold <= 0 or len(rts) < 2:
        return {"expected": None, "actual": len(rts), "excess": 0, "excess_pnl": 0.0}
    span = (_dt(rts[-1]["exit_at"]) - _dt(rts[0]["entry_at"])).total_seconds() / 86400.0
    expected = max(1.0, span / max(2.0 * median_hold, 1.0))
    excess = max(0, int(len(rts) - expected))
    extras = sorted(rts, key=lambda r: abs(r["pnl"]))[:excess]
    return {"expected": round(expected, 1), "actual": len(rts), "excess": excess,
            "excess_pnl": sum(r["pnl"] for r in extras)}


def _attribution(rts: list[dict]) -> dict:
    """Early/late-exit attribution vs the engine's own p25–p75 hold band.

    Winners exited below the band → pro-rata shortfall; losers held past it →
    pro-rata excess loss (vibe's ``_compute_attribution`` formulas, band from
    own history). Top-5 |impact| roundtrips returned as counterfactuals.
    """
    holds = sorted(r["hold_days"] for r in rts)
    n = len(holds)
    lo = holds[max(0, int(0.25 * (n - 1)))]
    hi = holds[min(n - 1, int(0.75 * (n - 1)))]
    early = late = 0.0
    counterfactuals = []
    for r in rts:
        impact, reason = 0.0, None
        if r["pnl"] > 0 and r["hold_days"] < lo:
            impact = r["pnl"] * max(0.0, (lo - r["hold_days"]) / max(lo, 1.0))
            early += impact
            reason = "early_exit"
        elif r["pnl"] < 0 and r["hold_days"] > hi:
            impact = -r["pnl"] * max(0.0, (r["hold_days"] - hi) / max(hi, 1.0))
            late += impact
            reason = "late_exit"
        if impact:
            counterfactuals.append({**{k: r[k] for k in
                                       ("ticker", "entry_at", "exit_at",
                                        "hold_days", "pnl")},
                                    "impact": impact, "reason": reason})
    counterfactuals.sort(key=lambda c: abs(c["impact"]), reverse=True)
    return {"band_lo_days": lo, "band_hi_days": hi,
            "early_exit_shortfall": early, "late_exit_excess": late,
            "counterfactuals": counterfactuals[:5]}


def _stop_churn(rts: list[dict]) -> list[dict]:
    """Re-entries into a ticker within STOP_CHURN_DAYS of a stop-loss exit."""
    churn = []
    stops = [r for r in rts
             if r["exit_reason"] and "stop" in r["exit_reason"].lower()]
    for s in stops:
        for r in rts:
            if (r["ticker"] == s["ticker"] and r is not s
                    and 0 <= (_dt(r["entry_at"]) - _dt(s["exit_at"])).total_seconds()
                    / 86400.0 <= STOP_CHURN_DAYS):
                churn.append({"ticker": s["ticker"], "stop_exit": s["exit_at"],
                              "reentry": r["entry_at"], "reentry_pnl": r["pnl"]})
    return churn


def _breakdown(rts: list[dict], key: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rts:
        groups.setdefault(str(r.get(key) or "unknown"), []).append(r)
    out = []
    for name, g in sorted(groups.items()):
        wins = [r for r in g if r["pnl"] > 0]
        out.append({"group": name, "n": len(g),
                    "win_rate": len(wins) / len(g),
                    "total_pnl": sum(r["pnl"] for r in g)})
    return out


def analyze_behavior(fills: list[dict] | None = None) -> dict:
    """Full trade-quality report over the ledger. Pure read; no side effects."""
    built = build_roundtrips(fills)
    rts = built["roundtrips"]
    n = len(rts)
    out = {
        "n_roundtrips": n,
        "n_open_positions": built["n_open"],
        "n_ledger_gaps": built["n_ledger_gaps"],
        "confidence": ("low" if n < MIN_SAMPLE else
                       "moderate" if n < 30 else "ok"),
    }
    if n == 0:
        return out

    wins = [r for r in rts if r["pnl"] > 0]
    losses = [r for r in rts if r["pnl"] < 0]
    gross_win = sum(r["pnl"] for r in wins)
    gross_loss = -sum(r["pnl"] for r in losses)
    median_hold = _median_hold(rts) or 0.0

    disposition = None
    if len(wins) >= 3 and len(losses) >= 3:
        mh_w = _median_hold(wins) or 0.0
        mh_l = _median_hold(losses) or 0.0
        disposition = {
            "median_hold_winners": mh_w,
            "median_hold_losers": mh_l,
            "ratio": (mh_l / mh_w) if mh_w > 0 else None,
            "flagged": mh_w > 0 and (mh_l / mh_w) > 1.5,
        }

    out.update({
        "total_pnl": sum(r["pnl"] for r in rts),
        "win_rate": len(wins) / n,
        "median_hold_days": median_hold,
        "avg_win": (gross_win / len(wins)) if wins else None,
        "avg_loss": (-gross_loss / len(losses)) if losses else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "disposition": disposition,
        "overtrading": _overtrading(rts, median_hold),
        "attribution": _attribution(rts),
        "stop_churn": _stop_churn(rts),
        "by_exit_reason": _breakdown(rts, "exit_reason"),
        "by_regime_at_entry": _breakdown(rts, "regime_at_entry"),
    })
    return out


__all__ = ["build_roundtrips", "analyze_behavior", "MIN_SAMPLE",
           "STOP_CHURN_DAYS"]
