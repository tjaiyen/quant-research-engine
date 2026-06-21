"""SQLite persistence layer for the auto_trader.

Invariants:
  * SCHEMA_VERSION + ``_run_migrations()`` upgrade existing DBs (M1).
  * trade_history is APPEND-ONLY — no UPDATE/DELETE methods exist.
  * cost_basis lives directly on each trade_history row (H4) so realized
    P&L is self-contained.
  * DB_PATH is resolved at runtime via ``config.get_db_path()`` (H3) —
    tests override with ``TRADER_DB_PATH=...``.
  * upsert_position preserves the original ``created_at`` (Gate 11).

All other auto_trader modules talk to SQLite **only** through this file.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from auto_trader.config import get_db_path
from auto_trader.utils import now_iso, today_iso

logger = logging.getLogger(__name__)

SCHEMA_VERSION: int = 2

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS positions (
    ticker              TEXT PRIMARY KEY,
    shares              REAL NOT NULL,
    cost_basis          REAL NOT NULL,
    total_cost          REAL NOT NULL,
    current_price       REAL,
    sector              TEXT NOT NULL,
    entry_date          TEXT NOT NULL,
    entry_score         REAL NOT NULL,
    last_score          REAL,
    last_scored_at      TEXT,
    stop_loss_price     REAL NOT NULL,
    target_allocation   REAL,
    status              TEXT DEFAULT 'ACTIVE',
    regime_at_entry     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_history (
    trade_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    action              TEXT NOT NULL,
    shares              REAL NOT NULL,
    price               REAL NOT NULL,
    total_value         REAL NOT NULL,
    cost_basis          REAL,
    commission          REAL DEFAULT 0.0,
    executed_at         TEXT NOT NULL,
    order_id            TEXT,
    trigger_reason      TEXT,
    composite_score_at  REAL,
    regime_at_trade     TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS signal_history (
    snapshot_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    snapshot_date       TEXT NOT NULL,
    composite_score     REAL NOT NULL,
    arima_score         REAL,
    kalman_score        REAL,
    garch_score         REAL,
    mc_score            REAL,
    sharpe_score        REAL,
    regime              TEXT,
    regime_confidence   REAL,
    UNIQUE(ticker, snapshot_date)
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_date       TEXT PRIMARY KEY,
    total_value         REAL NOT NULL,
    cash                REAL NOT NULL,
    invested_value      REAL NOT NULL,
    unrealized_pnl      REAL,
    realized_pnl_ytd    REAL,
    n_positions         INTEGER,
    regime              TEXT,
    benchmark_value     REAL,
    drawdown_from_peak  REAL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_events (
    event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type          TEXT NOT NULL,
    event_time          TEXT NOT NULL,
    description         TEXT NOT NULL,
    details             TEXT,
    trading_mode        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_ticker ON trade_history(ticker);
CREATE INDEX IF NOT EXISTS idx_sig_ticker   ON signal_history(ticker);
CREATE INDEX IF NOT EXISTS idx_sig_date     ON signal_history(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_pos_status   ON positions(status);
"""

# v0/v1 → v2 migrations (H4: cost_basis column).
MIGRATIONS: dict[int, list[str]] = {
    2: [
        "ALTER TABLE trade_history ADD COLUMN cost_basis REAL;",
    ],
}


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    db_path = get_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Initialization + migrations ────────────────────────────────────────────


def initialize_db() -> None:
    """Create tables if missing, then run pending forward migrations.

    Safe to call on every process start.
    """
    db_path = get_db_path()
    with get_connection() as conn:
        current = conn.execute("PRAGMA user_version").fetchone()[0]

        if current < SCHEMA_VERSION:
            logger.info(
                "DB schema v%d → v%d: running migrations",
                current, SCHEMA_VERSION,
            )
            _run_migrations(conn, current)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        conn.executescript(SCHEMA)
    logger.info("Database ready: %s (schema v%d)", db_path, SCHEMA_VERSION)


