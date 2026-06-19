"""Refresh daily prices for the configured watchlist.

Usage:
    python -m tasks.refresh_prices                 # refresh WATCHLIST from .env
    python -m tasks.refresh_prices AAPL MSFT       # refresh specific symbols
    python -m tasks.refresh_prices --full AAPL     # fetch 20y history (first run)
"""
from __future__ import annotations

import argparse
import sys

from data_providers.yfinance_provider import (
    ProviderError,
    TickerNotFound,
    fetch_daily_adjusted,
    fetch_fundamentals,
)
from utils.db import upsert_fundamentals


class RateLimited(Exception):
    """Placeholder — yfinance has no explicit rate-limit signal."""
from utils.config import load_settings
from utils.db import (
    init_db,
    list_tickers,
    mark_ticker_refreshed,
    upsert_prices,
    upsert_ticker,
)
from utils.logging_setup import get_logger

log = get_logger(__name__)


def refresh_one(symbol: str, full: bool = False) -> int:
    """Fetch and upsert one symbol. Returns rows written."""
    upsert_ticker(symbol)
    try:
        df = fetch_daily_adjusted(symbol, output_size="full" if full else "compact")
    except TickerNotFound as e:
        log.error("Ticker not found: %s (%s)", symbol, e)
        mark_ticker_refreshed(symbol, status=f"error:not_found")
        return 0
    except RateLimited as e:
        log.error("Rate limited on %s: %s", symbol, e)
        mark_ticker_refreshed(symbol, status="error:rate_limited")
        raise
    except (ProviderError, Exception) as e:
        log.exception("Failed fetching %s", symbol)
        mark_ticker_refreshed(symbol, status=f"error:{type(e).__name__}")
        return 0

    rows = upsert_prices(symbol, df)
    mark_ticker_refreshed(symbol, status="ok")
    log.info("Refreshed %s: %d rows", symbol, rows)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh daily prices.")
    parser.add_argument("symbols", nargs="*", help="Optional specific symbols.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Fetch full 20y history (first run). Default is compact (~100 days).",
    )
    parser.add_argument(
        "--fundamentals",
        action="store_true",
        help="Also refresh fundamentals snapshot for each symbol.",
    )
    args = parser.parse_args(argv)

    init_db()
    settings = load_settings()
    symbols = [s.upper() for s in args.symbols] or list(settings.watchlist)
    log.info("Refreshing %d symbols: %s", len(symbols), symbols)

    total = 0
    for sym in symbols:
        try:
            total += refresh_one(sym, full=args.full)
        except RateLimited:
            log.error("Stopping early due to rate limit.")
            break

    if args.fundamentals:
        log.info("Refreshing fundamentals for %d symbols", len(symbols))
        for sym in symbols:
            try:
                snap = fetch_fundamentals(sym)
                upsert_fundamentals(sym, snap)
                log.info(
                    "Fundamentals %s: PE=%s fwdPE=%s PS=%s sector=%s",
                    sym, snap.get("pe"), snap.get("forward_pe"),
                    snap.get("ps"), snap.get("sector"),
                )
            except Exception:
                log.exception("Fundamentals failed for %s", sym)

    log.info("Done. Total price rows upserted: %d", total)
    print(list_tickers().to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
