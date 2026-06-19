"""Convert sized allocations into the target portfolio dict the delta engine consumes.

This is intentionally a thin shape-converter. The interesting allocation
logic lives in ``position_sizer.compute_allocations``. ``target_builder``
just keys the list by ticker so the delta engine can do efficient lookups.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_target_portfolio(allocations: list[dict]) -> dict[str, dict]:
    """Index allocations by ticker.

    Args:
        allocations: list of dicts produced by ``compute_allocations``.
            Each must contain at minimum:
            ``{ticker, composite_score, allocation_usd, sector}``.

    Returns:
        Dict keyed by ticker, value is the same dict (with the ticker
        also kept inside for convenience).
    """
    target: dict[str, dict] = {}
    for a in allocations:
        if not isinstance(a, dict) or "ticker" not in a:
            continue
        target[a["ticker"]] = {
            "ticker": a["ticker"],
            "composite_score": float(a.get("composite_score", 0.0)),
            "allocation_usd": float(a.get("allocation_usd", 0.0)),
            "sector": a.get("sector", "UNKNOWN"),
            "regime": a.get("regime", "unknown"),
            "signal_scores": a.get("signal_scores", {}),
        }
    logger.info("Target portfolio: %d positions", len(target))
    return target


__all__ = ["build_target_portfolio"]
