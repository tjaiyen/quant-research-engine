"""Fetch + grade per-company health; cache to company_health (Phase 21).

Usage:
    python -m tasks.refresh_health                 # held positions (default)
    python -m tasks.refresh_health AAPL GD         # specific tickers
    python -m tasks.refresh_health --universe      # the whole 220-stock universe
    python -m tasks.refresh_health --limit 10      # first N (quick test)

Graceful: a ticker with no usable yfinance quality data is cached as UNAVAILABLE;
nothing here ever blocks a trade (health is a monitoring overlay, not a signal).
"""
from __future__ import annotations

import argparse
import sys
import time

from screener.health.scorer import score_ticker_health
from utils.db import init_db, upsert_health
from utils.logging_setup import get_logger

log = get_logger(__name__)
_DELAY = 0.3   # gentle pacing for the yfinance .info calls


def _held_tickers() -> list[str]:
    try:
        from auto_trader.state.portfolio_db import get_all_positions
        return sorted({p["ticker"] for p in get_all_positions()})
    except Exception as exc:
        log.warning("could not read held positions (%s)", exc)
        return []


def _sector_for(ticker: str) -> str | None:
    try:
        from utils.db import fetch_latest_fundamentals
        f = fetch_latest_fundamentals(ticker)
        return (f or {}).get("sector")
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh company-health cache.")
    parser.add_argument("tickers", nargs="*", help="specific tickers (default: held positions)")
    parser.add_argument("--universe", action="store_true", help="score the whole universe")
    parser.add_argument("--limit", type=int, default=None, help="only the first N tickers")
    args = parser.parse_args(argv)

    init_db()
    if args.tickers:
        names = [t.upper() for t in args.tickers]
    elif args.universe:
        from tasks.seed_universe import load_universe
        names = load_universe()
    else:
        names = _held_tickers()
    if args.limit:
        names = names[: args.limit]
    if not names:
        print("No tickers to score (no held positions? pass tickers or --universe).")
        return 0
    log.info("Health refresh for %d tickers", len(names))

    ok = unavailable = 0
    for i, sym in enumerate(names):
        try:
            snap = score_ticker_health(sym, _sector_for(sym))
            upsert_health(sym, snap)
            if snap["health_label"] == "UNAVAILABLE":
                unavailable += 1
            else:
                ok += 1
            log.info("  %s: %s (%s, %d/%d floors)", sym, snap["health_label"],
                     snap["health_score"], snap["floors_passed"], snap["floors_total"])
        except Exception as exc:
            log.warning("  %s: health failed (%s)", sym, exc)
            unavailable += 1
        if i < len(names) - 1:
            time.sleep(_DELAY)

    print(f"Health cached: {ok} scored, {unavailable} unavailable / {len(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
