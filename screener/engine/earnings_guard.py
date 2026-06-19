"""Earnings-blackout guard (Upgrade U7).

Pure, DB-free, network-free: given a ticker's next-earnings date, decide whether
it is inside the blackout window. The screener vetoes (and never relaxes) any
candidate within ±``blackout_days`` of earnings to dodge event-gap risk.

Fail-open: an unknown / unparseable date is NOT a veto — never block a name just
because the (Tier-3) earnings feed lacked a date.
"""
from __future__ import annotations

from datetime import date, datetime

VETO_REASON = "EARNINGS_BLACKOUT"


def _parse(d: str | None) -> date | None:
    if not d:
        return None
    s = str(d).strip()[:10]  # tolerate ISO datetimes; take the date part
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def earnings_blackout(
    next_earnings: str | None,
    today: date,
    blackout_days: int,
) -> tuple[bool, str | None]:
    """Return (passed, veto_reason).

    ``passed`` is False (with reason ``EARNINGS_BLACKOUT``) when ``next_earnings``
    is within ``blackout_days`` calendar days of ``today`` on either side.
    Unknown / unparseable date → (True, None) (fail-open).
    """
    ed = _parse(next_earnings)
    if ed is None:
        return (True, None)
    if abs((ed - today).days) <= blackout_days:
        return (False, VETO_REASON)
    return (True, None)


__all__ = ["earnings_blackout", "VETO_REASON"]
