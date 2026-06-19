"""Sector-level configuration for valuation, scoring, and quality gates.

Single source of truth for assumptions that vary by GICS sector. Keeping
everything in one file makes it easy to tune without hunting through
fundamental.py / scoring_ars.py / quant_models.py.

Conventions:
- WACC ranges are nominal pre-tax discount rates: (low, high) tuples.
- Terminal growth defaults to long-run inflation (2.5%).
- ARS weight tilts shift composite scoring weights by sector
  (e.g. financials weight book value more, tech weights growth more).
"""
from __future__ import annotations

from dataclasses import dataclass

# ---- WACC ranges by sector ----
# Sources blended: Damodaran cost-of-capital tables (2024) + standard finance
# textbook midpoints. These are deliberately conservative bands; tune as needed.
INDUSTRY_WACC: dict[str, tuple[float, float]] = {
    "Technology":             (0.09, 0.12),
    "Communication Services": (0.08, 0.11),
    "Financial Services":     (0.08, 0.11),  # yfinance label
    "Financials":             (0.08, 0.11),  # GICS label fallback
    "Healthcare":             (0.07, 0.10),
    "Energy":                 (0.08, 0.12),
    "Consumer Defensive":     (0.06, 0.09),
    "Consumer Cyclical":      (0.08, 0.11),
    "Industrials":            (0.08, 0.10),
    "Utilities":              (0.05, 0.08),
    "Real Estate":            (0.07, 0.10),
    "Basic Materials":        (0.08, 0.11),
    "Materials":              (0.08, 0.11),
}

DEFAULT_WACC = 0.09
DEFAULT_TERMINAL_GROWTH = 0.025
DEFAULT_HIGH_GROWTH_YEARS = 3
DEFAULT_FADE_YEARS = 2
DEFAULT_HIGH_GROWTH_RATE = 0.06   # 6% — moderate tech-adjacent default
DEFAULT_FADE_TO = DEFAULT_TERMINAL_GROWTH


# ---- Quality floors (minimum thresholds for a name to score 'attractive') ----
# Used by Tab 6 (ARS) and as warnings on Tab 4. Sector-specific because
# financials and utilities operate on different ROE / margin baselines.
@dataclass(frozen=True)
class QualityFloor:
    min_operating_margin: float | None  # None = not enforced
    min_roe: float | None
    max_debt_to_equity: float | None
    min_current_ratio: float | None


# Defaults are intentionally lenient — these are floors, not targets.
_DEFAULT_FLOOR = QualityFloor(
    min_operating_margin=0.05,
    min_roe=0.08,
    max_debt_to_equity=2.0,
    min_current_ratio=1.0,
)


SECTOR_QUALITY_FLOORS: dict[str, QualityFloor] = {
    "Technology":             QualityFloor(0.10, 0.12, 1.5, 1.2),
    "Communication Services": QualityFloor(0.08, 0.10, 2.0, 1.0),
    "Financials":             QualityFloor(None, 0.08, None, None),  # banks have different metrics
    "Financial Services":     QualityFloor(None, 0.08, None, None),
    "Healthcare":             QualityFloor(0.08, 0.10, 1.5, 1.2),
    "Energy":                 QualityFloor(0.05, 0.07, 1.5, 1.0),
    "Consumer Defensive":     QualityFloor(0.05, 0.10, 2.0, 1.0),
    "Consumer Cyclical":      QualityFloor(0.04, 0.10, 2.0, 1.0),
    "Industrials":            QualityFloor(0.06, 0.10, 1.8, 1.0),
    "Utilities":              QualityFloor(0.10, 0.08, 3.5, 0.8),  # capital-heavy, high leverage normal
    "Real Estate":            QualityFloor(None, 0.06, 4.0, None), # REITs have different leverage profile
    "Basic Materials":        QualityFloor(0.05, 0.07, 1.8, 1.2),
    "Materials":              QualityFloor(0.05, 0.07, 1.8, 1.2),
}


# ---- ARS (Aggregate Risk-adjusted Score) weight tilts by sector ----
# Base weights live in scoring_ars.py. These multiplicative tilts adjust the
# blend per sector — e.g. dial up valuation weight for value-heavy sectors,
# growth weight for tech.
@dataclass(frozen=True)
class WeightTilt:
    technical: float = 1.0
    valuation: float = 1.0
    quality: float = 1.0
    growth: float = 1.0
    risk: float = 1.0


SECTOR_WEIGHT_TILTS: dict[str, WeightTilt] = {
    "Technology":             WeightTilt(growth=1.20, valuation=0.90, quality=1.05),
    "Communication Services": WeightTilt(growth=1.10, valuation=0.95),
    "Financials":             WeightTilt(valuation=1.20, quality=1.10, growth=0.85),
    "Financial Services":     WeightTilt(valuation=1.20, quality=1.10, growth=0.85),
    "Healthcare":             WeightTilt(quality=1.10, growth=1.05),
    "Energy":                 WeightTilt(valuation=1.15, risk=1.10),
    "Consumer Defensive":     WeightTilt(quality=1.15, valuation=1.05, growth=0.90),
    "Consumer Cyclical":      WeightTilt(growth=1.05, risk=1.05),
    "Industrials":            WeightTilt(quality=1.05, valuation=1.05),
    "Utilities":              WeightTilt(valuation=1.10, quality=1.10, growth=0.80, risk=0.90),
    "Real Estate":            WeightTilt(valuation=1.10, quality=1.05, growth=0.90),
    "Basic Materials":        WeightTilt(valuation=1.10, risk=1.05),
    "Materials":              WeightTilt(valuation=1.10, risk=1.05),
}


# ---- Public helpers ----

def wacc_for_sector(sector: str | None) -> float:
    """Midpoint WACC for a sector. Returns DEFAULT_WACC if unknown."""
    if sector and sector in INDUSTRY_WACC:
        lo, hi = INDUSTRY_WACC[sector]
        return (lo + hi) / 2.0
    return DEFAULT_WACC


def wacc_band_for_sector(sector: str | None) -> tuple[float, float]:
    """Return (low, high) WACC band for a sector, with sensible defaults."""
    if sector and sector in INDUSTRY_WACC:
        return INDUSTRY_WACC[sector]
    return (DEFAULT_WACC - 0.015, DEFAULT_WACC + 0.015)


def quality_floor_for_sector(sector: str | None) -> QualityFloor:
    if sector and sector in SECTOR_QUALITY_FLOORS:
        return SECTOR_QUALITY_FLOORS[sector]
    return _DEFAULT_FLOOR


def weight_tilt_for_sector(sector: str | None) -> WeightTilt:
    if sector and sector in SECTOR_WEIGHT_TILTS:
        return SECTOR_WEIGHT_TILTS[sector]
    return WeightTilt()


__all__ = [
    "INDUSTRY_WACC",
    "DEFAULT_WACC",
    "DEFAULT_TERMINAL_GROWTH",
    "DEFAULT_HIGH_GROWTH_YEARS",
    "DEFAULT_FADE_YEARS",
    "DEFAULT_HIGH_GROWTH_RATE",
    "QualityFloor",
    "WeightTilt",
    "SECTOR_QUALITY_FLOORS",
    "SECTOR_WEIGHT_TILTS",
    "wacc_for_sector",
    "wacc_band_for_sector",
    "quality_floor_for_sector",
    "weight_tilt_for_sector",
]