def _run_migrations(conn: sqlite3.Connection, from_version: int) -> None:
    """Apply migrations from ``from_version + 1`` up to ``SCHEMA_VERSION``."""
    for v in range(from_version + 1, SCHEMA_VERSION + 1):
        for sql in MIGRATIONS.get(v, []):
            try:
                conn.execute(sql)
                logger.info("Migration v%d: %s", v, sql[:60])
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    logger.debug("Migration v%d: already applied (%s)", v, exc)
                elif "no such table" in msg:
                    # Cold DB — table doesn't exist yet; CREATE TABLE later
                    # will pick it up with the new column.
                    logger.debug("Migration v%d: table missing — fresh init (%s)", v, exc)
                else:
                    raise


# ── Positions ──────────────────────────────────────────────────────────────


def upsert_position(position: dict) -> None:
    """Insert or update a position. ``created_at`` is preserved on update."""
    now = now_iso()
    payload = {**position, "created_at": now, "updated_at": now}
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO positions (
                ticker, shares, cost_basis, total_cost, current_price,
                sector, entry_date, entry_score, last_score, last_scored_at,
                stop_loss_price, target_allocation, status, regime_at_entry,
                created_at, updated_at
            ) VALUES (
                :ticker, :shares, :cost_basis, :total_cost, :current_price,
                :sector, :entry_date, :entry_score, :last_score, :last_scored_at,
                :stop_loss_price, :target_allocation, :status, :regime_at_entry,
                :created_at, :updated_at
            )
            ON CONFLICT(ticker) DO UPDATE SET
                shares            = excluded.shares,
                cost_basis        = excluded.cost_basis,
                total_cost        = excluded.total_cost,
                current_price     = excluded.current_price,
                last_score        = excluded.last_score,
                last_scored_at    = excluded.last_scored_at,
                target_allocation = excluded.target_allocation,
                status            = excluded.status,
                stop_loss_price   = excluded.stop_loss_price,
                updated_at        = excluded.updated_at
                -- created_at intentionally excluded (Gate 11)
            """,
            payload,
        )


def get_all_positions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status NOT IN ('CLOSED')"
        ).fetchall()
    return [dict(r) for r in rows]


def get_position(ticker: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE ticker = ?", (ticker,)
        ).fetchone()
    return dict(row) if row else None


def update_position_price(ticker: str, price: float) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE positions SET current_price=?, updated_at=? WHERE ticker=?",
            (price, now_iso(), ticker),
        )


def close_position(ticker: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE positions SET status='CLOSED', updated_at=? WHERE ticker=?",
            (now_iso(), ticker),
        )


def update_position_stop_loss(ticker: str, new_stop: float) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE positions SET stop_loss_price=?, updated_at=? WHERE ticker=?",
            (new_stop, now_iso(), ticker),
        )


# ── Trade History (APPEND-ONLY) ────────────────────────────────────────────


def log_trade(trade: dict) -> int:
    """Append a fill row. The only writer for trade_history.

    Returns the new ``trade_id``. There is no UPDATE or DELETE method for
    this table by design.
    """
    payload = {
        "executed_at": now_iso(),
        "commission": 0.0,
        "order_id": None,
        "notes": None,
        "cost_basis": None,
        "trigger_reason": None,
        "composite_score_at": None,
        "regime_at_trade": None,
        **trade,
    }
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trade_history (
                ticker, action, shares, price, total_value, cost_basis,
                commission, executed_at, order_id, trigger_reason,
                composite_score_at, regime_at_trade, notes
            ) VALUES (
                :ticker, :action, :shares, :price, :total_value, :cost_basis,
                :commission, :executed_at, :order_id, :trigger_reason,
                :composite_score_at, :regime_at_trade, :notes
            )
            """,
            payload,
        )
        trade_id = int(cursor.lastrowid or 0)
    logger.info(
        "Trade logged: %s %.4f %s @ $%.2f [%s]",
        payload["action"], float(payload["shares"]), payload["ticker"],
        float(payload["price"]), payload.get("trigger_reason") or "?",
    )
    return trade_id


