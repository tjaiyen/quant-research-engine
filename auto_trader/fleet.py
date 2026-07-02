"""Strategy fleet — N parallel paper portfolios, one per strategy.

The Signal Lab's honest verdict is "forward paper data is the real arbiter";
the fleet collects that forward evidence for several strategies AT ONCE instead
of one at a time. Every member re-ranks the SAME weekly screen (the flagship's
``store/screener_cache.json`` already carries per-stock ``signal_scores``) with
its own weight vector — zero signal re-runs — and then trades through the SAME
unmodified ``monthly_run``/``daily_run`` machinery, isolated per member by the
env seams that already exist (``TRADER_DB_PATH``, ``MOCK_BROKER_STATE``,
``SCREENER_CACHE_PATH``).

Members run as SUBPROCESSES (``python -m auto_trader.fleet --member X --mode
cycle|monitor``) so each gets a fresh broker singleton and import-time config.
The flagship (the original book) is never driven from here — its history and
schedule are untouched; the fleet only ever READS its screener cache.

Paper-only, like everything else. The live-trading gates are untouched.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
FLEET_DIR = REPO_ROOT / "store" / "fleet"
SHARED_CACHE = REPO_ROOT / "store" / "screener_cache.json"

# ── The fleet registry (Core 6) ──────────────────────────────────────────────
# weights: signal → weight (normalised at use). None → blend the CURRENT-mode
# WEIGHT_MATRIX with the cache's regime probabilities (the "default" strategy).
# min_composite: per-member MIN_COMPOSITE_TO_BUY override — re-weighted
# composites live on different scales (a pure-ARIMA composite hovers near 0.5,
# under the 0.60 gate), so the floor is calibrated per strategy, explicitly.
# Must stay above SIGNAL_EXIT_THRESHOLD (0.45) — config self-validates.
FLEET: list[dict] = [
    {"id": "candidate", "label": "ARIMA+Sharpe (live)", "kind": "flagship"},
    {"id": "default", "label": "Default 5-signal", "kind": "strategy",
     "weights": None},
    {"id": "arima", "label": "ARIMA only", "kind": "strategy",
     "weights": {"arima": 1.0}, "min_composite": 0.46},
    {"id": "asm", "label": "ARIMA+Sharpe+Momentum", "kind": "strategy",
     "weights": {"arima": 0.5, "sharpe": 0.3, "momentum": 0.2},
     "min_composite": 0.55},
    {"id": "equal", "label": "Equal-weight 5", "kind": "strategy",
     "weights": {"arima": 0.2, "kalman": 0.2, "garch": 0.2,
                 "monte_carlo": 0.2, "sharpe": 0.2},
     "min_composite": 0.55},
    {"id": "spy", "label": "SPY buy-hold (control)", "kind": "hold",
     "symbol": "SPY", "group": "control"},
    # ── tournament variants: the backtest's hypotheses, forward-tested live ──
    # The tournament flagged its in-sample winner as "likely curve-fit,
    # hypothesis only" — these books let real forward data adjudicate.
    {"id": "inverse", "label": "Worst-ranked (inverse)", "kind": "strategy",
     "weights": None, "invert": True, "group": "tournament"},
    {"id": "sharpe", "label": "Pure Sharpe", "kind": "strategy",
     "weights": {"sharpe": 1.0}, "group": "tournament"},
    {"id": "top5", "label": "Top-5 per sector", "kind": "strategy",
     "weights": None, "env": {"TOP_N_PER_SECTOR": "5"},
     "group": "tournament"},
    {"id": "random20", "label": "Random 20 (control)", "kind": "strategy",
     "random_n": 20, "env": {"TOP_N_PER_SECTOR": "5"},
     "group": "control"},
]


def member_dir(member_id: str) -> Path:
    return FLEET_DIR / member_id


def member_env(member: dict) -> dict:
    """The env vars that point the unmodified trader at this member's state."""
    d = member_dir(member["id"])
    env = dict(os.environ)
    env["TRADER_DB_PATH"] = str(d / "portfolio.db")
    env["MOCK_BROKER_STATE"] = str(d / "mock_broker.json")
    env["SCREENER_CACHE_PATH"] = str(d / "screener_cache.json")
    env["FLEET_SKIP_REFRESH"] = "1"   # the flagship monitor already refreshed prices
    if member.get("min_composite") is not None:
        env["MIN_COMPOSITE_TO_BUY"] = str(member["min_composite"])
    for k, v in (member.get("env") or {}).items():   # per-member extras (e.g. TOP_N_PER_SECTOR)
        env[k] = str(v)
    return env


