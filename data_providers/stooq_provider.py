"""Stooq price provider (Upgrade U8) — emergency fallback for yfinance.

A thin, dependency-light client (`requests` only, already a dep) over Stooq's
public daily-CSV endpoint. Returns the EXACT same DataFrame contract as the
frozen ``data_providers.yfinance_provider.fetch_daily_adjusted`` so it can drop
in behind ``data_fetcher`` when yfinance returns empty/raises.

Limitation (documented): Stooq daily CSV is split-adjusted but not fully
dividend-adjusted, so ``adj_close := close`` here. This is acceptable for an
emergency fallback — the primary path remains yfinance. Raises the same
``TickerNotFound`` / ``ProviderError`` types as the yfinance adapter.
"""
from __future__ import annotations

import io
from typing import Literal

import pandas as pd
import requests

# Reuse the yfinance adapter's exception types so callers catch one contract.
from data_providers.yfinance_provider import ProviderError, TickerNotFound

OutputSize = Literal["compact", "full"]

_STOOQ_URL = "https://stooq.com/q/d/l/"
_COMPACT_ROWS = 130  # ~6 months of trading days, matching yfinance "compact"
_TIMEOUT = 20


def _stooq_symbol(symbol: str) -> str:
    """Map a US ticker to Stooq's convention: lowercase, '.'→'-', '.us' suffix.

    e.g. AAPL → aapl.us, BRK.B → brk-b.us.
    """
    s = symbol.strip().lower().replace(".", "-")
    return s if "." in s else f"{s}.us"


def fetch_daily_adjusted(symbol: str, output_size: OutputSize = "compact") -> pd.DataFrame:
    """Fetch daily OHLCV from Stooq. Same contract as the yfinance adapter.

    Returns a DataFrame indexed by tz-naive ascending date with columns
    ``[open, high, low, close, adj_close, volume]`` (``adj_close == close``).
    Raises ``TickerNotFound`` when Stooq has no data, ``ProviderError`` on
    network/parse failure.
    """
    params = {"s": _stooq_symbol(symbol), "i": "d"}
    try:
        resp = requests.get(_STOOQ_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        text = resp.text
    except Exception as exc:  # network / HTTP error
        raise ProviderError(f"Stooq request failed for {symbol}: {exc}") from exc

    # Stooq returns the literal "No data" (or a 1-line body) for unknown symbols.
    if not text or "No data" in text[:64] or "\n" not in text.strip():
        raise TickerNotFound(f"No Stooq data returned for {symbol}")

    try:
        raw = pd.read_csv(io.StringIO(text))
    except Exception as exc:
        raise ProviderError(f"Stooq CSV parse failed for {symbol}: {exc}") from exc

    cols = {c.lower(): c for c in raw.columns}
    required = ("date", "open", "high", "low", "close", "volume")
    if not all(c in cols for c in required):
        raise TickerNotFound(f"Stooq response missing OHLCV columns for {symbol}")

    # Use .to_numpy() so the source integer index can't misalign against the
    # DatetimeIndex we set explicitly below.
    df = pd.DataFrame(
        {
            "open": pd.to_numeric(raw[cols["open"]], errors="coerce").to_numpy(),
            "high": pd.to_numeric(raw[cols["high"]], errors="coerce").to_numpy(),
            "low": pd.to_numeric(raw[cols["low"]], errors="coerce").to_numpy(),
            "close": pd.to_numeric(raw[cols["close"]], errors="coerce").to_numpy(),
            "volume": pd.to_numeric(raw[cols["volume"]], errors="coerce").to_numpy(),
        },
        index=pd.DatetimeIndex(pd.to_datetime(raw[cols["date"]], errors="coerce")),
    )
    df["adj_close"] = df["close"]  # documented limitation: no dividend adjustment
    df = df[["open", "high", "low", "close", "adj_close", "volume"]]
    df = df[df["close"].notna()].sort_index()
    df.index.name = "date"
    if df.empty:
        raise TickerNotFound(f"No usable Stooq rows for {symbol}")
    if output_size == "compact":
        df = df.tail(_COMPACT_ROWS)
    return df


__all__ = ["fetch_daily_adjusted", "TickerNotFound", "ProviderError"]
