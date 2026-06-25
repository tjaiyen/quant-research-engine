"""screener/health — per-company health snapshot (Phase 21).

A monitoring overlay (NOT a screener signal): pull the quality metrics yfinance
exposes (ROE, margins, debt/equity, current ratio) and grade them against the
per-sector floors already defined in `industry_config.SECTOR_QUALITY_FLOORS`.
Mirrors the FinBERT sentiment overlay — fetch → store → refresh → render.
"""
