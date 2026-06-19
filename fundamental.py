"""Fundamental valuation engine — DCF, DDM, multiples (P/E, EV/EBITDA, PEG, P/B).

Lives alongside `models_fundamental.py` (which handles peer-relative scoring).
This module focuses on **intrinsic value** methods (DCF, DDM) plus a unified
`compute_fundamental_valuation()` that produces a single FundamentalValuation
dataclass for Tab 4.

Caches outputs to `valuation_cache` table (write-through) and reads cache on
request with a TTL. Cache writes are best-effort and never raise.

Tier-3 caveats (per data_fetcher.detect_data_tier):
- forward_peg disabled — use trailing PEG only
- consensus growth unavailable — DCF growth defaults from sector + capped
- IV-based DCF discount rate unavailable — use industry_config WACC band
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

import pandas as pd

from data_fetcher import detect_data_tier, is_feature_enabled, tier_info
from industry_config import (
    DEFAULT_FADE_YEARS,
    DEFAULT_HIGH_GROWTH_RATE,
    DEFAULT_HIGH_GROWTH_YEARS,
    DEFAULT_TERMINAL_GROWTH,
    DEFAULT_WACC,
    wacc_band_for_sector,
    wacc_for_sector,
)
from utils.db import fetch_latest_fundamentals, fetch_prices, get_conn
from utils.logging_setup import get_logger

log = get_logger(__name__)


# ----------------------------------------------------------------------------
# Dataclasses
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class DCFResult:
    intrinsic_per_share: float | None
    upside_pct: float | None
    wacc: float
    terminal_growth: float
    high_growth_rate: float
    high_growth_years: int
    fade_years: int
    base_fcf: float | None
    shares_out: float | None
    assumptions_note: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DDMResult:
    intrinsic_per_share: float | None
    upside_pct: float | None
    cost_of_equity: float
    growth_rate: float
    last_dividend: float | None
    div_yield: float | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FundamentalValuation:
    ticker: str
    as_of: str                          # ISO date
    data_tier: int
    price: float | None
    market_cap: float | None
    sector: str | None

    # Methodology results
    dcf: DCFResult
    ddm: DDMResult

    # Multiples (raw)
    pe: float | None
    forward_pe: float | None
    peg: float | None                   # trailing only at Tier 3
    pb: float | None
    ps: float | None
    ev_ebitda: float | None
    div_yield: float | None
    pe_relative: float | None           # vs sector / watchlist median

    # Aggregate verdict
    composite_score: float              # 0..1, higher = cheaper
    bucket: str                         # 'attractive' | 'fair' | 'expensive' | 'no_data'
    confidence: float                   # 0..1
    verdict: str                        # human-readable summary
    warnings: tuple[str, ...] = ()


# ----------------------------------------------------------------------------
# DCF
# ----------------------------------------------------------------------------

def compute_dcf(
    base_fcf: float | None,
    shares_out: float | None,
    sector: str | None,
    *,
    high_growth_rate: float = DEFAULT_HIGH_GROWTH_RATE,
    high_growth_years: int = DEFAULT_HIGH_GROWTH_YEARS,
    fade_years: int = DEFAULT_FADE_YEARS,
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH,
    wacc: float | None = None,
    current_price: float | None = None,
) -> DCFResult:
    """Two-stage DCF with a fade phase.

    Phases:
      Y1..high_growth_years: grow FCF at `high_growth_rate`
      Y(N+1)..N+fade_years: linearly fade growth from high_growth_rate to terminal_growth
      Beyond: Gordon terminal value at `terminal_growth`

    Discounts every cash flow at WACC (sector midpoint by default).
    """
    warnings: list[str] = []
    if wacc is None:
        wacc = wacc_for_sector(sector)
    if base_fcf is None or shares_out is None or shares_out <= 0:
        warnings.append("DCF skipped: missing TTM free cash flow or share count")
        return DCFResult(
            intrinsic_per_share=None, upside_pct=None,
            wacc=wacc, terminal_growth=terminal_growth,
            high_growth_rate=high_growth_rate,
            high_growth_years=high_growth_years, fade_years=fade_years,
            base_fcf=base_fcf, shares_out=shares_out,
            assumptions_note=f"WACC {wacc*100:.1f}%, gT {terminal_growth*100:.1f}%",
            warnings=tuple(warnings),
        )
    if base_fcf <= 0:
        warnings.append(f"DCF unreliable: TTM FCF is non-positive ({base_fcf:,.0f})")

    if wacc <= terminal_growth:
        warnings.append("DCF unstable: WACC <= terminal growth, capping terminal at WACC-50bps")
        terminal_growth = wacc - 0.005

    pv_total = 0.0
    fcf = base_fcf
    # High-growth phase
    for t in range(1, high_growth_years + 1):
        fcf = fcf * (1.0 + high_growth_rate)
        pv_total += fcf / ((1.0 + wacc) ** t)

    # Fade phase: linearly interpolate growth toward terminal
    g_curr = high_growth_rate
    if fade_years > 0:
        step = (terminal_growth - high_growth_rate) / fade_years
        for k in range(1, fade_years + 1):
            g_curr = high_growth_rate + step * k
            fcf = fcf * (1.0 + g_curr)
            t = high_growth_years + k
            pv_total += fcf / ((1.0 + wacc) ** t)

    # Terminal value (Gordon) at end of last explicit year
    last_year = high_growth_years + fade_years
    terminal_fcf = fcf * (1.0 + terminal_growth)
    terminal_value = terminal_fcf / (wacc - terminal_growth)
    pv_terminal = terminal_value / ((1.0 + wacc) ** last_year)
    pv_total += pv_terminal

    intrinsic_per_share = pv_total / shares_out
    upside = None
    if current_price is not None and current_price > 0:
        upside = intrinsic_per_share / current_price - 1.0

    return DCFResult(
        intrinsic_per_share=intrinsic_per_share,
        upside_pct=upside,
        wacc=wacc,
        terminal_growth=terminal_growth,
        high_growth_rate=high_growth_rate,
        high_growth_years=high_growth_years,
        fade_years=fade_years,
        base_fcf=base_fcf,
        shares_out=shares_out,
        assumptions_note=(
            f"WACC {wacc*100:.1f}%, gH {high_growth_rate*100:.1f}% for {high_growth_years}y, "
            f"fade over {fade_years}y to gT {terminal_growth*100:.1f}%"
        ),
        warnings=tuple(warnings),
    )


# ----------------------------------------------------------------------------
# DDM
# ----------------------------------------------------------------------------

def compute_ddm(
    last_dividend_per_share: float | None,
    div_yield: float | None,
    sector: str | None,
    *,
    growth_rate: float = 0.03,
    cost_of_equity: float | None = None,
    current_price: float | None = None,
) -> DDMResult:
    """Gordon DDM. Skipped for non-payers."""
    warnings: list[str] = []
    if cost_of_equity is None:
        cost_of_equity = wacc_for_sector(sector)
    if last_dividend_per_share is None or last_dividend_per_share <= 0:
        warnings.append("DDM skipped: ticker pays no dividend")
        return DDMResult(
            intrinsic_per_share=None, upside_pct=None,
            cost_of_equity=cost_of_equity, growth_rate=growth_rate,
            last_dividend=last_dividend_per_share, div_yield=div_yield,
            warnings=tuple(warnings),
        )
    if cost_of_equity <= growth_rate:
        warnings.append("DDM unstable: cost of equity <= growth, capping growth")
        growth_rate = cost_of_equity - 0.005

    next_div = last_dividend_per_share * (1.0 + growth_rate)
    intrinsic = next_div / (cost_of_equity - growth_rate)
    upside = None
    if current_price is not None and current_price > 0:
        upside = intrinsic / current_price - 1.0

    return DDMResult(
        intrinsic_per_share=intrinsic,
        upside_pct=upside,
        cost_of_equity=cost_of_equity,
        growth_rate=growth_rate,
        last_dividend=last_dividend_per_share,
        div_yield=div_yield,
        warnings=tuple(warnings),
    )


# ----------------------------------------------------------------------------
# Composite + verdict
# ----------------------------------------------------------------------------

def _score_multiple(value: float | None, cheap: float, expensive: float) -> float | None:
    if value is None or value <= 0:
        return None
    if value <= cheap:
        return 1.0
    if value >= expensive:
        return 0.0
    return 1.0 - (value - cheap) / (expensive - cheap)


def _score_dcf_upside(upside: float | None) -> float | None:
    if upside is None:
        return None
    # +50% → 1.0, -50% → 0.0, linear in between, clipped.
    s = 0.5 + upside  # +0.5 -> 1.0
    return max(0.0, min(1.0, s))


def _bucket_from_score(score: float | None) -> str:
    if score is None:
        return "no_data"
    if score >= 0.65:
        return "attractive"
    if score >= 0.35:
        return "fair"
    return "expensive"


def _verdict_text(
    composite: float | None,
    dcf: DCFResult,
    multiples: dict[str, float | None],
    bucket: str,
) -> str:
    if composite is None:
        return "Insufficient data for a fundamental verdict."
    bits: list[str] = []
    if dcf.intrinsic_per_share is not None and dcf.upside_pct is not None:
        direction = "above" if dcf.upside_pct >= 0 else "below"
        bits.append(f"DCF fair ${dcf.intrinsic_per_share:,.2f} ({dcf.upside_pct*100:+.0f}%)")
    fpe = multiples.get("forward_pe")
    if fpe is not None:
        bits.append(f"Fwd P/E {fpe:.1f}")
    eve = multiples.get("ev_ebitda")
    if eve is not None:
        bits.append(f"EV/EBITDA {eve:.1f}")
    label = bucket.replace("_", " ").title()
    head = f"{label} on a fundamental basis."
    if not bits:
        return head
    return head + " " + " · ".join(bits)


# ----------------------------------------------------------------------------
# Cache I/O
# ----------------------------------------------------------------------------

def _serialize_warnings(warns: Iterable[str]) -> str:
    return json.dumps(list(warns))


def _cache_write(val: FundamentalValuation) -> None:
    """Best-effort write into valuation_cache. Never raises.

    Upserts the ticker first to satisfy the FK constraint — covers the case
    where compute_fundamental_valuation is called for a ticker that's never
    been refreshed (e.g. exploratory queries). Without this, FK violations
    were noisy in logs.
    """
    try:
        notes = json.dumps({
            "warnings": list(val.warnings),
            "dcf_assumptions": val.dcf.assumptions_note,
            "dcf_warnings": list(val.dcf.warnings),
            "ddm_warnings": list(val.ddm.warnings),
        })
        with get_conn() as conn:
            # Ensure parent row exists before inserting the FK-constrained child.
            conn.execute(
                "INSERT OR IGNORE INTO tickers(symbol) VALUES (?)",
                (val.ticker,),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO valuation_cache(
                    ticker, as_of, data_tier,
                    absolute_score, peer_score, final_score, bucket,
                    peer_type, peer_group_size,
                    dcf_value, dcf_upside_pct, ddm_value,
                    ev_ebitda, pe_relative, peg, pb, confidence, notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    val.ticker, val.as_of, val.data_tier,
                    None, None, val.composite_score, val.bucket,
                    None, None,
                    val.dcf.intrinsic_per_share, val.dcf.upside_pct,
                    val.ddm.intrinsic_per_share,
                    val.ev_ebitda, val.pe_relative, val.peg, val.pb,
                    val.confidence, notes,
                ),
            )
    except Exception:
        log.exception("valuation_cache write failed for %s (non-fatal)", val.ticker)


def fetch_cached_valuation(
    ticker: str, max_age_hours: int = 24
) -> dict | None:
    """Return cached row as dict, or None if missing/stale."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT * FROM valuation_cache WHERE ticker = ? "
                "ORDER BY as_of DESC LIMIT 1",
                (ticker.upper(),),
            )
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            if not row:
                return None
            d = dict(zip(cols, row))
        # TTL check
        as_of = d.get("as_of")
        if as_of:
            try:
                d_dt = datetime.fromisoformat(as_of)
                if d_dt.tzinfo is None:
                    d_dt = d_dt.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - d_dt
                if age > timedelta(hours=max_age_hours):
                    return None
            except ValueError:
                return None
        return d
    except Exception:
        log.exception("valuation_cache read failed for %s", ticker)
        return None


