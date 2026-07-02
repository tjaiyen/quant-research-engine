"""render/build.py — read the off-Drive cache + paper ledger, write vault notes.

The only IO half of the renderer. Reads are best-effort: a fresh install with
no screener run and no paper cycle still produces a coherent (if sparse) set of
notes rather than crashing. All writes are atomic and confined to the
tracker-owned ``90 Tracker/`` folder.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from render import notes
from render.markdown import atomic_write, tracker_dir

logger = logging.getLogger(__name__)

# Dual-class share redundancy: a run scored BEFORE the universe dedup may have
# stored both classes (same company). Collapse to the kept class for DISPLAY only
# — stored history is untouched (the scorecard still grades the original rows).
# Mirrors render/html.py:_display_sector (display-layer remap, not a data change).
_CANONICAL_SHARE_CLASS = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA"}


def _collapse_share_classes(tickers: list[str]) -> list[str]:
    """Map redundant share classes to the kept class, de-duping in place order."""
    out: list[str] = []
    for t in tickers:
        c = _CANONICAL_SHARE_CLASS.get(t, t)
        if c not in out:
            out.append(c)
    return out


# ── Reads (best-effort) ──────────────────────────────────────────────────────

def latest_screener_results() -> dict | None:
    """Most-recent screener run as a ``format_results`` dict, or None."""
    try:
        from utils.db import get_conn

        with get_conn() as conn:
            row = conn.execute(
                "SELECT payload_json FROM screener_runs ORDER BY run_at DESC LIMIT 1"
            ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as exc:  # table missing / no DB yet
        logger.debug("no screener run available: %s", exc)
    return None


def _paper_reads() -> dict:
    """Pull positions / trades / snapshots from the paper ledger (best-effort)."""
    out = {"positions": [], "trades": [], "snapshots": []}
    try:
        from auto_trader.state import portfolio_db as pdb

        try:
            pdb.initialize_db()  # idempotent; creates the store DB if absent
        except Exception as exc:
            logger.debug("paper ledger init skipped: %s", exc)
        out["positions"] = pdb.get_all_positions()
        out["trades"] = pdb.get_trade_history(limit=500)
        out["snapshots"] = pdb.get_portfolio_snapshots(days=365)
    except Exception as exc:
        logger.debug("paper ledger unavailable: %s", exc)
    return out


# ── Build ────────────────────────────────────────────────────────────────────

def _prune_stale(folder: Path, keep: set[str]) -> int:
    """Delete tracker-owned .md files in ``folder`` not in ``keep`` (filenames)."""
    if not folder.exists():
        return 0
    removed = 0
    for f in folder.glob("*.md"):
        if f.name not in keep:
            try:
                f.unlink()
                removed += 1
            except OSError as exc:
                logger.debug("could not prune %s: %s", f, exc)
    return removed


_STORE = Path(__file__).resolve().parent.parent / "store"
_COPILOT_SIDECAR = _STORE / "last_copilot.json"
_RUN_BEACON = _STORE / "last_run.json"
_TOURNAMENT_SIDECAR = _STORE / "last_tournament.json"
_SIGNAL_LAB_SIDECAR = _STORE / "last_signal_lab.json"


def _latest_copilot() -> dict:
    """The last cached co-pilot take (written by `track copilot`), or {}."""
    try:
        return json.loads(_COPILOT_SIDECAR.read_text())
    except Exception:
        return {}


def _latest_tournament() -> dict:
    """The last tournament leaderboard (written by `track tournament`), or {}."""
    try:
        return json.loads(_TOURNAMENT_SIDECAR.read_text())
    except Exception:
        return {}


def _latest_signal_lab() -> dict:
    """The last signal-lab diagnosis (written by `track signal-lab`), or {}."""
    try:
        return json.loads(_SIGNAL_LAB_SIDECAR.read_text())
    except Exception:
        return {}


def _latest_run() -> dict:
    """The last scheduled-run health beacon (written by scheduled-run.sh), or {}.

    Adds `stale`/`age_h` so the dashboard can flag a missed cadence (the
    dead-man's-switch for silent launchd failures).
    """
    try:
        d = json.loads(_RUN_BEACON.read_text())
        ended = datetime.strptime(str(d.get("ended"))[:19], "%Y-%m-%dT%H:%M:%S")
        age_h = (datetime.now() - ended).total_seconds() / 3600.0
        d["age_h"], d["stale"] = round(age_h, 1), age_h > 36
        return d
    except Exception:
        return {}


def _latest_recon() -> dict:
    """The most recent RECON_OK / RECON_DRIFT event (Phase 29), or {}.

    The reconciler replays the trade ledger and compares every P&L surface;
    the dashboard shows an amber banner when the latest run found drift.
    """
    try:
        from auto_trader.state.portfolio_db import get_system_events
        for ev in get_system_events(limit=200):
            if ev.get("event_type") in ("RECON_OK", "RECON_DRIFT"):
                return {"ok": ev["event_type"] == "RECON_OK",
                        "at": str(ev.get("event_time", ""))[:16],
                        "discrepancies": (ev.get("details") or {}).get(
                            "discrepancies", [])}
    except Exception as exc:  # noqa: BLE001
        logger.debug("recon status unreadable: %s", exc)
    return {}


def _decisions(trades: list[dict], max_entries: int = 40) -> list[dict]:
    """Merge screens + trades + daily-monitor events into a typed, sorted feed."""
    decisions: list[dict] = []

    # Weekly screens — populated immediately (the autonomous screen already runs).
    try:
        from utils.db import fetch_screener_picks, list_screener_runs
        for run in list_screener_runs(limit=20):
            run_at = str(run.get("run_at"))
            picks = sorted(fetch_screener_picks(run_at),
                           key=lambda p: -(p.get("composite_score") or 0))
            ranked = [p["ticker"] for p in picks if p.get("top_overall_rank")] \
                or [p["ticker"] for p in picks]
            top = [{"ticker": t} for t in _collapse_share_classes(ranked)[:5]]
            decisions.append({
                "when": run_at, "kind": "screen",
                "regime": run.get("regime_label"), "regime_conf": run.get("regime_confidence"),
                "total_screened": run.get("total_screened"),
                "total_passed": run.get("total_passed_veto"),
                "veto_rate": run.get("veto_rate_pct"), "top": top,
            })
    except Exception as exc:
        logger.debug("screen decisions skipped: %s", exc)

    # Trades grouped by date (populates after the monthly buy).
    by_date: dict[str, list[dict]] = {}
    for t in trades:
        d = str(t.get("executed_at", ""))[:10]
        if d:
            by_date.setdefault(d, []).append(t)
    for d, ts in by_date.items():
        decisions.append({"when": ts[0].get("executed_at", d), "kind": "trades", "trades": ts})

    # Daily-monitor heartbeat — one entry per day (events are newest-first, so
    # the first seen for a date is the latest run).
    try:
        from auto_trader.state.portfolio_db import get_system_events
        seen_days: set[str] = set()
        for e in get_system_events(limit=120):
            if e.get("event_type") != "DAILY_MONITOR":
                continue
            day = str(e.get("event_time", ""))[:10]
            if day in seen_days:
                continue
            seen_days.add(day)
            decisions.append({"when": e.get("event_time"), "kind": "daily",
                              "details": e.get("details", {})})
    except Exception as exc:
        logger.debug("event decisions skipped: %s", exc)

    decisions.sort(key=lambda x: str(x.get("when", "")), reverse=True)
    return decisions[:max_entries]


def fleet_reads() -> list[dict]:
    """Leaderboard rows for the strategy fleet — one per member, best-effort.

    Reads each member's ``portfolio.db`` DIRECTLY (plain sqlite3, read-only) so
    no env juggling touches the in-process flagship connection. A member with
    no book yet (pre-first-cycle) appears as a pending row with value=None.
    """
    import sqlite3

    try:
        from auto_trader.fleet import FLEET, member_dir
    except Exception as exc:                          # noqa: BLE001
        logger.debug("fleet reads skipped: %s", exc)
        return []
    root = Path(__file__).resolve().parents[1]
    rows: list[dict] = []
    for m in FLEET:
        db = (root / "store" / "portfolio.db" if m.get("kind") == "flagship"
              else member_dir(m["id"]) / "portfolio.db")
        row = {"id": m["id"], "label": m["label"], "kind": m.get("kind", "strategy"),
               "group": m.get("group"), "value": None, "pnl": None, "ret_pct": None,
               "spy_pct": None, "excess_pct": None, "n_positions": None,
               "since": None, "holdings": []}
        # exists() pre-check: a missing DB is a normal PENDING member (pre first
        # cycle) — don't rely on the ro-URI exception, which also masks a
        # genuinely corrupt DB behind the same silence.
        if not db.exists():
            rows.append(row)
            continue
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            snaps = conn.execute(
                "SELECT snapshot_date, total_value, benchmark_value, n_positions "
                "FROM portfolio_snapshots ORDER BY snapshot_date").fetchall()
            # Drill-down: every book's holdings (the fleet card expands each
            # row to its positions). Own guard — fixtures/older DBs may lack
            # the table; a live book without positions is simply [].
            try:
                for p in conn.execute(
                        "SELECT ticker, shares, cost_basis, current_price "
                        "FROM positions WHERE status='ACTIVE'").fetchall():
                    sh = float(p["shares"] or 0.0)
                    cost = float(p["cost_basis"] or 0.0)
                    price = p["current_price"]
                    val = sh * float(price) if price is not None else None
                    pnl = (float(price) - cost) * sh if price is not None else None
                    row["holdings"].append({
                        "t": p["ticker"], "shares": sh, "price": price,
                        "value": val, "pnl": pnl,
                        "pnl_pct": (pnl / (cost * sh) * 100.0)
                                   if (pnl is not None and cost and sh) else None})
                row["holdings"].sort(key=lambda h: -(h["value"] or 0.0))
            except Exception as exc:                  # noqa: BLE001
                logger.debug("fleet member %s holdings unreadable (%s)", m["id"], exc)
            conn.close()
            if snaps:
                first, last = snaps[0], snaps[-1]
                # Baseline = the $10k STARTING CAPITAL every book deposits —
                # NOT the first snapshot (the flagship's first snapshot was
                # $10,121, which made its fleet row read −$82.82 while the P&L
                # KPI correctly said +$38.41 — one book, two answers). All
                # surfaces now measure from the same 10,000.
                base = 10_000.0
                val = float(last["total_value"])
                # 'since' = inception label — books still start on different
                # dates (flagship 6/24, members 7/1).
                row.update(value=val, pnl=val - base,
                           ret_pct=(val / base - 1.0) * 100.0,
                           n_positions=last["n_positions"],
                           since=str(first["snapshot_date"])[:10])
                if first["benchmark_value"] and last["benchmark_value"]:
                    spy = (float(last["benchmark_value"])
                           / float(first["benchmark_value"]) - 1.0) * 100.0
                    row.update(spy_pct=spy, excess_pct=row["ret_pct"] - spy)
        except Exception as exc:                      # noqa: BLE001 — corrupt DB
            logger.warning("fleet member %s DB unreadable (%s)", m["id"], exc)
        rows.append(row)
    rows.sort(key=lambda r: (r["ret_pct"] is None, -(r["ret_pct"] or 0.0)))
    return rows


def build_all() -> dict:
    """Regenerate every tracker note. Returns a summary of what was written."""
    root = tracker_dir()
    written: list[str] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    results = latest_screener_results()
    paper = _paper_reads()
    snapshots = paper["snapshots"]
    latest_snapshot = snapshots[-1] if snapshots else {}

    # 1) Screener run + regime + dashboard picks (only when a run exists).
    regime = (results or {}).get("regime", {}) if results else {}
    top_picks = (results or {}).get("summary", {}).get("top_overall", []) if results else []
    if results:
        run_date = str(results.get("generated_at", now_iso))[:10]
        atomic_write(
            root / "Screener" / f"Run-{run_date}.md",
            notes.screener_run_note(results),
        )
        written.append(f"Screener/Run-{run_date}.md")
        atomic_write(root / "Regime.md", notes.regime_note(regime, results.get("generated_at", now_iso)))
        written.append("Regime.md")

    # 2) Open paper positions (one note each) + prune closed ones.
    pos_dir = root / "Positions"
    trades_by_ticker: dict[str, list[dict]] = {}
    for t in paper["trades"]:
        trades_by_ticker.setdefault(str(t.get("ticker", "")), []).append(t)
    keep: set[str] = set()
    for pos in paper["positions"]:
        fname = f"{pos['ticker']}.md"
        atomic_write(
            pos_dir / fname,
            notes.position_note(pos, trades_by_ticker.get(pos["ticker"])),
        )
        keep.add(fname)
        written.append(f"Positions/{fname}")
    pruned = _prune_stale(pos_dir, keep)

    # 3) Daily journal — one note per date that has fills (+ system events).
    events_by_date: dict[str, list[dict]] = {}
    try:
        from auto_trader.state.portfolio_db import get_system_events
        for e in get_system_events(limit=200):
            ed = str(e.get("event_time", ""))[:10]
            if ed:
                events_by_date.setdefault(ed, []).append(e)
    except Exception as exc:
        logger.debug("system events unavailable: %s", exc)
    by_date: dict[str, list[dict]] = {}
    for t in paper["trades"]:
        d = str(t.get("executed_at", ""))[:10]
        if d:
            by_date.setdefault(d, []).append(t)
    for d, trades in by_date.items():
        atomic_write(root / "Journal" / f"{d}.md",
                     notes.journal_note(d, trades, events=events_by_date.get(d)))
        written.append(f"Journal/{d}.md")

    # 4) Performance equity curve.
    atomic_write(root / "Performance.md", notes.performance_note(snapshots))
    written.append("Performance.md")

    # 4b) Decisions feed — first-person narrative of every autonomous move.
    decisions = _decisions(paper["trades"])
    last_move = notes._decision_text(decisions[0]) if decisions else None
    atomic_write(root / "Decisions.md", notes.agent_log_note(decisions))
    written.append("Decisions.md")

    # 5) Dashboard (top-level Dataview surface) — leads with the latest move.
    atomic_write(
        root / "Dashboard.md",
        notes.dashboard_note(regime, latest_snapshot, top_picks, now_iso, last_move),
    )
    written.append("Dashboard.md")

    # 6) Scorecard — past picks vs actual forward returns (best-effort, DB-only).
    scorecard = None
    try:
        from screener.backtest.scorecard import compute_scorecard

        scorecard = compute_scorecard()
        atomic_write(root / "Scorecard.md", notes.scorecard_note(scorecard, snapshots))
        written.append("Scorecard.md")

        # 7) Review deck — same data, as a presentable slide deck (U20).
        from render import slides

        atomic_write(root / "Review.md", slides.review_deck(
            regime, top_picks, scorecard, snapshots, as_of=now_iso))
        written.append("Review.md")
    except Exception as exc:
        logger.debug("scorecard/review skipped: %s", exc)

    # 8) Visual HTML dashboard (self-contained; auto-refreshing in a browser).
    try:
        from render import html as _html
        try:
            from utils.db import list_sentiment
            sentiment = list_sentiment()
        except Exception:
            sentiment = []
        try:
            from utils.db import ticker_names
            names = ticker_names()
        except Exception:
            names = {}
        try:  # company-health snapshot, joined with valuation + earnings
            from utils.db import (fetch_earnings, fetch_earnings_history,
                                  fetch_latest_fundamentals, list_health)
            health = []
            for h in list_health():
                hist = fetch_earnings_history(h["ticker"], limit=4)
                health.append({
                    **h,
                    "pe": (fetch_latest_fundamentals(h["ticker"]) or {}).get("pe"),
                    "next_earnings": fetch_earnings(h["ticker"]),
                    "last_surprise_pct": hist[0]["surprise_pct"] if hist else None,
                })
        except Exception:
            health = []
        fleet = fleet_reads()
        atomic_write(root / "Dashboard.html", _html.dashboard_html({
            "as_of": now_iso, "regime": regime, "top_picks": top_picks,
            "summary": (results or {}).get("summary"),
            "sectors": (results or {}).get("sectors"),
            "latest_snapshot": latest_snapshot, "snapshots": snapshots,
            "positions": paper["positions"], "sentiment": sentiment, "names": names,
            "health": health, "fleet": fleet,
            "decisions": [notes._decision_text(d) for d in decisions],
            "scorecard": scorecard, "copilot": _latest_copilot(),
            "last_run": _latest_run(), "tournament": _latest_tournament(),
            "signal_lab": _latest_signal_lab(), "recon": _latest_recon(),
        }))
        written.append("Dashboard.html")
    except Exception as exc:
        logger.debug("html dashboard skipped: %s", exc)

    # 9) Fleet leaderboard note (best-effort; empty pre-first-cycle is fine).
    try:
        atomic_write(root / "Fleet.md",
                     notes.fleet_note({"as_of": now_iso, "rows": fleet_reads()}))
        written.append("Fleet.md")
    except Exception as exc:
        logger.debug("fleet note skipped: %s", exc)

    return {
        "vault": str(root),
        "written": written,
        "pruned_positions": pruned,
        "had_screener_run": results is not None,
        "n_positions": len(paper["positions"]),
        "n_snapshots": len(snapshots),
    }


__all__ = ["build_all", "latest_screener_results"]
