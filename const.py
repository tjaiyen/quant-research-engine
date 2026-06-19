"""Cross-cutting constants. One place to tune behavior without grepping the whole tree.

Module-specific constants stay in their owning modules (e.g. sector lists in
`tasks/refresh_sectors.py`). This file holds values that are referenced from
**multiple** modules or that callers may want to tune without touching the
math implementations.
"""
from __future__ import annotations

from typing import Final

# ============================================================================
# Risk / scoring
# ============================================================================

# Risk-free rate used by Black-Scholes (`quant_models.black_scholes`).
# Treasury 1Y has been ~4.5% in recent regime; revisit on rate cycle shifts.
RISK_FREE_RATE_ANNUAL: Final[float] = 0.045

# Trading days per calendar year (used wherever annualization happens).
TRADING_DAYS_PER_YEAR: Final[int] = 252

# Monte Carlo defaults (writers and readers should agree).
MC_DEFAULT_HORIZON_DAYS: Final[int] = 21
MC_DEFAULT_N_SIMS: Final[int] = 5_000
MC_DEFAULT_LOOKBACK_DAYS: Final[int] = 252
MC_DETERMINISTIC_SEED: Final[int] = 42
MC_RESULT_TTL_HOURS: Final[int] = 24


# ============================================================================
# ARS scoring (mirror of `scoring_ars.BASE_WEIGHTS` / `_BUCKET_TABLE` for
# external read-only access — single source of truth lives in scoring_ars.py).
# ============================================================================

# Bucket boundaries on the 0–100 scale (lower bound inclusive).
# Synced with `scoring_ars._BUCKET_TABLE`. Update both if tuning.
ARS_BUCKET_THRESHOLDS: Final[dict[str, float]] = {
    "strong_buy": 75.0,
    "buy":        60.0,
    "hold":       45.0,
    "reduce":     30.0,
    "avoid":       0.0,
}


# ============================================================================
# Rate limiting / network
# ============================================================================

# flask-limiter defaults (the production app reads these from app.py for now;
# put here so future tuning has one place to change).
RATE_LIMIT_PER_MINUTE: Final[int] = 600
RATE_LIMIT_PER_SECOND: Final[int] = 60


# ============================================================================
# Stress test windows (mirror of `models_portfolio.STRESS_WINDOWS` for external
# tooling that wants the date ranges without importing the whole module).
# Source of truth still lives in `models_portfolio.py`.
# ============================================================================

STRESS_WINDOW_DATES: Final[dict[str, tuple[str, str]]] = {
    "Covid 2020":      ("2020-02-15", "2020-05-15"),
    "Rate shock 2022": ("2022-01-01", "2022-12-31"),
    "Q4 2018 bear":    ("2018-10-01", "2018-12-31"),
}


# ============================================================================
# Tier thresholds (data confidence)
# ============================================================================

# Mirror of `data_fetcher._TIER_INFO`. Source of truth still lives there.
TIER_CONFIDENCE: Final[dict[int, float]] = {
    1: 1.00,
    2: 0.75,
    3: 0.50,
}


__all__ = [
    "RISK_FREE_RATE_ANNUAL",
    "TRADING_DAYS_PER_YEAR",
    "MC_DEFAULT_HORIZON_DAYS",
    "MC_DEFAULT_N_SIMS",
    "MC_DEFAULT_LOOKBACK_DAYS",
    "MC_DETERMINISTIC_SEED",
    "MC_RESULT_TTL_HOURS",
    "ARS_BUCKET_THRESHOLDS",
    "RATE_LIMIT_PER_MINUTE",
    "RATE_LIMIT_PER_SECOND",
    "STRESS_WINDOW_DATES",
    "TIER_CONFIDENCE",
]