# ── Re-ranking: same screen, this member's weights ───────────────────────────
def _momentum_scores(tickers: list[str]) -> dict[str, float]:
    """12-1 momentum score per ticker from the CACHED price history.

    Momentum is measured-only in the live engine (not in the stored 5
    signal_scores), so members that weight it compute it here — cheap, local.
    """
    import pandas as pd

    from screener.signals.momentum_signal import momentum_signal
    from utils.db import fetch_prices

    out: dict[str, float] = {}
    for t in tickers:
        try:
            df = fetch_prices(t)
            if df is None or df.empty or "adj_close" not in df.columns:
                out[t] = 0.0
                continue
            ph = pd.DataFrame({"Close": df["adj_close"].values})
            out[t] = float(momentum_signal(t, ph).get("score") or 0.0)
        except Exception as exc:                      # noqa: BLE001 — best-effort
            logger.debug("momentum for %s failed (%s) — 0.0", t, exc)
            out[t] = 0.0
    return out


def _default_weights(cache: dict) -> dict[str, float]:
    """Blend the CURRENT-mode WEIGHT_MATRIX with the cache's regime probabilities.

    The flagship cache's stored ``blended_weights`` were computed under the live
    candidate mode — the 'default' member must re-blend from the original matrix.
    """
    from screener.config import EXPECTED_SIGNAL_KEYS, WEIGHT_MATRIX

    probs = (cache.get("regime") or {}).get("probabilities") or {}
    if not probs:
        label = (cache.get("regime") or {}).get("label", "sideways")
        probs = {label: 1.0}
    blended = {s: sum(float(p) * WEIGHT_MATRIX[r][s]
                      for r, p in probs.items() if r in WEIGHT_MATRIX)
               for s in EXPECTED_SIGNAL_KEYS}
    total = sum(blended.values()) or 1.0
    return {s: w / total for s, w in blended.items()}


def rescore(row: dict, weights: dict[str, float],
            momentum: dict[str, float] | None = None) -> float:
    """Re-blend a stock's stored signal_scores with a member weight vector.

    Same math as the tournament's ``_row_score`` — composite = Σ w·score / Σ w.
    Momentum (if weighted) comes from the side-computed map, NOT the cache.
    """
    sig = dict(row.get("signal_scores") or {})
    if momentum is not None and "momentum" in weights:
        sig["momentum"] = momentum.get(row.get("ticker"), 0.0)
    den = sum(weights.values()) or 1.0
    return sum(w * float(sig.get(k) or 0.0) for k, w in weights.items()) / den


def _random_scores(shared: dict, n: int) -> dict[str, float]:
    """Month-seeded random selection among VETO-PASSERS (the harshest control).

    Deterministic within a month (like the tournament's fixed-seed control, and
    so a same-day re-run is idempotent); re-rolls at the next monthly cycle.
    Chosen names get a clearly-above-floor score; everything else scores 0.
    """
    import random

    month = str(shared.get("generated_at") or shared.get("_cached_at") or "")[:7]
    rng = random.Random(f"fleet-random20-{month}")
    passers = sorted({s["ticker"] for stocks in (shared.get("sectors") or {}).values()
                      for s in (stocks or [])
                      if s.get("passed_veto") and s.get("ticker")})
    chosen = rng.sample(passers, min(n, len(passers)))
    # tiny deterministic jitter so ranks are stable but not all identical
    return {t: 0.65 + 0.05 * rng.random() for t in chosen}


def build_member_cache(shared: dict, member: dict) -> dict:
    """The member's view of the shared screen: same stocks, same vetoes, same
    regime — composites re-scored with the member's weights and re-ranked.

    Variant flags: ``invert`` flips the ranking (composite = 1 − score) so the
    unchanged trader machinery buys the WORST-ranked veto-passers (the
    tournament's in-sample winner, forward-tested); ``random_n`` replaces
    scoring with a month-seeded random pick among veto-passers (control).
    """
    cache = copy.deepcopy(shared)
    randoms = _random_scores(shared, member["random_n"]) if member.get("random_n") else None
    weights = member.get("weights") or _default_weights(shared)
    momentum = None
    if randoms is None and "momentum" in weights:
        tickers = [s.get("ticker") for stocks in (shared.get("sectors") or {}).values()
                   for s in (stocks or []) if s.get("ticker")]
        momentum = _momentum_scores(tickers)
    top: list[dict] = []
    for sector, stocks in (cache.get("sectors") or {}).items():
        for s in (stocks or []):
            if randoms is not None:
                score = randoms.get(s.get("ticker"), 0.0)
            else:
                score = rescore(s, weights, momentum)
            s["composite_score"] = round(score, 6)
        if member.get("invert") and randoms is None:
            # Invert by RANK with a synthetic band [0.61, 0.75] (worst raw score
            # highest) — a naive 1−score puts most veto-passers under any legal
            # buy floor (>0.45), which choked the first live seed to 2 buys.
            # Order-faithful to the tournament's 'Worst-ranked' variant; veto
            # flags untouched, so 'worst-ranked' still ≠ 'unsafe'.
            asc = sorted(stocks, key=lambda s: (s.get("composite_score") or 0.0))
            n = max(len(asc) - 1, 1)
            for i, s in enumerate(asc):
                s["composite_score"] = round(0.75 - 0.14 * (i / n), 6)
        stocks.sort(key=lambda s: -(s.get("composite_score") or 0.0))
        for i, s in enumerate(stocks, start=1):
            s["rank"] = i
            if s.get("passed_veto"):
                top.append({"ticker": s.get("ticker"), "sector": sector,
                            "score": s["composite_score"]})
    top.sort(key=lambda p: -(p["score"] or 0.0))
    summary = cache.setdefault("summary", {})
    summary["top_overall"] = [dict(p, rank=i) for i, p in enumerate(top[:5], 1)]
    cache["_fleet_member"] = member["id"]
    return cache


