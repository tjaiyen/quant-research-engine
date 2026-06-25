"""Industry ranker — turns a sector's holdings list into a ranked top-N.

Spec invariants:
  - C3: ``_extract_ticker_data`` handles yfinance MultiIndex correctly.
  - H6: sector key validation against ``SECTOR_ETFS`` (Gate 10).
  - H7: ``rank_industry`` returns a dict with ``passed/skipped/failed/total_screened``.
  - M3: data-freshness warning when fetched batch lags by > N trading days.
  - Bear-regime veto relaxation (2 passes, 20% loosening per pass).

Smart-reuse (RECON_REPORT.md #2):
  - Per ticker, prefer the cockpit's ``prices`` SQLite table when it has
    ≥ ``YFIN_MIN_ROWS_REQUIRED`` rows. Only fetch from yfinance for the
    misses (in one batched call, with retry).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from screener.config import (
    BEAR_REGIME_VETO_RELAXATION,
    HOLDINGS_PATH,
    SCREENER_SKIP_STALE_DAYS,
    SCREENER_SKIP_STALE_ENABLED,
    SECTOR_ETFS,
    STOCKS_PER_SECTOR,
    TOP_N_OUTPUT,
    VETO_RELAXATION_FACTOR,
    VETO_RELAXATION_PASSES,
    VETO_THRESHOLDS,
    YFIN_DATA_STALE_DAYS,
    YFIN_INTERSECTOR_DELAY_SEC,
    YFIN_MIN_ROWS_REQUIRED,
    YFIN_RETRY_ATTEMPTS,
    YFIN_RETRY_DELAY_SECONDS,
)
from screener.engine.composite_scorer import score_stock

logger = logging.getLogger(__name__)


# --- Smart-reuse helper: cockpit prices table ------------------------------
def _from_cockpit(ticker: str) -> pd.DataFrame | None:
    """Return Title-cased OHLCV from cockpit's prices table, or None.

    Uses ``adj_close`` as the canonical Close (consistent with cockpit's
    other consumers).
    """
    try:
        from utils.db import fetch_prices
    except Exception:
        return None
    try:
        df = fetch_prices(ticker)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    if len(df) < YFIN_MIN_ROWS_REQUIRED:
        return None
    cols_needed = {"open", "high", "low", "adj_close", "volume"}
    if not cols_needed.issubset(df.columns):
        return None
    out = pd.DataFrame(
        {
            "Open": df["open"],
            "High": df["high"],
            "Low": df["low"],
            "Close": df["adj_close"],
            "Volume": df["volume"],
        },
        index=df.index,
    ).dropna(how="all")
    return out


# --- U9: delisting / stale-ticker skip --------------------------------------
def _is_tradeable(ticker: str, max_stale_days: int = SCREENER_SKIP_STALE_DAYS) -> bool:
    """Return False if the ticker's last fetch errored or its data is stale.

    Reads ``tickers.last_status`` / ``last_refreshed`` (written by seed/refresh).
    Fail-open: unknown status or unparseable timestamp → tradeable (never block
    a name just because the status table has no opinion).
    """
    try:
        from utils.db import ticker_status
    except Exception:
        return True
    try:
        status, last_refreshed = ticker_status(ticker)
    except Exception:
        return True
    if status and status.lower().startswith("error"):
        return False
    if last_refreshed:
        try:
            last = datetime.strptime(str(last_refreshed)[:19], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - last).days > max_stale_days:
                return False
        except ValueError:
            pass  # unparseable → fail-open
    return True


def _next_earnings(ticker: str) -> str | None:
    """Cached next-earnings date for a ticker (U7), or None. Never raises."""
    try:
        from utils.db import fetch_earnings
        return fetch_earnings(ticker)
    except Exception:
        return None


def _cached_sentiment(ticker: str) -> dict | None:
    """Cached news-sentiment row for a ticker (U11), or None. Never raises."""
    try:
        from utils.db import fetch_sentiment
        return fetch_sentiment(ticker)
    except Exception:
        return None


# --- yfinance batch fetch ---------------------------------------------------
def _extract_ticker_data(
    raw_data: pd.DataFrame, ticker: str, tickers_list: list[str]
) -> pd.DataFrame:
    """C3: extract a single-ticker slice from a yfinance batch response.

    Handles both the MultiIndex (multi-ticker) and flat (single-ticker)
    formats yfinance can produce, and normalizes column names to Title
    case so the universal-signal contract holds.
    """
    if len(tickers_list) == 1:
        ph = raw_data.copy()
        ph.columns = [
            c.capitalize() if str(c).lower() in {"open", "high", "low", "close", "volume"} else c
            for c in ph.columns
        ]
    else:
        if isinstance(raw_data.columns, pd.MultiIndex):
            try:
                ph = raw_data.xs(ticker, level=1, axis=1).copy()
            except KeyError:
                # Some yfinance versions use level=0 for the ticker
                ph = raw_data.xs(ticker, level=0, axis=1).copy()
        else:
            ph = raw_data.filter(like=ticker).copy()
            ph.columns = [c.split("_")[-1] if "_" in c else c for c in ph.columns]

    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in ph.columns]
    if missing:
        raise ValueError(f"{ticker}: missing columns after extraction: {missing}")
    return ph.dropna(how="all")


def fetch_with_retry(tickers: list[str], period: str = "2y") -> pd.DataFrame:
    """Batch fetch from yfinance with retry + M3 freshness check.

    Returns an empty DataFrame on total failure. Used only for the tickers
    not satisfied by the cockpit prices cache.
    """
    if not tickers:
        return pd.DataFrame()
    import yfinance as yf  # heavy import — lazy

    for attempt in range(YFIN_RETRY_ATTEMPTS):
        df = yf.download(
            tickers, period=period, auto_adjust=True, progress=False, group_by="ticker"
        )
        if df is not None and not df.empty and len(df) >= YFIN_MIN_ROWS_REQUIRED:
            last_date = df.index[-1]
            today = pd.Timestamp.now().normalize()
            days_diff = (today - last_date).days
            if days_diff > YFIN_DATA_STALE_DAYS:
                logger.warning(
                    "Data may be stale: last_date=%s (%d calendar days old)",
                    last_date.date(), days_diff,
                )
            return df
        logger.warning(
            "Fetch attempt %d/%d returned insufficient data; retrying in %ds",
            attempt + 1, YFIN_RETRY_ATTEMPTS, YFIN_RETRY_DELAY_SECONDS,
        )
        time.sleep(YFIN_RETRY_DELAY_SECONDS)
    logger.error("All %d fetch attempts failed.", YFIN_RETRY_ATTEMPTS)
    return pd.DataFrame()


def _gather_price_histories(
    tickers: list[str],
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Return (per-ticker price-history dict, list of unfulfilled tickers).

    Smart-reuse: use cockpit's prices table per ticker; fall back to a single
    yfinance batch fetch for the misses.
    """
    histories: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for t in tickers:
        cached = _from_cockpit(t)
        if cached is not None:
            histories[t] = cached
        else:
            missing.append(t)

    if missing:
        logger.info(
            "yfinance fallback for %d/%d tickers: %s",
            len(missing), len(tickers), ",".join(missing),
        )
        raw = fetch_with_retry(missing)
        if not raw.empty:
            for t in list(missing):
                try:
                    histories[t] = _extract_ticker_data(raw, t, missing)
                except Exception as exc:
                    logger.warning("yfinance extract failed for %s: %s", t, exc)
    return histories, [t for t in tickers if t not in histories]


