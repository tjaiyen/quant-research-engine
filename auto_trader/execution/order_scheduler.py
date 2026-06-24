"""Wait for the MOO (market-on-open) submission window.

Used by ``monthly_run.py`` so buy orders submit during 9:25–9:28 ET. The
scheduler polls ``is_moo_submission_window()`` every 30 s.
"""
from __future__ import annotations

import logging
import time

from auto_trader.broker.market_calendar import is_moo_submission_window

logger = logging.getLogger(__name__)


def wait_for_moo_window(timeout_seconds: int = 1800, poll_interval: int = 30) -> bool:
    """Block until the MOO window opens, or timeout fires.

    Returns True iff the function exited because the window is open.
    """
    if is_moo_submission_window():
        return True
    # Monotonic clock so an NTP step / manual clock change can't make the
    # deadline appear already-passed (instant false timeout).
    deadline = time.monotonic() + timeout_seconds
    logger.info("Waiting for MOO window… timeout=%ds", timeout_seconds)
    while time.monotonic() < deadline:
        if is_moo_submission_window():
            logger.info("MOO window open — proceeding")
            return True
        time.sleep(poll_interval)
    logger.error("MOO window NOT reached before timeout (%ds)", timeout_seconds)
    return False


__all__ = ["wait_for_moo_window"]