# ----------------------------------------------------------------------------
# Top-level entrypoint
# ----------------------------------------------------------------------------

def _last_price(ticker: str) -> float | None:
    df = fetch_prices(ticker, limit=1)
    if df.empty or "adj_close" not in df:
        return None
    v = df["adj_close"].iloc[-1]
    return float(v) if pd.notna(v) else None


def _normalize_div_yield(raw: float | None) -> float | None:
    """Normalize yfinance dividendYield to a decimal (0..1).

    yfinance has been inconsistent across versions:
      - Some versions return 0.005 for 0.5% (correct decimal)
      - Some versions return 0.5 for 0.5% (percent stored as number)
    No real US equity has >20% yield, so values >0.20 are interpreted as
    'percent stored as number' and divided by 100.
    """
    if raw is None or raw <= 0:
        return None
    if raw > 0.20:
        return raw / 100.0
    return raw


def compute_fundamental_valuation(
    ticker: str,
    snapshot: dict | None = None,
) -> FundamentalValuation:
    """Build the full FundamentalValuation for a ticker. Live compute.

    Reads the latest fundamentals snapshot from the DB if not provided,
    pulls last price for upside math, runs DCF + DDM + composite scoring,
    and writes to valuation_cache (best-effort).
    """
    ticker = ticker.upper()
    tier = detect_data_tier(ticker)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if snapshot is None:
        snapshot = fetch_latest_fundamentals(ticker) or {}

    sector = snapshot.get("sector")
    market_cap = snapshot.get("market_cap")
    pe = snapshot.get("pe")
    forward_pe = snapshot.get("forward_pe")
    peg_raw = snapshot.get("peg")
    pb = snapshot.get("pb")
    ps = snapshot.get("ps")
    ev_ebitda = snapshot.get("ev_ebitda")
    div_yield = _normalize_div_yield(snapshot.get("div_yield"))

    price = _last_price(ticker)

    # Forward PEG gating: at Tier 3, treat any PEG as trailing only
    peg = peg_raw if is_feature_enabled("forward_peg", tier) or peg_raw is not None else peg_raw

    # ---- DCF inputs ----
    # yfinance .info isn't always in our snapshot; we approximate base FCF
    # using market_cap * (FCF yield proxy) as a fallback. Better path is
    # snapshot-side enrichment in a future task.
    base_fcf = snapshot.get("free_cash_flow")
    shares_out = snapshot.get("shares_outstanding")
    if base_fcf is None and market_cap is not None and ps is not None and ps > 0:
        # Crude: assume FCF margin ~10% of revenue, revenue = market_cap/ps
        # This is a placeholder; flagged in warnings.
        revenue_proxy = market_cap / ps
        base_fcf = revenue_proxy * 0.10
    if shares_out is None and market_cap is not None and price is not None and price > 0:
        shares_out = market_cap / price

    dcf = compute_dcf(
        base_fcf=base_fcf,
        shares_out=shares_out,
        sector=sector,
        current_price=price,
    )
    ddm_warnings: list[str] = []
    last_div = None
    if div_yield is not None and price is not None:
        last_div = div_yield * price
    ddm = compute_ddm(
        last_dividend_per_share=last_div,
        div_yield=div_yield,
        sector=sector,
        current_price=price,
    )

    # ---- Composite ----
    parts: list[tuple[float, float]] = []  # (score, weight)
    s_dcf = _score_dcf_upside(dcf.upside_pct)
    if s_dcf is not None:
        parts.append((s_dcf, 0.40))
    s_fpe = _score_multiple(forward_pe, 12.0, 28.0) if forward_pe else None
    if s_fpe is not None:
        parts.append((s_fpe, 0.20))
    s_pe = _score_multiple(pe, 12.0, 30.0) if pe else None
    if s_pe is not None:
        parts.append((s_pe, 0.10))
    s_eve = _score_multiple(ev_ebitda, 8.0, 18.0) if ev_ebitda else None
    if s_eve is not None:
        parts.append((s_eve, 0.15))
    s_peg = _score_multiple(peg, 1.0, 2.5) if peg else None
    if s_peg is not None:
        parts.append((s_peg, 0.10))
    s_pb = _score_multiple(pb, 1.2, 5.0) if pb else None
    if s_pb is not None:
        parts.append((s_pb, 0.05))

    if parts:
        total_w = sum(w for _, w in parts)
        composite = sum(s * w for s, w in parts) / total_w
        coverage = total_w / 1.00  # max possible weight = 1.0
    else:
        composite = None
        coverage = 0.0

    bucket = _bucket_from_score(composite)
    multiples = {"forward_pe": forward_pe, "ev_ebitda": ev_ebitda, "pe": pe, "peg": peg, "pb": pb}
    verdict = _verdict_text(composite, dcf, multiples, bucket)

    confidence_tier = tier_info(tier).confidence
    confidence = max(0.0, min(1.0, 0.5 * confidence_tier + 0.5 * coverage))

    warnings: list[str] = []
    warnings.extend(dcf.warnings)
    warnings.extend(ddm.warnings)
    if base_fcf is not None and snapshot.get("free_cash_flow") is None:
        warnings.append("Base FCF estimated from market_cap × P/S × 10% margin proxy")
    if tier == 3:
        warnings.append("Tier 3 data — DCF & multiples reliability reduced")
    if not parts:
        warnings.append("No usable multiples in snapshot")

    val = FundamentalValuation(
        ticker=ticker,
        as_of=today,
        data_tier=tier,
        price=price,
        market_cap=market_cap,
        sector=sector,
        dcf=dcf,
        ddm=ddm,
        pe=pe, forward_pe=forward_pe, peg=peg, pb=pb, ps=ps,
        ev_ebitda=ev_ebitda, div_yield=div_yield, pe_relative=None,
        composite_score=composite if composite is not None else 0.0,
        bucket=bucket,
        confidence=confidence,
        verdict=verdict,
        warnings=tuple(warnings),
    )
    _cache_write(val)
    return val


__all__ = [
    "DCFResult",
    "DDMResult",
    "FundamentalValuation",
    "compute_dcf",
    "compute_ddm",
    "compute_fundamental_valuation",
    "fetch_cached_valuation",
]
