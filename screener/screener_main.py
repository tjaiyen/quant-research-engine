"""Screener orchestrator (Phase 10).

Spec invariants:
  - M5: log file goes to ``OUTPUT_DIR``, not the project root.
  - KeyboardInterrupt safe shutdown (no partial writes).
  - Estimated runtime printed before sector scoring begins.

Usage::

    python -m screener.screener_main           # use cached HMM if fresh
    python -m screener.screener_main --retrain # force HMM retrain

Output:
  * ``screener/output/runs/screener_output_YYYYMMDD.json``
  * gzip audit at ``screener/output/runs/audit/signal_audit_YYYYMMDD.json.gz``
  * SQLite rows in ``screener_results`` + ``screener_runs`` tables (cockpit
    Screener tab reads these)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from screener.config import LOG_LEVEL, OUTPUT_DIR, SECTOR_ETFS, STOCKS_PER_SECTOR
from screener.engine.industry_ranker import rank_all_sectors
from screener.output.results_formatter import (
    format_results,
    save_audit_trail,
    to_json,
    write_to_sqlite,
)
from screener.regime.hmm_predictor import get_regime
from screener.regime.hmm_trainer import should_retrain, train_hmm

# M5: log to OUTPUT_DIR, not project root
_today = datetime.now(timezone.utc).strftime("%Y%m%d")
os.makedirs(OUTPUT_DIR, exist_ok=True)
_log_path = Path(OUTPUT_DIR) / f"screener_run_{_today}.log"

# Configure root logger only once (idempotent on re-imports / re-runs)
_handlers: list[logging.Handler] = []
_root = logging.getLogger()
if not any(getattr(h, "_screener_log", False) for h in _root.handlers):
    sh = logging.StreamHandler()
    sh._screener_log = True  # type: ignore[attr-defined]
    fh = logging.FileHandler(str(_log_path))
    fh._screener_log = True  # type: ignore[attr-defined]
    _handlers = [sh, fh]
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=_handlers,
    )

logger = logging.getLogger(__name__)


def run_screener(force_retrain: bool = False, persist_to_db: bool = True) -> dict:
    """Run the full screener pipeline and persist outputs.

    Args:
        force_retrain: if True, retrain HMM unconditionally.
        persist_to_db: if True, also write rows to the cockpit SQLite tables.

    Returns:
        The ``format_results`` dict.
    """
    start = time.time()
    logger.info("=" * 60)
    logger.info("REGIME-AWARE STOCK SCREENER v3.0 — START")
    logger.info("Run date: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Log: %s", _log_path)
    logger.info("=" * 60)

    # Step 1 — HMM retrain if needed
    if force_retrain or should_retrain():
        logger.info("Retraining HMM model…")
        train_hmm()
    else:
        logger.info("HMM model is current — skipping retrain")

    # Step 2 — Regime detection
    logger.info("Detecting market regime…")
    regime_data = get_regime()
    logger.info(
        "Regime: %s (confidence=%.1f%%, stable=%s)",
        regime_data["regime"].upper(),
        regime_data["confidence"] * 100.0,
        regime_data.get("stable", False),
    )

    # Step 3 — Sector scoring
    n_stocks = len(SECTOR_ETFS) * STOCKS_PER_SECTOR
    est_min = n_stocks * 5 / 60.0  # ~5s per stock estimate (worst case)
    logger.info(
        "Scoring %d sectors (~%d stocks, est. %.0f min)…",
        len(SECTOR_ETFS), n_stocks, est_min,
    )
    ranked_sectors = rank_all_sectors(regime_data)

    # Step 4 — Format + save
    results = format_results(ranked_sectors, regime_data)
    output_path = os.path.join(OUTPUT_DIR, f"screener_output_{_today}.json")
    to_json(results, path=output_path)
    save_audit_trail(ranked_sectors, results)

    elapsed = time.time() - start
    if persist_to_db:
        try:
            write_to_sqlite(results, elapsed_seconds=elapsed)
        except Exception as exc:
            logger.error("SQLite persist failed: %s", exc)

    logger.info("Pipeline complete: %.1fs (%.1f min)", elapsed, elapsed / 60.0)
    logger.info(
        "Passed veto: %d / %d",
        results["summary"]["total_passed_veto"], results["summary"]["total_screened"],
    )
    logger.info("Veto rate: %.1f%%", results["summary"]["veto_rate_pct"])
    logger.info("Output: %s", output_path)
    return results


def print_summary(results: dict) -> None:
    """Human-readable sector summary to console."""
    print("\n" + "=" * 60)
    print(f"  SCREENER RESULTS — {results['generated_at'][:10]}")
    print(
        f"  REGIME: {results['regime']['label'].upper()} "
        f"({results['regime']['confidence']:.1%} confidence, "
        f"stable={results['regime']['stable']})"
    )
    print("=" * 60)
    for sector, stocks in results["sectors"].items():
        print(f"\n  {sector}")
        if not stocks:
            print("    ⚠  No stocks passed veto")
            continue
        for s in stocks:
            relaxed = " [RELAXED]" if s.get("veto_relaxed") else ""
            print(
                f"    {s['rank']}. {s['ticker']:6s}  "
                f"score={s['composite_score']:.4f}{relaxed}"
            )
    print("\n  TOP 5 OVERALL:")
    for s in results["summary"]["top_overall"]:
        print(
            f"    {s['rank']}. {s['ticker']:6s} "
            f"({s.get('sector', '')}) "
            f"score={s['composite_score']:.4f}"
        )


if __name__ == "__main__":
    try:
        force = "--retrain" in sys.argv
        results = run_screener(force_retrain=force)
        print_summary(results)
    except KeyboardInterrupt:
        logger.warning(
            "Pipeline interrupted. Partial results NOT saved. Model state unchanged."
        )
        sys.exit(0)
    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        sys.exit(1)