def write_member_cache(member: dict) -> Path:
    shared = json.loads(SHARED_CACHE.read_text())
    cache = build_member_cache(shared, member)
    d = member_dir(member["id"])
    d.mkdir(parents=True, exist_ok=True)
    out = d / "screener_cache.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(out)
    return out


# ── Member entrypoint (run as a SUBPROCESS with member env already set) ──────
def _run_hold_cycle(member: dict) -> dict:
    """SPY buy-hold control: buy once with all cash, then never trade again."""
    from auto_trader.broker.alpaca_client import get_client
    from auto_trader.state.portfolio_db import initialize_db

    initialize_db()
    client = get_client()
    if client.list_positions():
        logger.info("hold member %s already invested — no-op", member["id"])
        return {"status": "held"}
    cash = float(client.get_account().cash)
    if cash <= 0:
        return {"status": "no_cash"}
    order = client.submit_order(member["symbol"], "buy", notional=cash)
    logger.info("hold member %s: bought %s %s @ %s",
                member["id"], order.filled_qty, member["symbol"],
                order.filled_avg_price)
    # Ledger + positions DB sync via the standard monitor path (next monitor run
    # writes the snapshot); record the trade now so the audit trail is complete.
    try:
        from auto_trader.state.portfolio_db import log_trade
        log_trade({
            "ticker": member["symbol"], "action": "BUY",
            "shares": float(order.filled_qty),
            "price": float(order.filled_avg_price),
            "total_value": float(order.filled_qty) * float(order.filled_avg_price),
            "cost_basis": float(order.filled_avg_price),
            "order_id": order.id, "trigger_reason": "FLEET_HOLD_SEED",
        })
    except Exception as exc:                          # noqa: BLE001
        logger.warning("hold trade log failed (%s) — broker book is authoritative", exc)
    return {"status": "bought"}


def _member_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="fleet member runner (subprocess)")
    ap.add_argument("--member", required=True)
    ap.add_argument("--mode", choices=("cycle", "monitor"), required=True)
    ap.add_argument("--force-window", action="store_true",
                    help="bypass the MOO-window wait (manual seeding inside the "
                         "1st-5th window; scheduled runs never pass this)")
    args = ap.parse_args(argv)

    member = next((m for m in FLEET if m["id"] == args.member), None)
    if member is None:
        print(f"unknown fleet member: {args.member}", file=sys.stderr)
        return 2
    if member.get("kind") == "flagship":
        print("the flagship is driven by the normal paper commands, not the fleet",
              file=sys.stderr)
        return 2

    # Env must already point at the member dir (the driver sets it); tolerate a
    # direct manual invocation by setting it here too.
    for k, v in member_env(member).items():
        os.environ.setdefault(k, v)

    if args.mode == "monitor":
        from auto_trader.scripts.daily_run import main as daily_main
        return int(daily_main([]) or 0)

    # cycle
    if member.get("kind") == "hold":
        res = _run_hold_cycle(member)
        return 0 if res.get("status") in ("held", "bought") else 1

    from auto_trader.scripts import monthly_run
    if args.force_window:
        from auto_trader.execution import order_scheduler
        order_scheduler.wait_for_moo_window = lambda **_: True   # paper-only bypass
    res = monthly_run.run_monthly_cycle()
    # "skipped"/"no_eligible"/"all_blocked" are legitimate no-trade outcomes,
    # not failures — the member simply sits out this cycle.
    ok = res.get("status") in ("ok", "skipped", "no_eligible", "all_blocked")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_member_main())
