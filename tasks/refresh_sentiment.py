"""Fetch + FinBERT-score recent news for the universe; cache to news_sentiment (U11).

Usage:
    python -m tasks.refresh_sentiment                 # whole 220-stock universe
    python -m tasks.refresh_sentiment AAPL MSFT       # specific tickers
    python -m tasks.refresh_sentiment --limit 10      # first N (quick test)

Graceful: if FinBERT can't load, every ticker is cached as UNAVAILABLE and the
opt-in veto stays a no-op. The first run downloads the FinBERT model (~440MB).
"""
from __future__ import annotations

import argparse
import sys
import time

from screener.config import SENTIMENT_MAX_ARTICLES, SENTIMENT_NEWS_LOOKBACK_DAYS
from screener.sentiment.scorer import score_ticker_news
from tasks.seed_universe import load_universe
from utils.db import init_db, upsert_sentiment
from utils.logging_setup import get_logger

log = get_logger(__name__)
_DELAY = 0.3   # gentle pacing for the yfinance news calls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh news sentiment cache.")
    parser.add_argument("tickers", nargs="*", help="specific tickers (default: universe)")
    parser.add_argument("--limit", type=int, default=None, help="only the first N tickers")
    args = parser.parse_args(argv)

    init_db()
    universe = [t.upper() for t in args.tickers] or load_universe()
    if args.limit:
        universe = universe[: args.limit]
    log.info("Sentiment refresh for %d tickers", len(universe))

    ok = unavailable = 0
    for i, sym in enumerate(universe):
        try:
            res = score_ticker_news(sym, SENTIMENT_NEWS_LOOKBACK_DAYS, SENTIMENT_MAX_ARTICLES)
            upsert_sentiment(sym, res["sentiment_score"], res["label"],
                             res["n_headlines"], res.get("confidence"))
            if res["label"] == "UNAVAILABLE":
                unavailable += 1
            else:
                ok += 1
            log.info("  %s: %s (%s, %d headlines)", sym, res["label"],
                     res["sentiment_score"], res["n_headlines"])
        except Exception as exc:
            log.warning("  %s: sentiment failed (%s)", sym, exc)
            unavailable += 1
        if i < len(universe) - 1:
            time.sleep(_DELAY)

    print(f"Sentiment cached: {ok} scored, {unavailable} unavailable / {len(universe)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