def get_trade_history(ticker: Optional[str] = None, limit: int = 100) -> list[dict]:
    with get_connection() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM trade_history WHERE ticker=? "
                "ORDER BY executed_at DESC LIMIT ?",
                (ticker, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trade_history ORDER BY executed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def compute_realized_pnl_ytd() -> float:
    """Realized P&L year-to-date.

    H4: uses the ``cost_basis`` column directly — no JOIN to positions
    needed. Sum over (price - cost_basis) * shares for SELL rows since
    Jan 1 of the current year.
    """
    year_start = f"{datetime.now().year}-01-01"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT shares, price, cost_basis FROM trade_history "
            "WHERE action = 'SELL' AND executed_at >= ?",
            (year_start,),
        ).fetchall()
    return float(
        sum(
            (float(r["price"]) - float(r["cost_basis"] or r["price"])) * float(r["shares"])
            for r in rows
        )
    )


# ── Signal History ─────────────────────────────────────────────────────────


def log_signal_snapshot(snapshot: dict) -> None:
    snapshot.setdefault("snapshot_date", today_iso())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO signal_history (
                ticker, snapshot_date, composite_score,
                arima_score, kalman_score, garch_score,
                mc_score, sharpe_score, regime, regime_confidence
            ) VALUES (
                :ticker, :snapshot_date, :composite_score,
                :arima_score, :kalman_score, :garch_score,
                :mc_score, :sharpe_score, :regime, :regime_confidence
            )
            """,
            snapshot,
        )


def get_signal_history(ticker: str, days: int = 90) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM signal_history WHERE ticker=? "
            "ORDER BY snapshot_date DESC LIMIT ?",
            (ticker, days),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Portfolio Snapshots ────────────────────────────────────────────────────


def log_portfolio_snapshot(snapshot: dict) -> None:
    snapshot.setdefault("snapshot_date", today_iso())
    snapshot["created_at"] = now_iso()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO portfolio_snapshots (
                snapshot_date, total_value, cash, invested_value,
                unrealized_pnl, realized_pnl_ytd, n_positions,
                regime, benchmark_value, drawdown_from_peak, created_at
            ) VALUES (
                :snapshot_date, :total_value, :cash, :invested_value,
                :unrealized_pnl, :realized_pnl_ytd, :n_positions,
                :regime, :benchmark_value, :drawdown_from_peak, :created_at
            )
            """,
            snapshot,
        )


def get_portfolio_snapshots(days: int = 365) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY snapshot_date ASC LIMIT ?",
            (days,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_peak_portfolio_value() -> float:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(total_value) AS peak FROM portfolio_snapshots"
        ).fetchone()
    return float(row["peak"]) if row and row["peak"] is not None else 0.0


# ── System Events ──────────────────────────────────────────────────────────


def log_system_event(
    event_type: str,
    description: str,
    details: Optional[dict] = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO system_events (
                event_type, event_time, description, details, trading_mode
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type,
                now_iso(),
                description,
                json.dumps(details or {}),
                os.getenv("TRADING_MODE", "paper"),
            ),
        )


def get_system_events(days: int = 365, limit: int = 500) -> list[dict]:
    """Return recent system_events rows (newest first), with details JSON parsed."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT event_type, event_time, description, details, trading_mode "
            "FROM system_events ORDER BY event_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["details"] = json.loads(d["details"]) if d.get("details") else {}
        except (json.JSONDecodeError, TypeError):
            d["details"] = {}
        out.append(d)
    return out


__all__ = [
    "SCHEMA_VERSION",
    "initialize_db",
    "get_connection",
    "get_system_events",
    # Positions
    "upsert_position",
    "get_all_positions",
    "get_position",
    "update_position_price",
    "close_position",
    "update_position_stop_loss",
    # Trade history (append-only)
    "log_trade",
    "get_trade_history",
    "compute_realized_pnl_ytd",
    # Signal history
    "log_signal_snapshot",
    "get_signal_history",
    # Portfolio snapshots
    "log_portfolio_snapshot",
    "get_portfolio_snapshots",
    "get_peak_portfolio_value",
    # System events
    "log_system_event",
]
