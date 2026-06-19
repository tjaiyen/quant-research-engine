"""auto_trader/utils.py — shared helpers.

No imports from any other ``auto_trader`` module. Provides:

  * UTC-aware timestamp helpers
  * Idempotent ``setup_logging`` (M6) — safe to call multiple times
  * ``yf_retry`` decorator (M7) — exponential-backoff wrapper for any
    function that calls yfinance
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T", bound=Callable)


# ── Timestamps (all UTC) ───────────────────────────────────────────────────


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_iso() -> str:
    return date.today().isoformat()


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def days_since(iso_str: str) -> int:
    past = parse_iso(iso_str)
    if past.tzinfo is None:
        past = past.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - past).days


def format_usd(amount: float) -> str:
    return f"${amount:,.2f}"


def project_root() -> Path:
    return Path(__file__).parent.parent


# ── M6: Centralized logging setup ──────────────────────────────────────────


def setup_logging(log_path: str | None = None, level: str = "INFO") -> None:
    """Configure the root logger once. Safe to call multiple times.

    Console handler always; file handler iff ``log_path`` provided.
    """
    root = logging.getLogger()
    if any(getattr(h, "_auto_trader_log", False) for h in root.handlers):
        return

    handlers: list[logging.Handler] = []
    sh = logging.StreamHandler()
    sh._auto_trader_log = True  # type: ignore[attr-defined]
    handlers.append(sh)

    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path)
        fh._auto_trader_log = True  # type: ignore[attr-defined]
        handlers.append(fh)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))


# ── M7: yfinance retry wrapper ─────────────────────────────────────────────


def yf_retry(max_attempts: int = 3) -> Callable[[T], T]:
    """Decorator: retry ``fn`` with exponential backoff on any exception.

    Usage::

        @yf_retry(max_attempts=3)
        def fetch_recent_prices(ticker): ...

    Falls back to a plain pass-through if ``tenacity`` is not installed
    (so the auto_trader can still be imported in environments without it).
    """
    try:
        from tenacity import retry, stop_after_attempt, wait_exponential
    except Exception:  # pragma: no cover - dep missing
        def _passthrough(fn: T) -> T:
            return fn

        return _passthrough  # type: ignore[return-value]

    def decorator(fn: T) -> T:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(min=1, max=10),
            reraise=True,
        )
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = [
    "now_iso",
    "today_iso",
    "parse_iso",
    "days_since",
    "format_usd",
    "project_root",
    "setup_logging",
    "yf_retry",
]
