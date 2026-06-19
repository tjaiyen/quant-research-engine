"""Refresh SPDR sector ETF prices + write sector_perf rows.

The 11 Select Sector SPDRs cover the full equity market. We compute returns
and relative strength vs SPY at multiple horizons and persist them to the
`sector_perf` table for Tab 5 (Sector Rotation).

Usage:
    python -m tasks.refresh_sectors                # default — refresh all
    python -m tasks.refresh_sectors --full         # full price history first run

Adds sector ETFs to the `tickers` table on first run. Does NOT touch the
WATCHLIST env var or any user holdings.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from data_providers.yfinance_provider import (
    ProviderError,
    TickerNotFound,
    fetch_daily_adjusted,
)
from utils.db import (
    fetch_prices,
    get_conn,
    init_db,
    mark_ticker_refreshed,
    upsert_prices,
    upsert_ticker,
)
from utils.logging_setup import get_logger

log = get_logger(__name__)


# 11 Select Sector SPDR ETFs + benchmark.
SECTOR_ETFS: tuple[tuple[str, str], ...] = (
    ("XLK",  "Technology"),
    ("XLF",  "Financials"),
    ("XLE",  "Energy"),
    ("XLV",  "Healthcare"),
    ("XLY",  "Consumer Cyclical"),
    ("XLP",  "Consumer Defensive"),
    ("XLI",  "Industrials"),
    ("XLU",  "Utilities"),
    ("XLB",  "Materials"),
    ("XLRE", "Real Estate"),
    ("XLC",  "Communication Services"),
)
BENCHMARK = "SPY"

HORIZONS = {
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
}


def _ensure_prices(symbol: str, full: bool) -> int:
    """Upsert price history for `symbol`. Returns rows written."""
    upsert_ticker(symbol)
    try:
        df = fetch_daily_adjusted(symbol, output_size="full" if full else "compact")
    except (TickerNotFound, ProviderError) as e:
        log.error("Price fetch failed for %s: %s", symbol, e)
        mark_ticker_refreshed(symbol, status=f"error:{type(e).__name__}")
        return 0
    rows = upsert_prices(symbol, df)
    mark_ticker_refreshed(symbol, status="ok")
    return rows


def _ret_over(px: pd.Series, days: int) -> float | None:
    if len(px) <= days:
        return None
    a, b = px.iloc[-1], px.iloc[-1 - days]
    if pd.isna(a) or pd.isna(b) or b == 0:
        return None
    return float(a / b - 1.0)


def _rotation_signal(
    rs1m: float | None, rs3m: float | None, rs6m: float | None
) -> tuple[str, float]:
    """Classic RRG-style four-quadrant signal + 0..1 rotation score.

    Quadrants by (rs_level_avg, rs_momentum_3to1):
      leading    — rs_avg > 0 AND rs_momentum > 0
      weakening  — rs_avg > 0 AND rs_momentum <= 0
      improving  — rs_avg <= 0 AND rs_momentum > 0
      lagging    — rs_avg <= 0 AND rs_momentum <= 0
    """
    available = [v for v in (rs1m, rs3m, rs6m) if v is not None]
    if not available:
        return "lagging", 0.0
    rs_avg = sum(available) / len(available)
    if rs1m is not None and rs3m is not None:
        rs_momentum = rs1m - rs3m
    elif rs1m is not None and rs6m is not None:
        rs_momentum = rs1m - rs6m
    else:
        rs_momentum = 0.0

    if rs_avg > 0 and rs_momentum > 0:
        signal = "leading"
    elif rs_avg > 0 and rs_momentum <= 0:
        signal = "weakening"
    elif rs_avg <= 0 and rs_momentum > 0:
        signal = "improving"
    else:
        signal = "lagging"

    # Rotation score 0..1 — high when rs_avg is high and momentum is positive
    level_part = max(0.0, min(1.0, 0.5 + rs_avg / 0.20))   # ±10% RS maps to 0/1
    mom_part = max(0.0, min(1.0, 0.5 + rs_momentum / 0.10))  # ±5% mom maps to 0/1
    score = 0.6 * level_part + 0.4 * mom_part
    return signal, score


def _compute_and_write(today_iso: str) -> int:
    """Pull SPY + sector ETF prices from DB, compute returns + rel-strength, write."""
    spy_df = fetch_prices(BENCHMARK)
    if spy_df.empty or "adj_close" not in spy_df:
        log.error("Benchmark %s missing — abort.", BENCHMARK)
        return 0

    bench_returns: dict[str, float | None] = {
        h: _ret_over(spy_df["adj_close"], d) for h, d in HORIZONS.items()
    }

    written = 0
    for etf, sector_name in SECTOR_ETFS:
        df = fetch_prices(etf)
        if df.empty or "adj_close" not in df:
            log.warning("No prices yet for %s — run with --full or include in refresh.", etf)
            continue
        px = df["adj_close"]
        rets = {h: _ret_over(px, d) for h, d in HORIZONS.items()}

        def _rs(h: str) -> float | None:
            r = rets.get(h)
            b = bench_returns.get(h)
            if r is None or b is None:
                return None
            return r - b

        rs_1m = _rs("1m")
        rs_3m = _rs("3m")
        rs_6m = _rs("6m")
        signal, score = _rotation_signal(rs_1m, rs_3m, rs_6m)

        with get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sector_perf (
                    etf_ticker, sector_name, as_of,
                    ret_1m, ret_3m, ret_6m, ret_1y,
                    rel_strength_1m, rel_strength_3m, rel_strength_6m,
                    rotation_score, rotation_signal
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    etf, sector_name, today_iso,
                    rets.get("1m"), rets.get("3m"), rets.get("6m"), rets.get("1y"),
                    rs_1m, rs_3m, rs_6m,
                    score, signal,
                ),
            )
        written += 1
        log.info(
            "sector_perf %s (%s) ret1m=%s rs1m=%s signal=%s score=%.2f",
            etf, sector_name,
            f"{rets['1m']*100:.1f}%" if rets["1m"] is not None else "—",
            f"{rs_1m*100:.1f}%" if rs_1m is not None else "—",
            signal, score,
        )
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh SPDR sector ETFs + sector_perf cache.")
    parser.add_argument("--full", action="store_true",
                        help="Pull max history for first run (default: compact ~6mo).")
    parser.add_argument("--skip-prices", action="store_true",
                        help="Skip price fetch — only recompute sector_perf from cached prices.")
    args = parser.parse_args(argv)

    init_db()

    if not args.skip_prices:
        # Fetch each sector ETF; SPY is assumed to already be in the DB
        # (it's in the default WATCHLIST and refreshed by tasks.refresh_prices).
        for etf, _ in SECTOR_ETFS:
            _ensure_prices(etf, full=args.full)

    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = _compute_and_write(today_iso)
    log.info("Done. sector_perf rows written: %d", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
