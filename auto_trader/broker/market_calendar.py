"""Trading-day and market-window awareness.

H7: ``is_moo_submission_window()`` uses ``MOO_SUBMIT_MINUTE_START`` and
``MOO_SUBMIT_MINUTE_END`` from config (no hardcoded numbers).

The MOO (market-on-open) window for Alpaca is 9:25–9:28 AM ET. The
auto_trader's monthly buy cycle waits for this window before submitting
``opg`` orders so that fills are deterministic.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import pytz

from auto_trader.broker.alpaca_client import get_client, rate_limited
from auto_trader.config import (
    MONTHLY_CYCLE_DAY,
    MONTHLY_CYCLE_WINDOW_DAYS,
    MOO_SUBMIT_HOUR,
    MOO_SUBMIT_MINUTE_END,
    MOO_SUBMIT_MINUTE_START,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


@rate_limited
def is_trading_day(check_date: Optional[date] = None) -> bool:
    """True if Alpaca's calendar reports an open session on ``check_date``."""
    check_date = check_date or date.today()
    cal = get_client().get_calendar(start=str(check_date), end=str(check_date))
    return len(cal) > 0


def get_et_time() -> datetime:
    """Wall clock in America/New_York."""
    return datetime.now(ET)


def is_moo_submission_window() -> bool:
    """True iff current ET time is inside the MOO submission window.

    H7: window bounds come from config — never hardcode 25/28.
    """
    now = get_et_time()
    return (
        now.hour == MOO_SUBMIT_HOUR
        and MOO_SUBMIT_MINUTE_START <= now.minute <= MOO_SUBMIT_MINUTE_END
    )


def is_fill_confirmation_window() -> bool:
    """True after 10:30 AM ET — when overnight MOO fills are settled."""
    now = get_et_time()
    return now.hour > 10 or (now.hour == 10 and now.minute >= 30)


def get_monthly_cycle_date(
    target_day: int = MONTHLY_CYCLE_DAY,
    window: int = MONTHLY_CYCLE_WINDOW_DAYS,
) -> Optional[date]:
    """Return the trading-day date for this month's cycle, or None.

    Walks from ``target_day`` forward up to ``window`` days, returning the
    first trading day. Returns None if today is outside the [target_day,
    target_day + window] window of the month.
    """
    today = date.today()
    if today.day < target_day or today.day > target_day + window:
        return None

    check = today.replace(day=target_day)
    for _ in range(window + 1):
        if is_trading_day(check):
            return check
        check += timedelta(days=1)
    return None


__all__ = [
    "ET",
    "is_trading_day",
    "get_et_time",
    "is_moo_submission_window",
    "is_fill_confirmation_window",
    "get_monthly_cycle_date",
]
