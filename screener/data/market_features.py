"""Pull and assemble the HMM feature set.

Returns a DataFrame whose columns are exactly `HMM_FEATURES`, in that order:

    [log_return, realized_vol_20d, vix_normalized, breadth_pct]

M1 fix: VIX uses ffill (limited) + linear interpolation, with a fallback
constant when too much data is missing.

Smart-reuse decision (RECON_REPORT.md #2): SPY price history is sourced from
the cockpit's `prices` SQLite table when ≥ `YFIN_MIN_ROWS_REQUIRED` rows are
present, falling back to yfinance only on cache miss.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from screener.config import (
    HMM_FEATURES,
    HMM_LOOKBACK_YEARS,
    VIX_FALLBACK_VALUE,
    VIX_FFILL_LIMIT,
    VIX_MAX_NAN_PCT,
    VIX_NORMALIZE_DIVISOR,
    VIX_TICKER,
    YFIN_MIN_ROWS_REQUIRED,
)

logger = logging.getLogger(__name__)


def _spy_from_cockpit(lookback_years: int) -> pd.Series | None:
    """Return SPY adj_close series from cockpit's `prices` table, or None.

    Returns None if the table doesn't have enough rows (cold start) or any
    error occurs — caller falls back to yfinance.
    """
    try:
        from utils.db import fetch_prices  # local import to avoid cockpit deps at import time
    except Exception as exc:  # pragma: no cover
        logger.debug("cockpit utils.db unavailable (%s); falling back to yfinance", exc)
        return None
    try:
        df = fetch_prices("SPY")
    except Exception as exc:
        logger.debug("fetch_prices('SPY') failed (%s); falling back to yfinance", exc)
        return None
    if df is None or df.empty or "adj_close" not in df.columns:
        return None
    if len(df) < YFIN_MIN_ROWS_REQUIRED:
        logger.debug(
            "cockpit SPY has only %d rows (< %d); falling back to yfinance",
            len(df), YFIN_MIN_ROWS_REQUIRED,
        )
        return None
    series = df["adj_close"].astype(float).dropna()
    cutoff = series.index.max() - pd.Timedelta(days=int((lookback_years + 1) * 365.25))
    series = series[series.index >= cutoff]
    if len(series) < YFIN_MIN_ROWS_REQUIRED:
        return None
    series.name = "Close"
    return series


def _yf_close(ticker: str, period: str) -> pd.Series:
    """yfinance fallback for one ticker. Always returns a 1-D Series of close."""
    import yfinance as yf  # heavyweight import — lazy

    raw = yf.download(ticker, period=period, auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        return pd.Series(dtype=float)
    if "Close" in raw.columns:
        s = raw["Close"]
    else:
        # Some yfinance versions return MultiIndex columns
        try:
            s = raw.xs("Close", axis=1, level=0)
        except KeyError:
            return pd.Series(dtype=float)
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return s.astype(float).dropna()


def get_market_features(
    lookback_years: int = HMM_LOOKBACK_YEARS,
    min_rows: int = 500,
) -> pd.DataFrame:
    """Build the 4-feature DataFrame for HMM training/prediction.

    Columns are guaranteed to be exactly `HMM_FEATURES` in that order (C4).
    All-NaN rows are dropped. Asserts ``len(df) > min_rows``: training
    callers should use the default 500-row floor to keep the HMM well-fit;
    the predictor uses a smaller floor (~100) since only the final row is
    consumed.
    """
    period = f"{lookback_years + 1}y"

    # SPY: prefer cockpit's prices table; fall back to yfinance
    spy = _spy_from_cockpit(lookback_years)
    if spy is None or spy.empty:
        logger.info("Pulling SPY from yfinance (period=%s)", period)
        spy = _yf_close("SPY", period)
    else:
        logger.info("SPY sourced from cockpit prices table (%d rows)", len(spy))
    if spy.empty:
        raise RuntimeError("Could not source SPY price history from cockpit or yfinance")

    logger.info(
        "SPY range: %s → %s (%d rows)",
        spy.index[0].date(), spy.index[-1].date(), len(spy),
    )

    log_ret = np.log(spy / spy.shift(1))
    realized_vol = log_ret.rolling(20).std() * np.sqrt(252)

    # VIX — M1: ffill short gaps, linear interp longer gaps, fallback constant
    vix_raw = _yf_close(VIX_TICKER, period)
    nan_pct = vix_raw.isna().sum() / max(len(vix_raw), 1) if not vix_raw.empty else 1.0
    if vix_raw.empty or nan_pct > VIX_MAX_NAN_PCT:
        logger.warning(
            "VIX %.1f%% missing — using fallback constant (%.1f)",
            nan_pct * 100.0, VIX_FALLBACK_VALUE,
        )
        vix_normalized = pd.Series(
            VIX_FALLBACK_VALUE / VIX_NORMALIZE_DIVISOR, index=spy.index
        )
    else:
        vix_clean = (
            vix_raw.reindex(spy.index)
            .ffill(limit=VIX_FFILL_LIMIT)
            .interpolate(method="linear")
        )
        vix_normalized = vix_clean / VIX_NORMALIZE_DIVISOR

    # Breadth — v1 proxy: 1.0 if SPY > 50dMA else 0.0
    # NOTE: real breadth requires per-constituent data. This is a deliberate
    # v1 approximation; upgrade path lives in feature_store.py.
    spy_ma50 = spy.rolling(50).mean()
    breadth = (spy > spy_ma50).astype(float)

    df = pd.DataFrame(
        {
            "log_return": log_ret,
            "realized_vol_20d": realized_vol,
            "vix_normalized": vix_normalized,
            "breadth_pct": breadth,
        },
        index=spy.index,
    ).dropna()

    assert len(df) > min_rows, (
        f"Insufficient market feature rows: {len(df)} <= {min_rows}"
    )

    # C4: enforce column ordering exactly matches HMM_FEATURES
    df = df[HMM_FEATURES]
    logger.info("Market features assembled: %d rows × %d cols", len(df), len(df.columns))
    return df


__all__ = ["get_market_features"]
