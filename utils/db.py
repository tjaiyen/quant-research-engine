"""SQLite connection + schema bootstrap. Idempotent upserts for prices/tickers."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

from utils.config import load_settings
from utils.logging_setup import get_logger

log = get_logger(__name__)
# Schema lives at the repo root (NOT under db/) so that production deployments
# can mount a persistent volume at /app/db without shadowing the schema file.
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    settings = load_settings()
    conn = _connect(settings.db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist."""
    ddl = _SCHEMA_PATH.read_text()
    with get_conn() as conn:
        conn.executescript(ddl)
    log.info("DB initialized at %s", load_settings().db_path)


def upsert_ticker(symbol: str, name: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tickers(symbol, name) VALUES(?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET name = COALESCE(excluded.name, tickers.name)",
            (symbol.upper(), name),
        )


def mark_ticker_refreshed(symbol: str, status: str = "ok") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tickers SET last_refreshed = datetime('now'), last_status = ? "
            "WHERE symbol = ?",
            (status, symbol.upper()),
        )


def upsert_prices(symbol: str, df: pd.DataFrame) -> int:
    """Insert or replace daily price rows. Returns rows written."""
    if df.empty:
        return 0
    records = [
        (
            symbol.upper(),
            idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx),
            float(row["open"]) if pd.notna(row["open"]) else None,
            float(row["high"]) if pd.notna(row["high"]) else None,
            float(row["low"]) if pd.notna(row["low"]) else None,
            float(row["close"]) if pd.notna(row["close"]) else None,
            float(row["adj_close"]) if pd.notna(row["adj_close"]) else None,
            int(row["volume"]) if pd.notna(row["volume"]) else None,
        )
        for idx, row in df.iterrows()
    ]
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO prices"
            "(ticker, date, open, high, low, close, adj_close, volume) "
            "VALUES(?,?,?,?,?,?,?,?)",
            records,
        )
    return len(records)


def fetch_prices(symbol: str, limit: int | None = None) -> pd.DataFrame:
    """Return prices as a DataFrame indexed by date ascending."""
    q = (
        "SELECT date, open, high, low, close, adj_close, volume "
        "FROM prices WHERE ticker = ? ORDER BY date ASC"
    )
    with get_conn() as conn:
        df = pd.read_sql_query(q, conn, params=(symbol.upper(),), parse_dates=["date"])
    df = df.set_index("date")
    if limit:
        df = df.tail(limit)
    return df


def upsert_fundamentals(symbol: str, snapshot: dict) -> int:
    """Insert today's fundamentals snapshot. Overwrites same-day row."""
    fields = [
        "pe", "forward_pe", "ps", "pb", "ev_ebitda", "peg",
        "div_yield", "market_cap", "sector", "industry",
    ]
    row = (
        symbol.upper(),
        snapshot.get("as_of") or pd.Timestamp.utcnow().strftime("%Y-%m-%d"),
        *[snapshot.get(f) for f in fields],
    )
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals"
            "(ticker, as_of, pe, forward_pe, ps, pb, ev_ebitda, peg, "
            "div_yield, market_cap, sector, industry) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
        )
    return 1


def fetch_latest_fundamentals(symbol: str) -> dict | None:
    """Return most recent fundamentals row as a dict, or None."""
    q = (
        "SELECT * FROM fundamentals WHERE ticker = ? "
        "ORDER BY as_of DESC LIMIT 1"
    )
    with get_conn() as conn:
        df = pd.read_sql_query(q, conn, params=(symbol.upper(),))
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def list_tickers() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT symbol, name, last_refreshed, last_status FROM tickers ORDER BY symbol",
            conn,
        )


# ---------- Holdings ----------

def upsert_holding(
    ticker: str, shares: float, cost_basis: float, opened_on: str | None = None
) -> None:
    upsert_ticker(ticker)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO holdings(ticker, shares, cost_basis, opened_on) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "shares = excluded.shares, cost_basis = excluded.cost_basis, "
            "opened_on = COALESCE(excluded.opened_on, holdings.opened_on), "
            "updated_at = datetime('now')",
            (ticker.upper(), float(shares), float(cost_basis), opened_on),
        )


def delete_holding(ticker: str) -> int:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM holdings WHERE ticker = ?", (ticker.upper(),))
        return cur.rowcount


def list_holdings() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT ticker, shares, cost_basis, opened_on, updated_at "
            "FROM holdings ORDER BY ticker",
            conn,
        )


# ---------- Earnings calendar (Upgrade U7) ----------

def upsert_earnings(ticker: str, next_earnings: str | None) -> None:
    """Store the next earnings date (ISO 'YYYY-MM-DD' or None) for a ticker."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO earnings_calendar(ticker, next_earnings, updated_at) "
            "VALUES(?, ?, datetime('now')) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "next_earnings = excluded.next_earnings, updated_at = datetime('now')",
            (ticker.upper(), next_earnings),
        )


def fetch_earnings(ticker: str) -> str | None:
    """Return the cached next earnings date for a ticker, or None."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT next_earnings FROM earnings_calendar WHERE ticker = ?",
            (ticker.upper(),),
        )
        row = cur.fetchone()
    return row[0] if row else None


# ---------- Ticker status (Upgrade U9) ----------

def ticker_status(ticker: str) -> tuple[str | None, str | None]:
    """Return (last_status, last_refreshed) for a ticker, or (None, None)."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT last_status, last_refreshed FROM tickers WHERE symbol = ?",
            (ticker.upper(),),
        )
        row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


# ---------- User settings (Phase H) ----------

import json as _json
from typing import Any as _Any


def get_setting(key: str, default: _Any = None) -> _Any:
    """Read a JSON-serialized user setting, returning `default` if absent."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT value FROM user_settings WHERE key = ?", (key,)
            )
            row = cur.fetchone()
            if not row:
                return default
            try:
                return _json.loads(row[0])
            except _json.JSONDecodeError:
                # Treat as a plain string
                return row[0]
    except Exception:
        log.exception("get_setting failed for key=%s", key)
        return default


def set_setting(key: str, value: _Any) -> None:
    """Upsert a user setting. Value is JSON-serialized."""
    payload = _json.dumps(value)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_at = datetime('now')",
            (key, payload),
        )


def delete_setting(key: str) -> int:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM user_settings WHERE key = ?", (key,))
        return cur.rowcount


def list_settings() -> dict[str, _Any]:
    """Return all settings as a dict, with JSON-decoded values."""
    out: dict[str, _Any] = {}
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT key, value FROM user_settings ORDER BY key")
            for k, v in cur.fetchall():
                try:
                    out[k] = _json.loads(v)
                except _json.JSONDecodeError:
                    out[k] = v
    except Exception:
        log.exception("list_settings failed")
    return out
