"""Daily-script wrapper around ``signal_decay_monitor.rescore_positions``.

Adds a cadence gate (``SIGNAL_RESCORE_CADENCE_DAYS``) so we don't rescore
every position every day — only the at-risk subset (lowest scores) and
only when the last rescore is older than the cadence.
"""
from __future__ import annotations

import logging
from typing import Optional

from auto_trader.config import SIGNAL_RESCORE_CADENCE_DAYS
from auto_trader.risk.signal_decay_monitor import rescore_positions
from auto_trader.utils import days_since

logger = logging.getLogger(__name__)


def refresh_signals(regime_data: dict, force: bool = False) -> list[dict]:
    """Run the rescore pass if the cadence has elapsed.

    Args:
        regime_data: dict from ``screener.regime.hmm_predictor.get_regime``.
        force: bypass the cadence gate (used by tests + manual runs).

    Returns the list of decay/exit alerts to forward to the alert engine.
    """
    from auto_trader.state.portfolio_db import get_all_positions

    positions = get_all_positions()
    if not positions:
        return []

    if not force:
        last_scored: list[Optional[str]] = [p.get("last_scored_at") for p in positions]
        eligible = [
            p for p, ts in zip(positions, last_scored)
            if ts is None or days_since(ts) >= SIGNAL_RESCORE_CADENCE_DAYS
        ]
        if not eligible:
            logger.info(
                "Signal refresh: all positions rescored within last %d days — skipping",
                SIGNAL_RESCORE_CADENCE_DAYS,
            )
            return []

    return rescore_positions(regime_data)


__all__ = ["refresh_signals"]
