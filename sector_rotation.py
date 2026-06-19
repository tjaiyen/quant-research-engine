"""Sector rotation reads.

Reads from the `sector_perf` cache populated by `tasks/refresh_sectors.py`.
This module is read-only — UI callbacks call these functions; writes happen
in the task.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from utils.db import fetch_prices, get_conn
from utils.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class SectorRow:
    etf_ticker: str
    sector_name: str
    as_of: str
    ret_1m: float | None
    ret_3m: float | None
    ret_6m: float | None
    ret_1y: float | None
    rel_strength_1m: float | None
    rel_strength_3m: float | None
    rel_strength_6m: float | None
    rotation_score: float | None
    rotation_signal: str


def latest_sector_perf() -> list[SectorRow]:
    """Return the most-recent row per sector ETF, ordered by rotation_score desc."""
    sql = """
    SELECT etf_ticker, sector_name, as_of,
           ret_1m, ret_3m, ret_6m, ret_1y,
           rel_strength_1m, rel_strength_3m, rel_strength_6m,
           rotation_score, rotation_signal
    FROM sector_perf
    WHERE (etf_ticker, as_of) IN (
        SELECT etf_ticker, MAX(as_of) FROM sector_perf GROUP BY etf_ticker
    )
    ORDER BY rotation_score DESC
    """
    try:
        with get_conn() as conn:
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        log.exception("sector_perf read failed")
        return []
    return [SectorRow(**r) for r in rows]


def relative_strength_history(
    etf_ticker: str, lookback_days: int = 252, benchmark: str = "SPY"
) -> pd.DataFrame:
    """Build a daily relative-strength time series: cumulative ETF return minus
    cumulative benchmark return, indexed by date.

    Returns DataFrame with one column 'rs' indexed by trading date.
    Empty DataFrame if data is missing.
    """
    etf_df = fetch_prices(etf_ticker, limit=lookback_days + 30)
    bench_df = fetch_prices(benchmark, limit=lookback_days + 30)
    if etf_df.empty or bench_df.empty:
        return pd.DataFrame()
    common = etf_df.index.intersection(bench_df.index)
    if len(common) < 5:
        return pd.DataFrame()
    e = etf_df.loc[common, "adj_close"].astype(float)
    b = bench_df.loc[common, "adj_close"].astype(float)
    e_norm = e / e.iloc[0]
    b_norm = b / b.iloc[0]
    rs = (e_norm / b_norm - 1.0)
    return pd.DataFrame({"rs": rs}).tail(lookback_days)


def signal_color(signal: str) -> tuple[str, str]:
    """Return (foreground, background) for a rotation signal."""
    return {
        "leading":   ("#15803d", "#dcfce7"),
        "improving": ("#0e7490", "#cffafe"),
        "weakening": ("#b45309", "#fef3c7"),
        "lagging":   ("#b91c1c", "#fee2e2"),
    }.get(signal, ("#475569", "#e2e8f0"))


__all__ = [
    "SectorRow",
    "latest_sector_perf",
    "relative_strength_history",
    "signal_color",
]
