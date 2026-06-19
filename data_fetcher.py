"""Data fetcher facade with 3-tier confidence detection.

Wraps `data_providers/yfinance_provider.py` without modifying it. Adds:
  - 3-tier data quality detection (Tier 1 premium > Tier 2 enhanced > Tier 3 yfinance)
  - Feature gating — which capabilities are enabled at each tier
  - UI helpers — badge text + confidence score

Tier 3 (yfinance scraped) is the current default. Tiers 1 and 2 are reserved
for future paid/enhanced-feed integrations; detection looks at env vars.

At Tier 3, features like `forward_peg`, `iv_surface`, and `consensus_estimates`
are disabled and every tab is expected to render the
'⚠️ REDUCED DATA CONFIDENCE' badge from `tier_info()`.

Public API:
    detect_data_tier(symbol=None) -> int            # 1 | 2 | 3
    tier_info(tier=None) -> TierInfo                # human label, badge, confidence
    is_feature_enabled(feature, tier=None) -> bool
    disabled_features(tier=None) -> list[str]

    fetch_daily_adjusted, fetch_fundamentals — re-exported from yfinance_provider
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

# Re-export the underlying provider's functions so callers can import from
# `data_fetcher` instead of reaching into `data_providers`.
from data_providers.yfinance_provider import (  # noqa: F401
    ProviderError,
    TickerNotFound,
    fetch_daily_adjusted,
    fetch_fundamentals,
)

DataTier = Literal[1, 2, 3]


# ---------- Feature catalog ----------
# Keys are feature names referenced from other modules; values are the
# minimum tier required to enable the feature. Lower tier number = better
# data; a feature is enabled iff current_tier <= minimum_tier_required.
FEATURE_MIN_TIER: dict[str, DataTier] = {
    # Tier 1 only (paid premium feed)
    "iv_surface":             1,   # implied-vol surface for Black-Scholes
    "intraday_bars":          1,
    "consensus_estimates":    1,   # analyst consensus EPS/revenue
    "fundamentals_history":   1,   # historical fundamental snapshots beyond TTM
    # Tier 2+ (enhanced free feeds, e.g. FMP, Polygon)
    "forward_peg":            2,   # depends on reliable forward earnings
    "ev_ebitda_history":      2,
    "options_chain":          2,
    "earnings_surprises":     2,
    # Tier 3+ (yfinance baseline — always available)
    "daily_prices":           3,
    "current_fundamentals":   3,
    "trailing_pe":            3,
    "monte_carlo":            3,
    "capm_attribution":       3,
    "stress_scenarios":       3,
}


@dataclass(frozen=True)
class TierInfo:
    tier: DataTier
    label: str           # short label for chips / pills
    badge: str           # banner text shown on every tab
    confidence: float    # 0..1 numerical confidence
    description: str     # one-line explanation


_TIER_INFO: dict[DataTier, TierInfo] = {
    1: TierInfo(
        tier=1,
        label="Premium",
        badge="✓ Premium data",
        confidence=1.00,
        description="Paid feed (e.g. Refinitiv, Bloomberg). Full feature set, intraday + consensus.",
    ),
    2: TierInfo(
        tier=2,
        label="Enhanced",
        badge="● Enhanced data",
        confidence=0.75,
        description="Enhanced free feed (e.g. FMP, Polygon paid tier). Forward PEG + options enabled.",
    ),
    3: TierInfo(
        tier=3,
        label="Reduced",
        badge="⚠️ REDUCED DATA CONFIDENCE",
        confidence=0.50,
        description="yfinance scraped data. Forward PEG, IV surface, and consensus disabled.",
    ),
}


def detect_data_tier(symbol: str | None = None) -> DataTier:
    """Detect the data tier currently available.

    Currently returns Tier 3 unless a premium/enhanced API key is wired into
    the environment. Future per-symbol detection (some symbols may be premium-
    only; some may have richer data) is what the `symbol` arg is reserved for.

    Args:
        symbol: optional ticker for per-symbol tier detection. Currently unused.

    Returns:
        1, 2, or 3.
    """
    # Tier 1: explicit premium feed key
    if os.getenv("REFINITIV_API_KEY") or os.getenv("BLOOMBERG_API_KEY"):
        return 1
    # Tier 2: enhanced-free feed key
    if os.getenv("FMP_API_KEY") or os.getenv("POLYGON_API_KEY"):
        return 2
    # Tier 3: yfinance default (current state)
    return 3


def tier_info(tier: DataTier | None = None) -> TierInfo:
    """Return the TierInfo descriptor for a tier (or the current tier if None)."""
    if tier is None:
        tier = detect_data_tier()
    return _TIER_INFO[tier]


def is_feature_enabled(feature: str, tier: DataTier | None = None) -> bool:
    """Whether a named feature is available at the given tier.

    Unknown feature names return False (conservative default).
    """
    if tier is None:
        tier = detect_data_tier()
    minimum = FEATURE_MIN_TIER.get(feature)
    if minimum is None:
        return False
    # Lower tier number = better data; enabled iff we're at or above the
    # minimum required quality.
    return tier <= minimum


def disabled_features(tier: DataTier | None = None) -> list[str]:
    """Return feature names that are disabled at the given tier."""
    if tier is None:
        tier = detect_data_tier()
    return [name for name, minimum in FEATURE_MIN_TIER.items() if tier > minimum]


__all__ = [
    "DataTier",
    "TierInfo",
    "FEATURE_MIN_TIER",
    "detect_data_tier",
    "tier_info",
    "is_feature_enabled",
    "disabled_features",
    # Re-exported provider entrypoints
    "fetch_daily_adjusted",
    "fetch_fundamentals",
    "TickerNotFound",
    "ProviderError",
]
