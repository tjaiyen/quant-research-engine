"""One-time paper-trading onboarding.

Designed from scratch (the v2 spec is unavailable; the v3 spec only
references it without including its content).

Steps:
  1. ``initialize_db()`` — creates / migrates the auto_trader SQLite
  2. Verify the broker connection (mock or real Alpaca paper)
  3. Write ``.paper_start_date`` if missing — kicks off the 3-month gate
  4. Print onboarding summary

After this script, the auto_trader is "ready to trade" — daily monitor +
monthly cycle scripts will skip the early-return paths and execute against
the broker.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

from auto_trader.config import LOG_DIR, LOG_LEVEL
from auto_trader.utils import setup_logging

logger = logging.getLogger(__name__)


def setup_paper_account() -> dict:
    """Run the onboarding flow. Returns a summary dict."""
    today = datetime.now().strftime("%Y%m%d")
    setup_logging(f"{LOG_DIR}paper_trade_setup_{today}.log", LOG_LEVEL)

    summary: dict = {}

    # Step 1: DB
    from auto_trader.state.portfolio_db import initialize_db, log_system_event

    initialize_db()
    summary["db_initialized"] = True

    # Step 2: Broker verification
    from auto_trader.broker.alpaca_client import get_account_info, reset_client
    from auto_trader.credentials import get_trading_mode, use_mock_broker

    reset_client()
    mode = get_trading_mode()
    is_mock = use_mock_broker()
    summary["trading_mode"] = mode
    summary["mock_broker"] = is_mock
    try:
        acct = get_account_info()
        summary["broker"] = {
            "status": acct["status"],
            "cash": acct["cash"],
            "buying_power": acct["buying_power"],
            "trading_blocked": acct["trading_blocked"],
        }
    except Exception as exc:
        logger.exception("Broker verification failed: %s", exc)
        summary["broker_error"] = str(exc)

    # Step 3: Mark paper start (idempotent)
    from auto_trader.credentials import (
        PAPER_START_PATH,
        PAPER_TRADE_MIN_MONTHS,
        write_paper_start,
    )

    when = write_paper_start()
    summary["paper_start_date"] = when
    summary["paper_start_path"] = str(PAPER_START_PATH)
    summary["paper_min_months"] = PAPER_TRADE_MIN_MONTHS

    log_system_event(
        "PAPER_TRADE_SETUP",
        f"Paper trading initialized; start={when}",
        summary,
    )

    # Step 4: Onboarding summary
    print()
    print("=" * 60)
    print("AUTO-TRADER PAPER TRADING — SETUP COMPLETE")
    print("=" * 60)
    print(f"  Mode:               {mode}")
    print(f"  Mock broker:        {is_mock}")
    print(f"  DB initialized:     {summary['db_initialized']}")
    if "broker" in summary:
        print(f"  Broker status:      {summary['broker']['status']}")
        print(f"  Buying power:       ${summary['broker']['buying_power']:,.2f}")
    print(f"  Paper start date:   {when}")
    print(f"  Paper start path:   {PAPER_START_PATH}")
    print(f"  Min paper months:   {PAPER_TRADE_MIN_MONTHS}")
    print()
    print("Next steps:")
    print("  1. Schedule scripts/pre_run_screener.py weekly")
    print("  2. Schedule scripts/daily_run.py weekdays after close")
    print("  3. Schedule scripts/monthly_run.py daily; the 1st-5th gate fires the buys")
    print()
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    setup_paper_account()
    return 0


if __name__ == "__main__":
    sys.exit(main())
