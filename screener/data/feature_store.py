"""In-memory feature cache for HMM market features.

Smart-reuse decision (RECON_REPORT.md #3): the SCREENER_BUILD_v3 spec calls
for a Parquet file cache via pyarrow. Cockpit already uses SQLite as its
durable cache layer, and we don't want to add a heavy pyarrow dependency
just for a 4-column dataframe that is cheap to recompute.

Implementation: a thin process-local cache keyed by `lookback_years` with a
12-hour TTL (matches the spec's `CACHE_MAX_AGE_HOURS`). On cache hit we
return the same DataFrame; on miss we recompute via
`screener.data.market_features.get_market_features` and refresh the entry.

The cache is cleared automatically when `lookback_years` changes between
calls (different cache key) or when the entry exceeds the TTL.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

import pandas as pd

from screener.config import FEATURE_CACHE_TTL_HOURS, HMM_LOOKBACK_YEARS

logger = logging.getLogger(__name__)

# Module-level cache: key → (epoch_seconds_when_stored, df)
_cache: Dict[Any, tuple[float, pd.DataFrame]] = {}


def _ttl_seconds() -> int:
    return FEATURE_CACHE_TTL_HOURS * 3600


def get_market_features_cached(
    lookback_years: int = HMM_LOOKBACK_YEARS,
    min_rows: int = 500,
) -> pd.DataFrame:
    """Return cached features if fresh; otherwise recompute and cache.

    ``min_rows`` is forwarded to ``get_market_features`` and is part of
    the cache key — different floors produce different cache entries.
    """
    from screener.data.market_features import get_market_features

    now = time.time()
    key = ("features", int(lookback_years), int(min_rows))
    entry = _cache.get(key)
    if entry is not None:
        stored_at, df = entry
        age_s = now - stored_at
        if age_s < _ttl_seconds():
            logger.info(
                "Feature cache hit: lookback=%dy min_rows=%d age=%.1fh rows=%d",
                lookback_years, min_rows, age_s / 3600.0, len(df),
            )
            return df
        else:
            logger.info(
                "Feature cache stale (age=%.1fh); recomputing",
                age_s / 3600.0,
            )

    logger.info("Feature cache miss — recomputing market features")
    df = get_market_features(lookback_years, min_rows=min_rows)
    _cache[key] = (now, df)
    return df


def clear_cache() -> None:
    """Drop all cached entries (used by tests)."""
    _cache.clear()


__all__ = ["get_market_features_cached", "clear_cache"]
