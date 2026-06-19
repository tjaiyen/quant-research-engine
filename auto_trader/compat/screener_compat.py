"""C1: normalize the screener cache to a stable v3 contract.

Every consumer of the screener cache calls ``normalize_screener_cache()``
first. This protects the auto_trader from a runtime KeyError if the
screener output structure changes between versions, and from per-stock
dicts that were generated under an older schema.

The cockpit's Phase J ``screener.screener_main`` already produces v3-shaped
output, so the most common path through this shim is a pass-through.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Expected v3 schema — required keys per stock entry. ``sector`` is
# allowed to be absent when it's implied by the parent dict's key
# (this is the cockpit Phase J convention). We backfill it here.
REQUIRED_STOCK_KEYS: set[str] = {
    "ticker", "composite_score", "passed_veto", "signal_scores",
}


def normalize_screener_cache(cache: dict) -> dict:
    """Ensure ``cache`` has the v3 contract shape.

    Fills missing keys with safe defaults and emits a WARNING log line for
    each one so unexpected drift is loud. The function is non-mutating on
    the top-level dict (returns a new one) but **does** mutate per-stock
    dicts inside ``cache["sectors"]`` for setdefault efficiency.
    """
    regime_raw = cache.get("regime", {}) or {}
    normalized = {
        "regime": {
            "label": regime_raw.get("label", "unknown"),
            "confidence": float(regime_raw.get("confidence", 0.0)),
            "probabilities": regime_raw.get("probabilities", {}) or {},
            "blended_weights": regime_raw.get("blended_weights", {}) or {},
            "stable": bool(regime_raw.get("stable", False)),
        },
        "sectors": cache.get("sectors", {}) or {},
        "summary": cache.get("summary", {}) or {},
        "generated_at": cache.get("generated_at", ""),
        "_cached_at": cache.get("_cached_at", cache.get("generated_at", "")),
    }

    # Normalize per-stock entries
    for sector, stocks in normalized["sectors"].items():
        if not isinstance(stocks, list):
            continue
        for stock in stocks:
            if not isinstance(stock, dict):
                continue
            missing = REQUIRED_STOCK_KEYS - set(stock.keys())
            if missing:
                logger.warning(
                    "Stock %s in %s missing keys %s — filling with defaults",
                    stock.get("ticker", "?"), sector, missing,
                )
                stock.setdefault("composite_score", 0.0)
                stock.setdefault("passed_veto", False)
                stock.setdefault("signal_scores", {})
                stock.setdefault("ticker", "UNKNOWN")
            # Backfill `sector` from the parent dict key when missing
            stock.setdefault("sector", sector)

    if normalized["regime"]["label"] == "unknown":
        logger.warning(
            "Screener cache: regime label is 'unknown' — check screener output version"
        )
    return normalized


def validate_cache_contract(cache: dict) -> tuple[bool, list[str]]:
    """Validate a *normalized* cache against the full v3 contract.

    Returns ``(is_valid, errors)``. The errors list contains plain-English
    descriptions; an empty list means the cache passes.
    """
    errors: list[str] = []

    sectors = cache.get("sectors")
    if not isinstance(sectors, dict):
        errors.append("cache['sectors'] must be a dict")

    regime = cache.get("regime")
    if not isinstance(regime, dict):
        errors.append("cache['regime'] must be a dict")
    else:
        label = regime.get("label")
        if label not in ("bull", "sideways", "bear", "unknown"):
            errors.append(f"Invalid regime label: {label}")

    return len(errors) == 0, errors


__all__ = [
    "REQUIRED_STOCK_KEYS",
    "normalize_screener_cache",
    "validate_cache_contract",
]
