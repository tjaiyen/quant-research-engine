"""Monthly holdings update helper (run manually).

Downloads top-N holdings from iShares CSV exports and updates
`screener/data/holdings.json`. Also bumps the `_meta.last_updated` field.

Usage:
    python -m screener.data.update_holdings

Steps:
  1. Visit https://www.ishares.com/us/products/etf-investments and download
     the holdings CSV for each Select Sector SPDR (XLK, XLV, XLF, XLE, XLI,
     XLY, XLP, XLB, XLU, XLRE, XLC).
  2. Drop the CSV files in ``screener/data/raw_holdings/`` named after the
     ETF ticker (e.g. ``XLK.csv``).
  3. Run this script. It rewrites ``holdings.json`` with the top
     ``STOCKS_PER_SECTOR`` constituents per sector.

The script preserves any sector whose CSV is absent (logs a warning) and
emits a final diff so you can sanity-check changes before committing.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

from screener.config import HOLDINGS_PATH, SECTOR_ETFS, STOCKS_PER_SECTOR

logger = logging.getLogger(__name__)


def _read_csv_top_n(csv_path: Path, n: int) -> list[str]:
    import pandas as pd  # local import — heavyweight

    # iShares CSVs typically have 2 metadata rows above the header
    df = pd.read_csv(csv_path, skiprows=2)
    ticker_col = df.columns[0]
    tickers = (
        df[ticker_col]
        .dropna()
        .astype(str)
        .str.strip()
        .str.upper()
        .head(n)
        .tolist()
    )
    return tickers


def update_from_csvs(raw_dir: str = "screener/data/raw_holdings") -> int:
    """Update ``holdings.json`` from CSVs in *raw_dir*.

    Returns the number of sectors actually updated. Sectors without a CSV
    are left untouched.
    """
    raw_path = Path(raw_dir)
    if not raw_path.exists():
        logger.error("Raw holdings directory not found: %s", raw_dir)
        return 0

    holdings_path = Path(HOLDINGS_PATH)
    with holdings_path.open() as f:
        holdings: dict = json.load(f)

    updated = 0
    for sector, etf in SECTOR_ETFS.items():
        csv_path = raw_path / f"{etf}.csv"
        if not csv_path.exists():
            logger.warning("MISSING: %s — skipping %s", csv_path, sector)
            continue
        try:
            tickers = _read_csv_top_n(csv_path, STOCKS_PER_SECTOR)
        except Exception as exc:
            logger.error("Failed to parse %s: %s", csv_path, exc)
            continue
        if len(tickers) < STOCKS_PER_SECTOR:
            logger.warning(
                "%s: only %d tickers in CSV (expected %d)",
                sector, len(tickers), STOCKS_PER_SECTOR,
            )
        prior = holdings.get(sector, [])
        diff_added = sorted(set(tickers) - set(prior))
        diff_removed = sorted(set(prior) - set(tickers))
        holdings[sector] = tickers
        updated += 1
        logger.info(
            "%s updated: +%s / -%s",
            sector,
            ",".join(diff_added) or "—",
            ",".join(diff_removed) or "—",
        )

    holdings.setdefault("_meta", {})["last_updated"] = date.today().isoformat()
    with holdings_path.open("w") as f:
        json.dump(holdings, f, indent=2)
    logger.info("holdings.json rewritten (%d sectors updated)", updated)
    return updated


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    n = update_from_csvs()
    sys.exit(0 if n > 0 else 1)
