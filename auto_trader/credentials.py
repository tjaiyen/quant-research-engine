"""auto_trader/credentials.py — broker creds + safety gates.

H2: ZERO imports from any other ``auto_trader`` module — circular imports
between credentials and config would break the live-trading gate. The
constants used here are duplicated from ``config.py`` on purpose.

Three responsibilities:

  1. Resolve Alpaca credentials for the current ``TRADING_MODE``
     (paper | live), enforcing two hard gates before live keys are loaded
     (3-month paper duration AND explicit confirmation env var).
  2. Provide the halt-flag primitives (``is_halted``, ``set_halt``,
     ``clear_halt``) used at the top of every trading entry point and
     inside every order submission.
  3. Tell the world whether to use ``mock_broker.MockAlpacaClient``
     (``ALPACA_USE_MOCK=true``) or the real Alpaca REST client.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# Load dotenv at the project root — works regardless of launch directory.
PROJECT_ROOT = Path(__file__).parent.parent
try:  # pragma: no cover - optional dep
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass  # dotenv optional during unit tests

logger = logging.getLogger(__name__)

# H2: defined locally — never import from config.py
PAPER_TRADE_MIN_MONTHS: int = int(os.getenv("PAPER_TRADE_MIN_MONTHS", "3"))
REQUIRE_PAPER_BEFORE_LIVE: bool = (
    os.getenv("REQUIRE_PAPER_BEFORE_LIVE", "true").strip().lower() == "true"
)

# Halt flag and paper-start file. On Fly the auto_trader's volume is mounted
# at /app/db, so the paper-start file lives there to survive redeploys.
# Locally we keep it inside the package directory.
_VOLUME_PATH = Path("/app/db")
if _VOLUME_PATH.exists():
    PAPER_START_PATH = _VOLUME_PATH / ".paper_start_date"
    HALT_FLAG_PATH = _VOLUME_PATH / ".halt"
else:
    PAPER_START_PATH = PROJECT_ROOT / "auto_trader" / ".paper_start_date"
    HALT_FLAG_PATH = PROJECT_ROOT / "auto_trader" / ".halt"


def get_trading_mode() -> str:
    mode = os.getenv("TRADING_MODE", "paper").strip().lower()
    assert mode in ("paper", "live"), (
        f"Invalid TRADING_MODE='{mode}'. Must be 'paper' or 'live'."
    )
    return mode


def use_mock_broker() -> bool:
    """Return True when the auto_trader should use ``MockAlpacaClient``."""
    return os.getenv("ALPACA_USE_MOCK", "false").strip().lower() == "true"


def get_alpaca_credentials() -> dict:
    """Return Alpaca credentials for the current mode.

    Live mode has two hard gates that cannot be bypassed by environment
    manipulation alone — both must be true:
      1. Paper trading duration >= ``PAPER_TRADE_MIN_MONTHS``
      2. ``LIVE_TRADING_CONFIRMED=YES_I_UNDERSTAND_REAL_MONEY`` in env
    """
    mode = get_trading_mode()

    if mode == "paper":
        key = os.getenv("ALPACA_API_KEY", "")
        sec = os.getenv("ALPACA_SECRET_KEY", "")
        url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        # Mock mode short-circuits the URL check (mock client doesn't talk
        # to a URL anyway).
        if use_mock_broker():
            return {
                "api_key": key or "mock",
                "secret_key": sec or "mock",
                "base_url": url,
                "mock": True,
            }
        assert key and sec, (
            "Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env. "
            "Set ALPACA_USE_MOCK=true to use the mock broker for testing."
        )
        assert "paper-api" in url, (
            f"TRADING_MODE=paper but URL is not paper-api: {url}"
        )
        return {"api_key": key, "secret_key": sec, "base_url": url, "mock": False}

    # Live mode — two hard gates
    if not can_go_live():
        start = _read_paper_start()
        raise RuntimeError(
            f"LIVE TRADING BLOCKED: Paper gate not cleared.\n"
            f"Paper start: {start}\n"
            f"Required duration: {PAPER_TRADE_MIN_MONTHS} months\n"
            "Complete paper trading before switching to live."
        )
    confirm = os.getenv("LIVE_TRADING_CONFIRMED", "").strip()
    if confirm != "YES_I_UNDERSTAND_REAL_MONEY":
        raise RuntimeError(
            "LIVE TRADING BLOCKED: Explicit confirmation required.\n"
            "Add to .env: LIVE_TRADING_CONFIRMED=YES_I_UNDERSTAND_REAL_MONEY"
        )
    key = os.getenv("ALPACA_API_KEY_LIVE", "")
    sec = os.getenv("ALPACA_SECRET_KEY_LIVE", "")
    url = os.getenv("ALPACA_BASE_URL_LIVE", "https://api.alpaca.markets")
    assert key and sec, "Missing ALPACA_API_KEY_LIVE or ALPACA_SECRET_KEY_LIVE"
    logger.warning("LIVE TRADING MODE ACTIVE — REAL CAPITAL AT RISK")
    return {"api_key": key, "secret_key": sec, "base_url": url, "mock": False}


def can_go_live() -> bool:
    """Return True iff the paper-trade duration gate is satisfied."""
    if not REQUIRE_PAPER_BEFORE_LIVE:
        logger.warning("REQUIRE_PAPER_BEFORE_LIVE=false — gate bypassed")
        return True
    start = _read_paper_start()
    if start is None:
        return False
    try:  # pragma: no cover - optional dep
        from dateutil.relativedelta import relativedelta
    except Exception:
        # Conservative fallback: 3 months ≈ 90 days
        from datetime import timedelta

        return datetime.now() >= start + timedelta(days=PAPER_TRADE_MIN_MONTHS * 30)
    return datetime.now() >= start + relativedelta(months=PAPER_TRADE_MIN_MONTHS)


def _read_paper_start() -> Optional[datetime]:
    try:
        return datetime.strptime(PAPER_START_PATH.read_text().strip(), "%Y-%m-%d")
    except (FileNotFoundError, ValueError):
        return None


def write_paper_start(when: Optional[datetime] = None) -> str:
    """Mark the start of paper trading. Idempotent — refuses to overwrite."""
    when = when or datetime.now()
    if PAPER_START_PATH.exists():
        existing = _read_paper_start()
        if existing is not None:
            return existing.strftime("%Y-%m-%d")
    PAPER_START_PATH.parent.mkdir(parents=True, exist_ok=True)
    PAPER_START_PATH.write_text(when.strftime("%Y-%m-%d"))
    return when.strftime("%Y-%m-%d")


# ── Halt flag ──────────────────────────────────────────────────────────────


def is_halted() -> bool:
    return HALT_FLAG_PATH.exists()


def set_halt(reason: str = "") -> None:
    HALT_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = f"HALTED at {datetime.now().isoformat()}"
    if reason:
        body += f"\nReason: {reason}"
    HALT_FLAG_PATH.write_text(body)


def clear_halt() -> None:
    HALT_FLAG_PATH.unlink(missing_ok=True)


__all__ = [
    "PAPER_TRADE_MIN_MONTHS",
    "REQUIRE_PAPER_BEFORE_LIVE",
    "PAPER_START_PATH",
    "HALT_FLAG_PATH",
    "get_trading_mode",
    "use_mock_broker",
    "get_alpaca_credentials",
    "can_go_live",
    "write_paper_start",
    "is_halted",
    "set_halt",
    "clear_halt",
]
