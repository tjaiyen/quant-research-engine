"""Always-on accounting reconciler (Phase 29).

Independently REPLAYS the raw trade ledger (``trade_history``) — cash flows,
WACC cost bases, position quantities — and compares every stored P&L surface
against the replay: the broker book (``mock_broker.json``), the ``positions``
table, ``compute_realized_pnl_ytd()``, and today's ``portfolio_snapshots``
row. Any drift beyond $0.01 is a discrepancy: it means one surface's math or
data diverged from the append-only ledger of record.

Pure read-only — never repairs anything. The daily monitor runs it after the
snapshot and logs RECON_OK / RECON_DRIFT to ``system_events``; the dashboard
renders an amber banner on drift; ``track audit`` runs it on demand.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from auto_trader.state.portfolio_db import (
    compute_realized_pnl_ytd,
    get_all_positions,
    get_connection,
    get_portfolio_snapshots,
)

logger = logging.getLogger(__name__)

TOLERANCE = 0.01          # dollars — absorbs float dust, flags real drift
STARTING_CAPITAL = 10_000.0
_EPS_SHARES = 1e-6        # share-count tolerance (fractional-share dust)


def _broker_state_path() -> Path:
    """Same resolution as alpaca_client.get_client (env override, else store/)."""
    return Path(os.getenv(
        "MOCK_BROKER_STATE",
        str(Path(__file__).resolve().parents[2] / "store" / "mock_broker.json"),
    ))


def _replay_ledger() -> dict:
    """Re-derive the book from trade_history alone (the independent truth).

    Returns {cash, positions: {ticker: {qty, cost}}, realized_ytd}.
    Realized uses the REPLAYED WACC cost at sell time — independently
    validating the stored ``cost_basis`` column, not trusting it.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT executed_at, action, ticker, shares, price "
            "FROM trade_history ORDER BY executed_at, trade_id"
        ).fetchall()
    year_start = f"{datetime.now().year}-01-01"
    cash = STARTING_CAPITAL
    pos: dict[str, dict] = {}
    realized_ytd = 0.0
    for r in rows:
        sh, px = float(r["shares"]), float(r["price"])
        if r["action"] == "BUY":
            cash -= sh * px
            p = pos.setdefault(r["ticker"], {"qty": 0.0, "cost": px})
            total_cost = p["qty"] * p["cost"] + sh * px
            p["qty"] += sh
            p["cost"] = total_cost / p["qty"] if p["qty"] else px
        elif r["action"] == "SELL":
            cash += sh * px
            p = pos.get(r["ticker"])
            if p is None:
                logger.warning("replay: SELL %s with no prior BUY — skipped "
                               "from position math", r["ticker"])
                continue
            if str(r["executed_at"]) >= year_start:
                realized_ytd += (px - p["cost"]) * sh
            p["qty"] -= sh
            if p["qty"] <= _EPS_SHARES:
                pos.pop(r["ticker"], None)
    return {"cash": cash, "positions": pos, "realized_ytd": realized_ytd}


def _check(out: list[dict], field: str, expected: float, actual: float,
           tolerance: float) -> None:
    delta = actual - expected
    if abs(delta) > tolerance:
        out.append({"field": field, "expected": round(expected, 4),
                    "actual": round(actual, 4), "delta": round(delta, 4)})