# --- Sector-level ranker ----------------------------------------------------
def rank_industry(sector: str, tickers: list[str], regime_data: dict) -> dict:
    """Rank one sector and return the audit-trail dict (H7).

    Returns:
        ``{"passed": [...top N...], "skipped": [...], "failed": [...],
           "total_screened": int}``
    """
    histories, unfulfilled = _gather_price_histories(tickers)
    regime = regime_data["regime"]

    scored: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for ticker in tickers:
        # U9: skip delisted/stale tickers (errored last fetch or data too old)
        if SCREENER_SKIP_STALE_ENABLED and not _is_tradeable(ticker):
            skipped.append({"ticker": ticker, "reason": "delisted_or_stale"})
            continue
        ph = histories.get(ticker)
        if ph is None:
            failed.append({"ticker": ticker, "error": "no_data"})
            continue
        if len(ph) < YFIN_MIN_ROWS_REQUIRED:
            skipped.append(
                {
                    "ticker": ticker,
                    "reason": "insufficient_history",
                    "rows_available": int(len(ph)),
                    "rows_required": int(YFIN_MIN_ROWS_REQUIRED),
                }
            )
            continue
        try:
            # U7/U11: pass cached earnings + (only when enabled) sentiment.
            from screener.config import SENTIMENT_VETO_ENABLED
            scored.append(
                score_stock(
                    ticker, regime_data, ph,
                    next_earnings=_next_earnings(ticker),
                    cached_sentiment=_cached_sentiment(ticker) if SENTIMENT_VETO_ENABLED else None,
                )
            )
        except Exception as exc:
            logger.error("%s: unexpected scoring error — %s", ticker, exc)
            failed.append({"ticker": ticker, "error": str(exc)})

    passed = [s for s in scored if s["passed_veto"]]
    passed.sort(key=lambda x: x["composite_score"], reverse=True)

    # Bear-regime veto relaxation (safety valve: avoid empty sector)
    if not passed and BEAR_REGIME_VETO_RELAXATION:
        for i in range(VETO_RELAXATION_PASSES):
            multiplier = 1.0 + VETO_RELAXATION_FACTOR * (i + 1)
            base = VETO_THRESHOLDS[regime]
            relaxed_thresholds = {
                "garch_vol": base["garch_vol"] * multiplier,
                "mc_loss_prob": base["mc_loss_prob"] * multiplier,
            }
            relaxed: list[dict] = []
            for s in scored:
                # U7/U11: categorical vetoes (earnings, sentiment) — never relaxed.
                if s.get("earnings_veto") or s.get("sentiment_veto"):
                    continue
                gv = s["veto_detail"]["garch_vol"]
                ml = s["veto_detail"]["mc_loss_prob"]
                if (
                    gv <= relaxed_thresholds["garch_vol"]
                    and ml <= relaxed_thresholds["mc_loss_prob"]
                ):
                    s_copy = dict(s)
                    s_copy["veto_relaxed"] = True
                    s_copy["relaxation_passes"] = i + 1
                    relaxed.append(s_copy)
            if relaxed:
                relaxed.sort(key=lambda x: x["composite_score"], reverse=True)
                logger.warning(
                    "%s: veto relaxed %dx — %d candidates found",
                    sector, i + 1, len(relaxed),
                )
                passed = relaxed
                break

    if not passed:
        logger.warning(
            "%s: 0 stocks passed veto after %d relaxation passes",
            sector, VETO_RELAXATION_PASSES,
        )
    if skipped:
        logger.info("%s: %d tickers skipped (insufficient history)", sector, len(skipped))
    if failed:
        logger.warning("%s: %d tickers failed unexpectedly", sector, len(failed))
    if unfulfilled:
        logger.warning(
            "%s: %d tickers had no price source (cockpit miss + yfinance fail)",
            sector, len(unfulfilled),
        )

    return {
        "passed": passed[:TOP_N_OUTPUT],
        "skipped": skipped,
        "failed": failed,
        "total_screened": len(tickers),
    }


