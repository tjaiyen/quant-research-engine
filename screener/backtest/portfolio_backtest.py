"""Strategy portfolio backtest (Insight U4).

A historical *portfolio simulation* of the strategy: over rebalance dates, pick
the screener's per-sector top-N as-of that date (causal — history sliced to the
date), hold equal-weight to the next rebalance, and mark-to-market vs SPY. Produces
an equity curve + metrics (total return, CAGR, max drawdown, Sharpe, vs SPY).

This is NOT a bit-exact replay of the auto_trader's broker/guards/sizing — it's a
strategy-level simulation. Reuses the walk-forward scoring machinery; sampled
(quarterly) for runtime. Pure metric helpers are unit-tested; the scoring loop is
covered by monkeypatching the per-date scorer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from screener.config import HMM_LOOKBACK_YEARS, TOP_N_OUTPUT, YFIN_MIN_ROWS_REQUIRED

logger = logging.getLogger(__name__)


# ── pure metric helpers ──────────────────────────────────────────────────────

def _cagr(total_return: float, years: float) -> float:
    if years <= 0:
        return 0.0
    if total_return <= -1.0:          # wiped out: (1+r) <= 0 → fractional power is
        return -1.0                   # complex; report a total loss instead of crashing
    return float((1.0 + total_return) ** (1.0 / years) - 1.0)


def _max_drawdown(equity: list[float]) -> float:
    """Worst peak-to-trough decline of an equity series (a negative number)."""
    peak, mdd = -1e18, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return float(mdd)


def _sharpe(seg_returns: list[float], periods_per_year: float) -> float | None:
    if len(seg_returns) < 2:
        return None
    arr = np.asarray(seg_returns, float)
    sd = arr.std(ddof=1)
    if sd < 1e-12:           # treat ~constant returns as undefined Sharpe
        return None
    return float(arr.mean() / sd * np.sqrt(periods_per_year))


# ── rebalance dates ──────────────────────────────────────────────────────────

def _rebalance_dates(index: pd.DatetimeIndex, years: int, step_days: int) -> list[pd.Timestamp]:
    end = index[-1]
    start = end - pd.Timedelta(days=int(years * 365.25))
    dates, d = [], start
    while d < end - pd.Timedelta(days=step_days):
        pos = index.searchsorted(d)
        if pos < len(index):
            dates.append(index[min(pos, len(index) - 1)])
        d += pd.Timedelta(days=step_days)
    # de-dup while preserving order
    seen, out = set(), []
    for x in dates:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


# ── the backtest ─────────────────────────────────────────────────────────────

_STEP = {"month": 30, "quarter": 91}


def _picks_at(date, regime, histories: dict, sectors: dict, top_n: int,
              score_fn) -> list[str]:
    """Per-sector top-N passed-veto tickers as-of ``date`` (causal scoring)."""
    from screener.backtest.walk_forward import _slice_history_to
    picks: list[str] = []
    for _sector, tickers in sectors.items():
        scored = []
        for t in tickers:
            ph = histories.get(t)
            if ph is None:
                continue
            ph_train = _slice_history_to(ph, date)
            if len(ph_train) < YFIN_MIN_ROWS_REQUIRED:
                continue
            try:
                res = score_fn(t, regime, ph_train)
            except Exception:
                continue
            if res.get("passed_veto"):
                scored.append((res["composite_score"], t))
        scored.sort(reverse=True)
        picks.extend(t for _s, t in scored[:top_n])
    return picks


def _segment_return(tickers: list[str], d0, d1) -> float:
    """Equal-weight return of ``tickers`` from d0→d1 (skips names w/o prices)."""
    from utils.db import price_on_or_before
    rets = []
    for t in tickers:
        p0 = price_on_or_before(t, d0.isoformat())
        p1 = price_on_or_before(t, d1.isoformat())
        if p0 and p1:
            rets.append(p1 / p0 - 1.0)
    return float(np.mean(rets)) if rets else 0.0


def run_portfolio_backtest(years: int = 3, rebalance: str = "quarter",
                           max_per_sector: int | None = 8,
                           top_n_per_sector: int = TOP_N_OUTPUT,
                           score_fn=None, regime_fn=None) -> dict:
    """Simulate the strategy as an equal-weight rebalanced portfolio vs SPY.

    ``score_fn``/``regime_fn`` are injectable for testing; production uses the
    real screener scorer + per-date HMM regime.
    """
    from screener.backtest.walk_forward import _ph_for, _load_universe, _regime_at
    from screener.data.market_features import get_market_features

    if score_fn is None:
        from screener.engine.composite_scorer import score_stock as score_fn
    if regime_fn is None:
        regime_fn = _regime_at

    sectors = _load_universe()
    if max_per_sector:
        sectors = {s: t[:max_per_sector] for s, t in sectors.items()}
    flat = {t for ts in sectors.values() for t in ts}
    histories = {t: _ph_for(t) for t in flat}
    histories = {t: ph for t, ph in histories.items() if ph is not None}

    features = get_market_features(lookback_years=HMM_LOOKBACK_YEARS, min_rows=300)
    dates = _rebalance_dates(features.index, years, _STEP.get(rebalance, 91))

    base = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "years": years, "rebalance": rebalance,
        "n_rebalances": 0, "equity_curve": [], "metrics": {}, "avg_picks": 0.0,
    }
    if len(dates) < 2:
        return base

    equity, spy_equity = 1.0, 1.0
    curve = [{"date": str(dates[0].date()), "strategy": 100.0, "spy": 100.0}]
    seg_rets, n_picks = [], []
    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        try:
            regime = regime_fn(features, d0)
        except Exception as exc:
            logger.debug("regime skip at %s: %s", d0.date(), exc)
            continue
        picks = _picks_at(d0, regime, histories, sectors, top_n_per_sector, score_fn)
        n_picks.append(len(picks))
        seg = _segment_return(picks, d0, d1) if picks else 0.0
        spy_seg = _segment_return(["SPY"], d0, d1)
        equity *= (1.0 + seg)
        spy_equity *= (1.0 + spy_seg)
        seg_rets.append(seg)
        curve.append({"date": str(d1.date()),
                      "strategy": round(equity * 100, 2),
                      "spy": round(spy_equity * 100, 2)})

    total = equity - 1.0
    spy_total = spy_equity - 1.0
    ppy = 365.25 / _STEP.get(rebalance, 91)
    base.update({
        "n_rebalances": len(seg_rets),
        "avg_picks": float(np.mean(n_picks)) if n_picks else 0.0,
        "equity_curve": curve,
        "metrics": {
            "total_return": total,
            "spy_total_return": spy_total,
            "excess": total - spy_total,
            "cagr": _cagr(total, years),
            "max_drawdown": _max_drawdown([c["strategy"] for c in curve]),
            "sharpe": _sharpe(seg_rets, ppy),
            "win_rate": float(np.mean([1.0 if r > 0 else 0.0 for r in seg_rets]))
                        if seg_rets else None,
        },
    })
    return base


__all__ = ["run_portfolio_backtest", "_cagr", "_max_drawdown", "_sharpe"]
