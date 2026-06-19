"""Filter the screener cache down to eligible buy candidates.

Designed from scratch (spec references AUTO_TRADER_BUILD_v2.md which isn't
available). Logic per v3 contract:

  1. Drop any stock that didn't pass the screener's veto gate
  2. Drop any stock whose composite_score < ``MIN_COMPOSITE_TO_BUY`` (0.60)
  3. Keep top ``TOP_N_PER_SECTOR`` (2) per sector by composite_score desc
  4. Return a flat list of dicts (ticker, sector, composite_score,
     signal_scores, regime) — one entry per stock

The output is consumed by ``position_sizer.compute_allocations``.
"""
from __future__ import annotations

import logging

from auto_trader.config import MIN_COMPOSITE_TO_BUY, TOP_N_PER_SECTOR

logger = logging.getLogger(__name__)


def filter_signals(normalized_cache: dict) -> list[dict]:
    """Return eligible stocks from a normalized screener cache.

    Args:
        normalized_cache: output of ``compat.screener_compat.normalize_screener_cache``.

    Returns:
        List of dicts with shape:
        ``{ticker, sector, composite_score, signal_scores, regime}``.
    """
    regime_label = normalized_cache.get("regime", {}).get("label", "unknown")
    sectors: dict = normalized_cache.get("sectors", {}) or {}

    eligible: list[dict] = []
    for sector, stocks in sectors.items():
        if not isinstance(stocks, list):
            continue

        # Steps 1+2: pass-veto and score-floor
        candidates = [
            s for s in stocks
            if isinstance(s, dict)
            and s.get("passed_veto", False)
            and float(s.get("composite_score", 0.0)) >= MIN_COMPOSITE_TO_BUY
        ]

        # Step 3: top-N per sector by composite_score desc
        candidates.sort(
            key=lambda s: float(s.get("composite_score", 0.0)),
            reverse=True,
        )
        top_n = candidates[:TOP_N_PER_SECTOR]

        for s in top_n:
            eligible.append(
                {
                    "ticker": s["ticker"],
                    "sector": s.get("sector", sector),
                    "composite_score": float(s["composite_score"]),
                    "signal_scores": s.get("signal_scores", {}),
                    "regime": regime_label,
                    # Optionally carry through the GARCH daily_vol if present
                    # (used by score_vol position-sizing mode). The screener
                    # writes this under signal_scores' upstream metadata,
                    # but the cache strips that — we keep a placeholder here
                    # and the sizer will look it up via metadata if available.
                }
            )

    logger.info(
        "Signal filter: %d stocks eligible (top %d/sector, score >= %.2f, regime=%s)",
        len(eligible), TOP_N_PER_SECTOR, MIN_COMPOSITE_TO_BUY, regime_label,
    )
    return eligible


__all__ = ["filter_signals"]