def rank_all_sectors(regime_data: dict) -> dict[str, dict]:
    """Score all 11 sectors with H6 sector-key validation."""
    holdings_path = Path(HOLDINGS_PATH)
    with holdings_path.open() as f:
        holdings = json.load(f)

    meta = holdings.get("_meta", {})
    if meta.get("last_updated"):
        try:
            last_updated = datetime.strptime(meta["last_updated"], "%Y-%m-%d")
            days_stale = (datetime.now() - last_updated).days
            warn_days = int(meta.get("staleness_warning_days", 35))
            if days_stale > warn_days:
                logger.warning(
                    "holdings.json is %d days old — update recommended", days_stale,
                )
        except Exception:
            pass

    sectors = {k: v for k, v in holdings.items() if not k.startswith("_")}

    # H6 / Gate 10: sector key validation
    config_sectors = set(SECTOR_ETFS.keys())
    holdings_sectors = set(sectors.keys())
    if config_sectors != holdings_sectors:
        raise ValueError(
            f"Sector key mismatch! "
            f"Missing in holdings: {config_sectors - holdings_sectors} | "
            f"Extra in holdings: {holdings_sectors - config_sectors}"
        )

    results: dict[str, dict] = {}
    items = list(sectors.items())
    for i, (sector, tickers) in enumerate(items):
        # A sector may carry FEWER than STOCKS_PER_SECTOR after dual-class dedup
        # (e.g. Communications drops GOOG/FOX/NWS); guard the real failure modes
        # (empty, or somehow oversized) rather than an exact count.
        assert 0 < len(tickers) <= STOCKS_PER_SECTOR, (
            f"{sector}: {len(tickers)} tickers, expected 1..{STOCKS_PER_SECTOR}"
        )
        logger.info("[%d/%d] %s (%d stocks)…", i + 1, len(items), sector, len(tickers))
        results[sector] = rank_industry(sector, tickers, regime_data)
        if i < len(items) - 1:
            time.sleep(YFIN_INTERSECTOR_DELAY_SEC)

    return results


__all__ = [
    "_extract_ticker_data",
    "fetch_with_retry",
    "rank_industry",
    "rank_all_sectors",
]
