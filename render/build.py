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

    # 3) Daily journal — one note per date that has fills.
    by_date: dict[str, list[dict]] = {}
    for t in paper["trades"]:
        d = str(t.get("executed_at", ""))[:10]
        if d:
            by_date.setdefault(d, []).append(t)
    for d, trades in by_date.items():
        atomic_write(root / "Journal" / f"{d}.md", notes.journal_note(d, trades))
        written.append(f"Journal/{d}.md")

    # 4) Performance equity curve.
    atomic_write(root / "Performance.md", notes.performance_note(snapshots))
    written.append("Performance.md")

    # 5) Dashboard (top-level Dataview surface).
    atomic_write(
        root / "Dashboard.md",
        notes.dashboard_note(regime, latest_snapshot, top_picks, now_iso),
    )
    written.append("Dashboard.md")

    # 6) Scorecard — past picks vs actual forward returns (best-effort, DB-only).
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

    return {
        "vault": str(root),
        "written": written,
        "pruned_positions": pruned,
        "had_screener_run": results is not None,
        "n_positions": len(paper["positions"]),
        "n_snapshots": len(snapshots),
    }


__all__ = ["build_all", "latest_screener_results"]
