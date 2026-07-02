"""auto_trader/config.py — single source of truth for trader constants.

No imports from any other ``auto_trader`` module. Self-validates at import
time so a bad config fails fast.

Path constants resolve at runtime via accessor functions (H3) so tests
can override with ``DB_PATH=...`` / ``SCREENER_CACHE_PATH=...`` env vars.

Note on env var naming: the cockpit uses ``DB_PATH`` for its own SQLite
(``db/cockpit.sqlite``). To keep them clearly separated we use
``TRADER_DB_PATH`` for this package. The spec calls out ``DB_PATH``; we
honor both — ``TRADER_DB_PATH`` takes precedence if set.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Capital ────────────────────────────────────────────────────────────────
MONTHLY_DEPOSIT_USD: float = 1_000.00
EXPECTED_MONTHLY_DEPOSIT: float = 1_000.00
DEPOSIT_TOLERANCE_PCT: float = 0.05
MIN_CASH_TO_TRADE: float = 50.00
MAX_MONTHLY_DEPLOYMENT_PCT: float = 0.90
CASH_RESERVE_PCT: float = 0.10
MAX_ORDER_SIZE_USD: float = 500.00

# ── Portfolio Limits ───────────────────────────────────────────────────────
MAX_POSITIONS: int = 25
MAX_SINGLE_STOCK_PCT: float = 0.06
MAX_SECTOR_PCT: float = 0.20
MAX_ADV_PCT: float = 0.01
MIN_POSITION_VALUE_USD: float = 10.00

# ── Signal Quality Gates ───────────────────────────────────────────────────
# MIN_COMPOSITE_TO_BUY is env-overridable for the strategy FLEET: a re-weighted
# composite lives on a different scale (pure-ARIMA hovers near 0.5), so each
# fleet member sets its own calibrated floor. Default unchanged for the
# flagship. Self-validation below still enforces > SIGNAL_EXIT_THRESHOLD.
MIN_COMPOSITE_TO_BUY: float = float(os.getenv("MIN_COMPOSITE_TO_BUY", "0.60"))
SIGNAL_EXIT_THRESHOLD: float = 0.45
SCORE_DECAY_WARN_DELTA: float = -0.20
TOP_N_PER_SECTOR: int = 2

# ── Risk Controls ──────────────────────────────────────────────────────────
STOP_LOSS_PCT: float = 0.12
DRAWDOWN_HALT_PCT: float = 0.15
BEAR_REGIME_CONFIDENCE_HALT: float = 0.70

# ── Position Sizing ────────────────────────────────────────────────────────
POSITION_SIZING_MODE: str = "score_vol"
VOL_PARITY_TARGET_VOL: float = 0.15
VOL_PARITY_FLOOR: float = 0.50
VOL_PARITY_CEILING: float = 1.50

# ── Execution ──────────────────────────────────────────────────────────────
ORDER_TYPE: str = "market"
SELL_TIME_IN_FORCE: str = "day"
BUY_TIME_IN_FORCE: str = "opg"
MOO_SUBMIT_HOUR: int = 9
MOO_SUBMIT_MINUTE_START: int = 25  # H7
MOO_SUBMIT_MINUTE_END: int = 28    # H7
FILL_CONFIRM_TIMEOUT_SELL: int = 300
FILL_CONFIRM_TIMEOUT_BUY: int = 600
FILL_CONFIRM_POLL_INTERVAL: int = 15
FRACTIONAL_SHARES: bool = True

# ── Monitoring ─────────────────────────────────────────────────────────────
RESCORE_DATA_PERIOD: str = "6mo"
RESCORE_MAX_POSITIONS: int = 10
RESCORE_AT_RISK_THRESHOLD: float = 0.60
SIGNAL_RESCORE_CADENCE_DAYS: int = 7

# ── Monthly Cycle ──────────────────────────────────────────────────────────
MONTHLY_CYCLE_DAY: int = 1
MONTHLY_CYCLE_WINDOW_DAYS: int = 3
SCREENER_CACHE_MAX_AGE_HOURS: int = 10  # C7

# ── Benchmark ──────────────────────────────────────────────────────────────
BENCHMARK_TICKER: str = "SPY"
RISK_FREE_RATE_ANNUAL: float = 0.05

# ── Alerts ─────────────────────────────────────────────────────────────────
ALERT_ON_STOP_LOSS: bool = True
ALERT_ON_SIGNAL_EXIT: bool = True
ALERT_ON_SCORE_DECAY: bool = True
ALERT_ON_DRAWDOWN_HALT: bool = True
ALERT_ON_MONTHLY_COMPLETE: bool = True
ALERT_ON_BEAR_REGIME: bool = True

# ── API Resilience ─────────────────────────────────────────────────────────
ALPACA_RETRY_ATTEMPTS: int = 3
ALPACA_RETRY_MIN_WAIT: int = 1
ALPACA_RETRY_MAX_WAIT: int = 10
ALPACA_API_CALLS_PER_MIN: int = 150

# ── Paper Trade Gate ───────────────────────────────────────────────────────
PAPER_TRADE_MIN_MONTHS: int = 3
REQUIRE_PAPER_BEFORE_LIVE: bool = True


# ── Paths (runtime resolution per H3) ──────────────────────────────────────
def get_db_path() -> str:
    """Auto-trader's SQLite path — ALWAYS absolute and repo-root anchored.

    Prefers TRADER_DB_PATH; falls back to a non-cockpit DB_PATH; otherwise the
    canonical ``store/portfolio.db``. A RELATIVE value (env or default) is
    resolved against the repo root so the path is identical regardless of the
    process's cwd — this is what prevents a stray second ``portfolio.db`` from
    being created/read when auto_trader is imported outside the CLI's chdir.
    """
    explicit = os.getenv("TRADER_DB_PATH")
    legacy = os.getenv("DB_PATH", "")
    if explicit:
        raw = explicit
    elif legacy and "cockpit" not in legacy:
        raw = legacy
    else:
        raw = "store/portfolio.db"
    p = Path(raw)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p   # repo root = parents[1]
    return str(p)


def get_screener_cache_path() -> str:
    return os.getenv(
        "SCREENER_CACHE_PATH", "auto_trader/state/screener_cache.json"
    )


LOG_DIR: str = "auto_trader/logs/"
OUTPUT_DIR: str = "auto_trader/output/"
EXECUTION_LOG_PATH: str = "auto_trader/logs/execution_audit.jsonl"
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── Internal ───────────────────────────────────────────────────────────────
EXPECTED_SIGNAL_KEYS: set[str] = {
    "arima", "kalman", "garch", "monte_carlo", "sharpe",
}


# ── Self-Validation (runs at import time — fails fast on config error) ────
def _validate_config() -> None:
    assert 0 < MAX_MONTHLY_DEPLOYMENT_PCT <= 1.0, "MAX_MONTHLY_DEPLOYMENT_PCT out of range"
    assert 0 < CASH_RESERVE_PCT < 1.0, "CASH_RESERVE_PCT out of range"
    assert SIGNAL_EXIT_THRESHOLD < MIN_COMPOSITE_TO_BUY, (
        "SIGNAL_EXIT_THRESHOLD must be below MIN_COMPOSITE_TO_BUY"
    )
    assert MAX_SINGLE_STOCK_PCT + CASH_RESERVE_PCT < 1.0, (
        "MAX_SINGLE_STOCK_PCT + CASH_RESERVE_PCT must leave room for other positions"
    )
    assert MAX_POSITIONS * MAX_SINGLE_STOCK_PCT >= 1.0, (
        "MAX_POSITIONS * MAX_SINGLE_STOCK_PCT must allow full deployment"
    )
    assert STOP_LOSS_PCT > 0, "STOP_LOSS_PCT must be positive"
    assert DRAWDOWN_HALT_PCT > 0, "DRAWDOWN_HALT_PCT must be positive"
    assert POSITION_SIZING_MODE in ("score_weight", "equal", "score_vol"), (
        f"Unknown POSITION_SIZING_MODE: {POSITION_SIZING_MODE}"
    )
    assert SELL_TIME_IN_FORCE == "day", "Sell must use time_in_force='day'"
    assert BUY_TIME_IN_FORCE == "opg", "Buy must use time_in_force='opg'"
    assert TOP_N_PER_SECTOR >= 1, "TOP_N_PER_SECTOR must be >= 1"
    assert RESCORE_MAX_POSITIONS <= MAX_POSITIONS, (
        "RESCORE_MAX_POSITIONS cannot exceed MAX_POSITIONS"
    )
    assert 0 < SCREENER_CACHE_MAX_AGE_HOURS <= 24, (
        "SCREENER_CACHE_MAX_AGE_HOURS out of range"
    )
    assert MOO_SUBMIT_MINUTE_START < MOO_SUBMIT_MINUTE_END, (
        "MOO window: start must precede end"
    )


_validate_config()