def reconcile(tolerance: float = TOLERANCE) -> dict:
    """Run every check. Returns {ok, n_checks, discrepancies, as_of, notes}."""
    discrepancies: list[dict] = []
    notes: list[str] = []
    n_checks = 0
    replay = _replay_ledger()

    # ── 1. Broker book vs ledger replay ──────────────────────────────────
    state_path = _broker_state_path()
    broker = None
    if state_path.exists():
        try:
            broker = json.loads(state_path.read_text())
        except Exception as exc:  # noqa: BLE001 — corrupt state IS a finding
            discrepancies.append({"field": "broker_state", "expected": "readable",
                                  "actual": f"unreadable ({exc})", "delta": None})
    else:
        notes.append(f"broker state absent ({state_path.name}) — skipped")
    if broker is not None:
        n_checks += 1
        _check(discrepancies, "cash(broker vs ledger)",
               replay["cash"], float(broker.get("cash", 0.0)), tolerance)
        bpos = broker.get("positions", {})
        for t in sorted(set(replay["positions"]) | set(bpos)):
            n_checks += 1
            lq = replay["positions"].get(t, {}).get("qty", 0.0)
            bq = float(bpos.get(t, {}).get("qty", 0.0))
            if abs(bq - lq) > _EPS_SHARES:
                discrepancies.append({"field": f"shares:{t}(broker vs ledger)",
                                      "expected": round(lq, 6),
                                      "actual": round(bq, 6),
                                      "delta": round(bq - lq, 6)})
            lc = replay["positions"].get(t, {}).get("cost")
            bc = bpos.get(t, {}).get("cost")
            if lc is not None and bc is not None:
                _check(discrepancies, f"cost_basis:{t}(broker vs ledger)",
                       lc, float(bc), tolerance)

    # ── 2. Positions table vs ledger replay ──────────────────────────────
    db_pos = {p["ticker"]: p for p in get_all_positions()
              if p.get("status") == "ACTIVE"}
    for t in sorted(set(replay["positions"]) | set(db_pos)):
        n_checks += 1
        lq = replay["positions"].get(t, {}).get("qty", 0.0)
        dq = float(db_pos.get(t, {}).get("shares", 0.0) or 0.0)
        if abs(dq - lq) > _EPS_SHARES:
            discrepancies.append({"field": f"shares:{t}(db vs ledger)",
                                  "expected": round(lq, 6),
                                  "actual": round(dq, 6),
                                  "delta": round(dq - lq, 6)})

    # ── 3. Realized YTD: stored-column computation vs replayed WACC ──────
    n_checks += 1
    _check(discrepancies, "realized_ytd(column vs replay)",
           replay["realized_ytd"], compute_realized_pnl_ytd(), tolerance)

    # ── 4. Today's snapshot vs the live book ─────────────────────────────
    snaps = get_portfolio_snapshots(days=7)
    snap = snaps[-1] if snaps else None
    today = datetime.now().date().isoformat()
    if snap and str(snap.get("snapshot_date", ""))[:10] == today:
        if broker is not None:
            n_checks += 1
            _check(discrepancies, "cash(snapshot vs broker)",
                   float(broker.get("cash", 0.0)),
                   float(snap.get("cash") or 0.0), tolerance)
        n_checks += 1
        _check(discrepancies, "realized_ytd(snapshot vs replay)",
               replay["realized_ytd"],
               float(snap.get("realized_pnl_ytd") or 0.0), tolerance)
        # Unrealized: positions-table marks (the same marks the monitor used).
        unreal = sum(
            (float(p.get("current_price") or p.get("cost_basis") or 0.0)
             - float(p.get("cost_basis") or 0.0)) * float(p.get("shares") or 0.0)
            for p in db_pos.values())
        n_checks += 1
        _check(discrepancies, "unrealized(snapshot vs positions-table)",
               unreal, float(snap.get("unrealized_pnl") or 0.0), tolerance)
        # Book identity: total == cash + invested (as stored).
        n_checks += 1
        _check(discrepancies, "identity(total = cash + invested)",
               float(snap.get("cash") or 0.0) + float(snap.get("invested_value") or 0.0),
               float(snap.get("total_value") or 0.0), tolerance)
    else:
        notes.append("no same-day snapshot — snapshot checks skipped")

    ok = not discrepancies
    return {"ok": ok, "n_checks": n_checks, "discrepancies": discrepancies,
            "notes": notes, "as_of": datetime.now().isoformat(timespec="seconds")}
