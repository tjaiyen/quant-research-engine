"""Quant models — HMM regime detection, Black-Scholes pricing, MC cache reader.

Lives alongside `models_quant.py` (which holds the legacy live-compute risk
metrics). This module focuses on:

  - HMM-based regime detection with a `rolling_vol_regime()` fallback if
    `hmmlearn` import fails.
  - Black-Scholes Merton pricing (uses historical vol at Tier 3 since
    IV surface is disabled — flagged in output).
  - Read-side helpers for the `mc_results` cache. **Write happens only in
    `tasks/precompute_mc.py`** per the cache-only Monte Carlo policy —
    UI callbacks must never compute MC live.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from data_fetcher import is_feature_enabled
from utils.db import fetch_prices, get_conn
from utils.logging_setup import get_logger

log = get_logger(__name__)

TRADING_DAYS = 252


# ============================================================================
# HMM regime detection (with fallback)
# ============================================================================

REGIME_LABELS = ("low_vol", "neutral", "high_vol")


@dataclass(frozen=True)
class RegimeResult:
    method: str                       # 'hmm' | 'rolling_vol'
    n_states: int
    current_label: str                # one of REGIME_LABELS
    current_state: int                # 0..n_states-1
    state_means: tuple[float, ...]    # daily mean return per state
    state_vols: tuple[float, ...]     # daily vol per state
    state_history: tuple[int, ...]    # last 60 state ids (most recent last)
    confidence: float                 # 0..1
    notes: str


def _label_for_state(idx_sorted_by_vol: int, n: int) -> str:
    """Map sorted-vol state position to a human label."""
    if n <= 1:
        return "neutral"
    if idx_sorted_by_vol == 0:
        return "low_vol"
    if idx_sorted_by_vol == n - 1:
        return "high_vol"
    return "neutral"


def _hmm_regimes(returns: np.ndarray, n_states: int = 3) -> RegimeResult | None:
    """Fit a Gaussian HMM to log returns. Returns None on import or fit failure."""
    try:
        from hmmlearn.hmm import GaussianHMM
    except Exception:
        log.warning("hmmlearn unavailable; falling back to rolling_vol_regime.")
        return None

    X = returns.reshape(-1, 1)
    try:
        model = GaussianHMM(
            n_components=n_states,
            covariance_type="full",
            n_iter=200,
            random_state=42,
        )
        model.fit(X)
        states = model.predict(X)
    except Exception as e:
        log.warning("HMM fit failed: %s — falling back to rolling_vol_regime.", e)
        return None

    means = [float(m[0]) for m in model.means_]
    vols = [float(np.sqrt(c[0, 0])) for c in model.covars_]

    # Re-label states by ascending vol for stable interpretation.
    sort_order = np.argsort(vols)
    inverse = {old: new for new, old in enumerate(sort_order)}
    states_relabeled = np.array([inverse[s] for s in states])
    means_sorted = [means[i] for i in sort_order]
    vols_sorted = [vols[i] for i in sort_order]

    current = int(states_relabeled[-1])
    history = tuple(int(s) for s in states_relabeled[-60:])

    # Confidence ≈ posterior probability of the current state at last step.
    try:
        posteriors = model.predict_proba(X[-1:])[0]
        # Map posterior to relabeled order.
        confidence = float(posteriors[sort_order[current]])
    except Exception:
        confidence = 0.7

    return RegimeResult(
        method="hmm",
        n_states=n_states,
        current_label=_label_for_state(current, n_states),
        current_state=current,
        state_means=tuple(means_sorted),
        state_vols=tuple(vols_sorted),
        state_history=history,
        confidence=confidence,
        notes=f"GaussianHMM(n_states={n_states}) over {len(returns)} daily log returns",
    )


def rolling_vol_regime(
    returns: np.ndarray, window: int = 21, n_buckets: int = 3
) -> RegimeResult:
    """Fallback regime detector: bucket rolling realized vol into terciles.

    Lives at the same call site as the HMM but uses simple quantiles —
    no model fit, no extra deps. Always succeeds for sequences ≥ window.
    """
    series = pd.Series(returns)
    rv = series.rolling(window=window, min_periods=max(5, window // 2)).std()
    rv = rv.dropna()
    if len(rv) < 5:
        return RegimeResult(
            method="rolling_vol",
            n_states=1, current_label="neutral", current_state=0,
            state_means=(float(returns.mean()) if len(returns) else 0.0,),
            state_vols=(float(returns.std(ddof=1)) if len(returns) > 1 else 0.0,),
            state_history=(0,),
            confidence=0.3,
            notes="Insufficient history for rolling-vol regimes",
        )

    quantile_edges = np.quantile(rv.values, np.linspace(0, 1, n_buckets + 1))
    # np.digitize returns 1..n; subtract 1 to get 0..n-1, clip to handle edges.
    state_ids = np.clip(np.digitize(rv.values, quantile_edges[1:-1]), 0, n_buckets - 1)

    # Per-state statistics on the underlying (non-rolling) returns aligned to rv.
    aligned_returns = series.iloc[-len(rv):].values
    state_means: list[float] = []
    state_vols: list[float] = []
    for s in range(n_buckets):
        mask = state_ids == s
        if mask.sum() == 0:
            state_means.append(0.0)
            state_vols.append(0.0)
        else:
            state_means.append(float(aligned_returns[mask].mean()))
            state_vols.append(float(aligned_returns[mask].std(ddof=1) if mask.sum() > 1 else 0.0))

    current = int(state_ids[-1])
    return RegimeResult(
        method="rolling_vol",
        n_states=n_buckets,
        current_label=_label_for_state(current, n_buckets),
        current_state=current,
        state_means=tuple(state_means),
        state_vols=tuple(state_vols),
        state_history=tuple(int(s) for s in state_ids[-60:]),
        confidence=0.5,
        notes=f"Rolling {window}d realized vol bucketed into {n_buckets} terciles",
    )


def detect_regime(
    ticker: str, n_states: int = 3, lookback_days: int = 504
) -> RegimeResult | None:
    """Top-level regime detection. Tries HMM first, falls back to rolling vol."""
    df = fetch_prices(ticker, limit=lookback_days + 10)
    if df.empty or "adj_close" not in df:
        return None
    px = df["adj_close"].dropna()
    if len(px) < 60:
        return None
    log_returns = np.log(px / px.shift(1)).dropna().values

    hmm_result = _hmm_regimes(log_returns, n_states=n_states)
    if hmm_result is not None:
        return hmm_result
    return rolling_vol_regime(log_returns, window=21, n_buckets=n_states)


# ============================================================================
# Black-Scholes Merton
# ============================================================================

@dataclass(frozen=True)
class BlackScholesResult:
    spot: float
    strike: float
    days_to_expiry: int
    rate_annual: float
    sigma_annual: float
    sigma_source: str                 # 'historical' | 'iv_surface'
    call_price: float
    put_price: float
    call_delta: float
    put_delta: float
    notes: str = ""


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(
    spot: float,
    strike: float,
    days_to_expiry: int,
    sigma_annual: float,
    rate_annual: float | None = None,
    sigma_source: str = "historical",
    notes: str = "",
) -> BlackScholesResult:
    if rate_annual is None:
        from const import RISK_FREE_RATE_ANNUAL
        rate_annual = RISK_FREE_RATE_ANNUAL
    """Standard Black-Scholes-Merton pricing for a European call & put.

    `sigma_annual` is the annualized volatility input. At Tier 3 we use
    historical realized vol (IV surface unavailable). Caller should pass
    `sigma_source='historical'` and surface the limitation in the UI.
    """
    if days_to_expiry <= 0 or sigma_annual <= 0 or spot <= 0 or strike <= 0:
        return BlackScholesResult(
            spot=spot, strike=strike, days_to_expiry=days_to_expiry,
            rate_annual=rate_annual, sigma_annual=sigma_annual,
            sigma_source=sigma_source,
            call_price=max(0.0, spot - strike),
            put_price=max(0.0, strike - spot),
            call_delta=1.0 if spot > strike else 0.0,
            put_delta=-1.0 if spot < strike else 0.0,
            notes=notes or "degenerate inputs — returned intrinsic value",
        )
    T = days_to_expiry / 365.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (rate_annual + 0.5 * sigma_annual ** 2) * T) / (sigma_annual * sqrtT)
    d2 = d1 - sigma_annual * sqrtT
    nd1 = _norm_cdf(d1)
    nd2 = _norm_cdf(d2)
    nmd1 = _norm_cdf(-d1)
    nmd2 = _norm_cdf(-d2)
    discount = math.exp(-rate_annual * T)

    call = spot * nd1 - strike * discount * nd2
    put = strike * discount * nmd2 - spot * nmd1
    return BlackScholesResult(
        spot=spot, strike=strike, days_to_expiry=days_to_expiry,
        rate_annual=rate_annual, sigma_annual=sigma_annual,
        sigma_source=sigma_source,
        call_price=call, put_price=put,
        call_delta=nd1, put_delta=nd1 - 1.0,
        notes=notes,
    )


def realized_vol_annual(ticker: str, window_days: int = 60) -> float | None:
    """Helper for Black-Scholes when IV surface is gated off (Tier 3 default)."""
    df = fetch_prices(ticker, limit=window_days + 10)
    if df.empty or "adj_close" not in df:
        return None
    px = df["adj_close"].tail(window_days).dropna()
    if len(px) < 10:
        return None
    log_returns = np.log(px / px.shift(1)).dropna()
    if len(log_returns) < 5:
        return None
    return float(log_returns.std(ddof=1) * math.sqrt(TRADING_DAYS))


# ============================================================================
# Monte Carlo cache I/O — READ ONLY in this module.
# Writes happen exclusively in tasks/precompute_mc.py.
# ============================================================================

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_cached_mc(
    scenario_key: str, max_age_hours: int | None = None
) -> dict | None:
    """Return cached MC result row by scenario_key or None if missing/stale.

    Reads only; this function never writes. Use `tasks/precompute_mc.py` to
    populate the cache.
    """
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT * FROM mc_results WHERE scenario_key = ? LIMIT 1",
                (scenario_key,),
            )
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            if not row:
                return None
            d = dict(zip(cols, row))
        if max_age_hours is not None:
            run_at = d.get("run_at")
            if run_at:
                try:
                    dt = datetime.fromisoformat(run_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - dt > timedelta(hours=max_age_hours):
                        return None
                except ValueError:
                    return None
        else:
            ttl = d.get("ttl_hours") or 24
            run_at = d.get("run_at")
            if run_at:
                try:
                    dt = datetime.fromisoformat(run_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - dt > timedelta(hours=ttl):
                        return None
                except ValueError:
                    return None
        # Decode JSON columns
        for k in ("percentiles_json", "components_json", "asset_names_json"):
            if d.get(k):
                try:
                    d[k.replace("_json", "")] = json.loads(d[k])
                except Exception:
                    d[k.replace("_json", "")] = None
        return d
    except Exception:
        log.exception("mc_results read failed for key=%s", scenario_key)
        return None


def list_cached_mc_runs(limit: int = 20) -> list[dict]:
    """Recent MC runs for the diagnostics view."""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT scenario_key, run_at, stress_label, horizon_days, "
                "n_sims, current_value, var_pct_95, status "
                "FROM mc_results ORDER BY run_at DESC LIMIT ?",
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        log.exception("list_cached_mc_runs failed")
        return []


__all__ = [
    "REGIME_LABELS",
    "RegimeResult",
    "detect_regime",
    "rolling_vol_regime",
    "BlackScholesResult",
    "black_scholes",
    "realized_vol_annual",
    "fetch_cached_mc",
    "list_cached_mc_runs",
]
