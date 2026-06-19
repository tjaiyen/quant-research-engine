"""Seed cockpit's prices/fundamentals tables from `screener/data/holdings.json`.

This task makes every ticker in the screener's full 220-stock universe
analyzable through the cockpit's Technical / Fundamental / ARS tabs (the
ticker-picker is driven by the `tickers` table; once a ticker is in there
with prices + fundamentals, the tabs work automatically).

Usage:
    # First-time bootstrap (~30 min cold cache, ~5 min when most are present)
    python -m tasks.seed_universe --full

    # Daily refresh — top-up tickers whose prices are >1 day old
    python -m tasks.seed_universe --refresh

    # Refresh fundamentals only (doesn't touch price history)
    python -m tasks.seed_universe --refresh --fundamentals-only

    # Programmatic use (called from auto_trader/scripts/pre_run_screener.py)
    from tasks.seed_universe import refresh
    refresh()

Design:
  * Reads `screener/data/holdings.json` for the universe (220 tickers).
  * Imports the SAME data providers + db helpers that `tasks/refresh_prices.py`
    (FROZEN) uses internally — no change to the frozen file.
  * Per-ticker error handling: one bad ticker logs + continues.
  * Inter-batch delay (2s default) to avoid yfinance rate limits.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from data_providers.yfinance_provider import (
    ProviderError,
    TickerNotFound,
    fetch_daily_adjusted,
    fetch_fundamentals,
)
from utils.db import (
    fetch_prices,
    init_db,
    list_tickers,
    mark_ticker_refreshed,
    upsert_fundamentals,
    upsert_prices,
    upsert_ticker,
)
from utils.logging_setup import get_logger

log = get_logger(__name__)

# Where the screener's universe definition lives.
HOLDINGS_PATH = Path(__file__).resolve().parent.parent / "screener" / "data" / "holdings.json"

# yfinance rate-limit guards
INTER_TICKER_DELAY_SEC = 0.5  # tight loop, generous default
INTER_BATCH_DELAY_SEC = 2.0   # bigger pause every BATCH_SIZE tickers
BATCH_SIZE = 20

# How recent a ticker's price data must be in --refresh mode to skip refetching
REFRESH_FRESHNESS_HOURS = 24


def load_universe() -> list[str]:
    """Return the flat list of unique tickers from holdings.json."""
    with HOLDINGS_PATH.open() as f:
        holdings = json.load(f)
    seen: set[str] = set()
    out: list[str] = []
    for sector, tickers in holdings.items():
        if sector.startswith("_"):  # skip _meta
            continue
        if not isinstance(tickers, list):
            continue
        for t in tickers:
            t = str(t).upper().strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def _ticker_is_fresh(ticker: str, max_age_hours: int = REFRESH_FRESHNESS_HOURS) -> bool:
    """True if the ticker has price rows newer than max_age_hours."""
    try:
        df = fetch_prices(ticker)
    except Exception:
        return False
    if df is None or df.empty:
        return False
    last = df.index.max()
    if hasattr(last, "tz_localize") and last.tz is None:
        # treat naive index as UTC
        last = last.tz_localize("UTC")
    elif hasattr(last, "tz_convert"):
        last = last.tz_convert("UTC")
    age = datetime.now(timezone.utc) - last.to_pydatetime()
    return age <= timedelta(hours=max_age_hours)


def _fetch_company_name(symbol: str) -> str | None:
    """Best-effort yfinance call for the human-readable company name.

    The frozen ``data_providers.yfinance_provider.fetch_fundamentals`` doesn't
    return name today, so we fetch it directly here. Cost: one extra
    yfinance call per ticker during seed (~1 sec). Acceptable for the
    picker UX. Returns None on any failure (picker falls back to ticker).
    """
    try:
        import yfinance as yf

        info = yf.Ticker(symbol).info or {}
        for key in ("longName", "shortName", "displayName"):
            val = info.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
        return None
    except Exception:
        return None


def _seed_one(
    symbol: str,
    *,
    full: bool,
    fetch_fund: bool,
) -> tuple[bool, int]:
    """Seed a single ticker's prices + (optionally) fundamentals + name.

    Returns ``(ok, price_rows_written)``. Errors log + return ``(False, 0)``;
    the caller's outer loop continues.
    """
    upsert_ticker(symbol)

    rows = 0
    try:
        df = fetch_daily_adjusted(
            symbol, output_size="full" if full else "compact"
        )
        rows = upsert_prices(symbol, df)
        mark_ticker_refreshed(symbol, status="ok")
        log.info("  %s prices: %d rows", symbol, rows)
    except TickerNotFound:
        log.warning("  %s: ticker not found — skipping", symbol)
        mark_ticker_refreshed(symbol, status="error:not_found")
        return False, 0
    except (ProviderError, Exception) as exc:
        log.warning("  %s: price fetch failed (%s)", symbol, exc)
        mark_ticker_refreshed(symbol, status=f"error:{type(exc).__name__}")
        return False, 0

    if fetch_fund:
        try:
            snap = fetch_fundamentals(symbol)
            upsert_fundamentals(symbol, snap)
            log.info(
                "  %s fundamentals: pe=%s sector=%s",
                symbol,
                snap.get("pe"),
                snap.get("sector"),
            )
        except Exception as exc:
            log.warning("  %s: fundamentals fetch failed (%s)", symbol, exc)
            # NOT fatal — keep going

        # Phase M Slice 4: also persist company name so the picker can
        # render "AAPL · Apple Inc". Best-effort; missing name not fatal.
        try:
            name = _fetch_company_name(symbol)
            if name:
                upsert_ticker(symbol, name=name)
                log.debug("  %s name: %s", symbol, name)
        except Exception as exc:
            log.debug("  %s: name fetch failed (%s)", symbol, exc)

    return True, rows


def seed(
    symbols: Iterable[str] | None = None,
    *,
    full: bool = True,
    fetch_fund: bool = True,
    skip_fresh: bool = False,
    inter_delay: float = INTER_TICKER_DELAY_SEC,
) -> dict:
    """Programmatic entry point. Seeds every ticker in ``symbols``.

    Args:
        symbols: iterable of tickers, or None to use the full universe.
        full: True for 2-year history (bootstrap); False for ~100 days
            (compact / daily refresh).
        fetch_fund: also refresh fundamentals.
        skip_fresh: skip tickers with prices fresher than 24h (used by
            ``refresh()`` to be cheap).
        inter_delay: per-ticker sleep to keep yfinance happy.

    Returns:
        ``{ok, failed, skipped, total_price_rows}``.
    """
    init_db()
    universe = list(symbols) if symbols is not None else load_universe()
    log.info(
        "seed_universe: %d tickers | full=%s funds=%s skip_fresh=%s",
        len(universe), full, fetch_fund, skip_fresh,
    )

    ok = 0
    failed = 0
    skipped = 0
    total_rows = 0

    for i, sym in enumerate(universe):
        if skip_fresh and _ticker_is_fresh(sym):
            log.debug("  %s: fresh — skipping", sym)
            skipped += 1
            continue

        success, rows = _seed_one(sym, full=full, fetch_fund=fetch_fund)
        if success:
            ok += 1
            total_rows += rows
        else:
            failed += 1

        # Rate-limit pacing
        if i < len(universe) - 1:
            time.sleep(inter_delay)
            if (i + 1) % BATCH_SIZE == 0:
                log.info(
                    "  --- batch boundary (%d/%d) — pausing %.1fs ---",
                    i + 1, len(universe), INTER_BATCH_DELAY_SEC,
                )
                time.sleep(INTER_BATCH_DELAY_SEC)

    summary = {
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "total_price_rows": total_rows,
    }
    log.info("seed_universe done: %s", summary)
    return summary


def refresh(*, fetch_fund: bool = True) -> dict:
    """Cheap incremental refresh — only re-fetches tickers >24h stale.

    Used by ``auto_trader/scripts/pre_run_screener.py`` after the weekly
    screener run.
    """
    return seed(
        symbols=None,
        full=False,        # compact = recent ~100 days, much faster
        fetch_fund=fetch_fund,
        skip_fresh=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed cockpit prices+fundamentals from the screener universe."
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Fetch full 2y history. Default is compact (~100 days, daily refresh).",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Refresh mode: skip tickers whose prices are <24h old. Implies compact.",
    )
    parser.add_argument(
        "--fundamentals-only", action="store_true",
        help="Skip price refresh; only update the fundamentals table.",
    )
    parser.add_argument(
        "--names-only", action="store_true",
        help="Backfill tickers.name via yfinance only — no prices, no fundamentals.",
    )
    parser.add_argument(
        "--no-fundamentals", action="store_true",
        help="Skip fundamentals (price history only).",
    )
    parser.add_argument(
        "symbols", nargs="*", help="Optional specific tickers (overrides holdings.json).",
    )
    args = parser.parse_args(argv)

    fetch_fund = not args.no_fundamentals

    if args.names_only:
        # Phase M: backfill names cheaply (~5 min for 220 tickers, no price
        # writes). Useful when prices are already current but the picker
        # is missing names.
        init_db()
        universe = (
            [s.upper() for s in args.symbols] if args.symbols else load_universe()
        )
        log.info("Names-only refresh for %d tickers", len(universe))
        ok = 0
        failed = 0
        for i, sym in enumerate(universe):
            try:
                name = _fetch_company_name(sym)
                if name:
                    upsert_ticker(sym, name=name)
                    log.info("  %s: %s", sym, name)
                    ok += 1
                else:
                    log.warning("  %s: no name returned", sym)
                    failed += 1
            except Exception as exc:
                log.warning("  %s: name fetch failed (%s)", sym, exc)
                failed += 1
            if i < len(universe) - 1:
                time.sleep(INTER_TICKER_DELAY_SEC)
        log.info("Names-only done: ok=%d failed=%d", ok, failed)
        print()
        print(f"Names backfilled: {ok}/{ok+failed}")
        return 0

    if args.fundamentals_only:
        # Fundamentals-only path: walk universe, only call upsert_fundamentals.
        init_db()
        universe = (
            [s.upper() for s in args.symbols] if args.symbols else load_universe()
        )
        log.info("Fundamentals-only refresh for %d tickers", len(universe))
        ok = 0
        failed = 0
        for i, sym in enumerate(universe):
            try:
                snap = fetch_fundamentals(sym)
                upsert_fundamentals(sym, snap)
                log.info("  %s: pe=%s sector=%s", sym, snap.get("pe"), snap.get("sector"))
                ok += 1
            except Exception as exc:
                log.warning("  %s: fundamentals failed (%s)", sym, exc)
                failed += 1
            if i < len(universe) - 1:
                time.sleep(INTER_TICKER_DELAY_SEC)
        log.info("Fundamentals-only done: ok=%d failed=%d", ok, failed)
        return 0

    symbols = [s.upper() for s in args.symbols] if args.symbols else None
    summary = seed(
        symbols=symbols,
        full=args.full,
        fetch_fund=fetch_fund,
        skip_fresh=args.refresh,
    )

    print()
    print("=" * 60)
    print("SEED UNIVERSE — SUMMARY")
    print("=" * 60)
    print(f"  ok:               {summary['ok']}")
    print(f"  failed:           {summary['failed']}")
    print(f"  skipped (fresh):  {summary['skipped']}")
    print(f"  total price rows: {summary['total_price_rows']}")
    print()
    print(f"Tickers in DB now: {len(list_tickers())}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
