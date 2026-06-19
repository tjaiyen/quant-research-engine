"""Bridge: run cockpit's screener and copy its JSON to the auto_trader cache.

This is the only place where ``screener.screener_main`` is invoked from
the auto_trader. The monthly cycle reads the JSON cache directly — never
re-runs the screener inline.

Usage::

    python -m auto_trader.scripts.pre_run_screener           # use cached HMM if fresh
    python -m auto_trader.scripts.pre_run_screener --retrain # force HMM retrain
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from auto_trader.config import LOG_DIR, LOG_LEVEL, get_screener_cache_path
from auto_trader.utils import setup_logging

logger = logging.getLogger(__name__)


def _latest_screener_json() -> Path | None:
    """Find the most recent screener_output_*.json from cockpit Phase J."""
    runs_dir = Path("screener/output/runs")
    if not runs_dir.exists():
        return None
    candidates = sorted(runs_dir.glob("screener_output_*.json"))
    return candidates[-1] if candidates else None


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run cockpit screener and refresh auto_trader cache.")
    parser.add_argument("--retrain", action="store_true", help="Force HMM retrain")
    parser.add_argument("--skip-run", action="store_true",
                        help="Skip the screener run, just copy the latest JSON")
    args = parser.parse_args(argv)

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    setup_logging(f"{LOG_DIR}pre_run_screener_{today}.log", LOG_LEVEL)

    if not args.skip_run:
        try:
            from screener.screener_main import run_screener

            logger.info("Running cockpit screener (force_retrain=%s)…", args.retrain)
            run_screener(force_retrain=args.retrain, persist_to_db=True)
        except Exception as exc:
            logger.exception("Screener run failed: %s", exc)
            return 1

        # Phase L: top up cockpit prices+fundamentals for the full 220-stock
        # universe so every screener pick is analyzable in the Technical /
        # Fundamental / ARS tabs. Skips tickers fresher than 24h so this is
        # cheap once the bootstrap has populated the DB.
        try:
            from tasks.seed_universe import refresh as seed_refresh

            logger.info("Refreshing cockpit universe (Phase L)…")
            summary = seed_refresh(fetch_fund=True)
            logger.info("Universe refresh complete: %s", summary)
        except Exception as exc:
            # Non-fatal — the screener already succeeded. The cockpit picker
            # just won't gain newly-rotated tickers this cycle.
            logger.warning("Universe refresh failed (non-fatal): %s", exc)

    src = _latest_screener_json()
    if src is None:
        logger.error(
            "No screener JSON found at screener/output/runs/. "
            "Run cockpit's screener first, or check the path."
        )
        return 2

    cache_path = Path(get_screener_cache_path())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, cache_path)

    # Stamp _cached_at so the staleness check has something to compare to
    try:
        with cache_path.open() as f:
            data = json.load(f)
        data["_cached_at"] = datetime.now(timezone.utc).isoformat()
        with cache_path.open("w") as f:
            json.dump(data, f, default=str)
    except Exception as exc:
        logger.warning("Failed to stamp _cached_at on cache (%s); cache still usable", exc)

    logger.info("Screener cache refreshed: %s ← %s", cache_path, src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
