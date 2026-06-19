"""yfinance provider — daily adjusted prices via Yahoo Finance.

No API key, effectively unlimited calls. Occasional breakage when Yahoo
shifts endpoints; acceptable for an internal cockpit.

Schema match: same columns as alpha_vantage.fetch_daily_adjusted —
open, high, low, close, adj_close, volume — indexed by date ascending.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
import yfinance as yf

from utils.logging_setup import get_logger

log = get_logger(__name__)

OutputSize = Literal["compact", "full"]  # compact=~100d, full=max history


class TickerNotFound(Exception):
    pass


class ProviderError(Exception):
    pass


def fetch_daily_adjusted(
    symbol: str, output_size: OutputSize = "compact"
) -> pd.DataFrame:
    """Return daily adjusted OHLCV for `symbol`, indexed ascending.

    Uses `auto_adjust=False` so we get both raw OHLC and a separate Adj Close
    column (needed for correct return math across splits/dividends).
    """
    symbol = symbol.upper().strip()
    period = "max" if output_size == "full" else "6mo"
    log.info("Fetching %s via yfinance (period=%s)", symbol, period)

    try:
        df = yf.download(
            symbol,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception as e:  # network, parsing, etc.
        raise ProviderError(f"yfinance download failed for {symbol}: {e}") from e

    if df is None or df.empty:
        raise TickerNotFound(f"No data returned for {symbol}")

    # yfinance may return a MultiIndex when multiple tickers or new versions.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    rename = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }
    df = df.rename(columns=rename)
    keep = ["open", "high", "low", "close", "adj_close", "volume"]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        raise ProviderError(f"yfinance response missing columns {missing} for {symbol}")

    df = df[keep].copy()
    df = df.apply(pd.to_numeric, errors="coerce")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.sort_index()
    df = df.dropna(subset=["close"])
    log.info("Got %d rows for %s (%s..%s)", len(df), symbol, df.index.min().date(), df.index.max().date())
    return df


def _safe_float(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN check
        return None
    return f


def fetch_fundamentals(symbol: str) -> dict:
    """Return a fundamentals snapshot from yfinance.

    yfinance's `.info` is unreliable — fields missing frequently, especially
    for ETFs (SPY/QQQ won't have P/E). Caller must handle Nones gracefully.
    """
    symbol = symbol.upper().strip()
    log.info("Fetching fundamentals for %s via yfinance", symbol)
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as e:
        raise ProviderError(f"yfinance .info failed for {symbol}: {e}") from e

    return {
        "as_of": pd.Timestamp.utcnow().strftime("%Y-%m-%d"),
        "pe": _safe_float(info.get("trailingPE")),
        "forward_pe": _safe_float(info.get("forwardPE")),
        "ps": _safe_float(info.get("priceToSalesTrailing12Months")),
        "pb": _safe_float(info.get("priceToBook")),
        "ev_ebitda": _safe_float(info.get("enterpriseToEbitda")),
        "peg": _safe_float(info.get("pegRatio") or info.get("trailingPegRatio")),
        "div_yield": _safe_float(info.get("dividendYield")),
        "market_cap": _safe_float(info.get("marketCap")),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
    }
