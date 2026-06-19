"""Monthly performance computation vs the SPY benchmark.

M8: ``benchmark_return`` returns ``None`` on yfinance failure rather than
crashing the report. H11: holiday filter on benchmark dates.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from auto_trader.config import BENCHMARK_TICKER
from auto_trader.state.portfolio_db import (
    compute_realized_pnl_ytd,
    get_portfolio_snapshots,
)
from auto_trader.utils import yf_retry

logger = logging.getLogger(__name__)


def compute_monthly_performance(
    end_date: Optional[date] = None,
) -> dict:
    """Return a perf dict comparing the past ~30 days vs SPY.

    Reads portfolio_snapshots for the trailing window. SPY return is
    fetched via yfinance (M8: None on any failure).
    """
    end_date = end_date or date.today()
    snapshots = get_portfolio_snapshots(days=400)
    if not snapshots:
        return {"error": "no portfolio snapshots yet"}

    cutoff = end_date - timedelta(days=30)
    in_window = [
        s for s in snapshots
        if datetime.fromisoformat(s["snapshot_date"]).date() >= cutoff
    ]
    if len(in_window) < 2:
        return {"error": f"insufficient snapshots (need >=2, have {len(in_window)})"}

    start_value = float(in_window[0]["total_value"])
    end_value = float(in_window[-1]["total_value"])
    total_return = (end_value - start_value) / max(start_value, 1.0)

    bench_return = _benchmark_return(BENCHMARK_TICKER, days=30)

    return {
        "start_date": in_window[0]["snapshot_date"],
        "end_date": in_window[-1]["snapshot_date"],
        "start_value": start_value,
        "end_value": end_value,
        "total_return": total_return,
        "benchmark_return": bench_return,
        "alpha": (total_return - bench_return) if bench_return is not None else None,
        "realized_pnl_ytd": compute_realized_pnl_ytd(),
    }


@yf_retry(max_attempts=2)
def _benchmark_return(ticker: str = "SPY", days: int = 30) -> Optional[float]:
    """Trailing-N-day return for the benchmark, or None on failure."""
    try:
        import yfinance as yf

        end = date.today()
        start = end - timedelta(days=int(days * 1.5) + 7)  # buffer for holidays
        raw = yf.download(
            ticker, start=str(start), end=str(end), progress=False, auto_adjust=True,
        )
        if raw is None or raw.empty:
            return None
        # H11: drop NaN rows (covers holidays / partial trading days)
        close = raw["Close"].dropna()
        if isinstance(close, type(close).__class__) and hasattr(close, "iloc"):
            pass
        if hasattr(close, "iloc"):
            if len(close) < 2:
                return None
            first = float(close.iloc[0])
            last = float(close.iloc[-1])
            if first <= 0:
                return None
            return (last - first) / first
        return None
    except Exception as exc:
        logger.warning("benchmark_return(%s) failed: %s", ticker, exc)
        return None


__all__ = ["compute_monthly_performance"]
