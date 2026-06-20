"""Composite-score builder.

STRICT ORDERING (do not change without re-running every gate):
    1. Run all 5 signals
    2. Pull veto inputs from signal metadata
    3. Apply regime-adjusted veto
    4. Compute weighted composite ONLY if veto passed (else 0.0)

  - M4: NaN/infinite score guard on each signal before weighting
  - H4: ``GARCH_COMPOSITE_MODE`` switch — exclude GARCH from composite
        when the operator wants it as a veto-only signal.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from datetime import datetime, timezone

from screener.config import (
    EARNINGS_BLACKOUT_DAYS,
    EARNINGS_BLACKOUT_ENABLED,
    EXPECTED_SIGNAL_KEYS,
    FORECAST_HORIZON_DAYS,
    GARCH_COMPOSITE_MODE,
    SENTIMENT_VETO_ENABLED,
    SENTIMENT_VETO_THRESHOLD,
)
from screener.engine.earnings_guard import earnings_blackout
from screener.engine.veto_gate import apply_veto
from screener.signals.arima_signal import arima_signal
from screener.signals.garch_signal import garch_signal
from screener.signals.kalman_signal import kalman_signal
from screener.signals.monte_carlo_signal import monte_carlo_signal
from screener.signals.sharpe_signal import sharpe_signal

logger = logging.getLogger(__name__)

# (name, fn) — order is reproducible / stable for downstream serialization
SIGNAL_FUNCTIONS: list[tuple[str, Any]] = [
    ("arima", arima_signal),
    ("kalman", kalman_signal),
    ("garch", garch_signal),
    ("monte_carlo", monte_carlo_signal),
    ("sharpe", sharpe_signal),
]


def score_stock(
    ticker: str,
    regime_data: dict,
    price_history: pd.DataFrame,
    next_earnings: str | None = None,
    cached_sentiment: dict | None = None,
) -> dict:
    """Full scoring pipeline for one stock.

    Args:
        ticker: uppercase symbol.
        regime_data: dict from ``screener.regime.hmm_predictor.get_regime``.
            Must contain ``regime``, ``confidence``, ``blended_weights``.
        price_history: OHLCV DataFrame with capitalized columns
            (``Open, High, Low, Close, Volume``).

    Returns:
        A dict with the per-stock screener result. Contract documented in
        ``RECON_REPORT.md`` (signal_scores, signal_contributions, veto_detail,
        composite_score, regime, regime_confidence, ticker, passed_veto).
    """
    # STEP 1: run all 5 signals
    results: dict[str, dict] = {}
    for name, fn in SIGNAL_FUNCTIONS:
        results[name] = fn(ticker, price_history, FORECAST_HORIZON_DAYS)
        # M4: NaN / infinite / out-of-range guard
        score = results[name]["score"]
        if not np.isfinite(score) or not (0.0 <= float(score) <= 1.0):
            logger.warning(
                "%s %s: invalid score=%s — clamping to 0.0", ticker, name, score,
            )
            results[name]["score"] = 0.0
            results[name].setdefault("metadata", {})["score_clamped"] = True

    # STEP 2: extract raw veto inputs (default to worst case on missing data).
    # ``VETO_THRESHOLDS["*"]["garch_vol"]`` is on the *daily* vol scale
    # (e.g. sideways=0.035 ≈ 3.5% per day). The GARCH signal exposes both
    # ``daily_vol`` and ``annualized_vol`` — we feed daily into the veto.
    garch_meta = results["garch"]["metadata"]
    garch_vol = float(
        garch_meta.get(
            "daily_vol",
            (float(garch_meta["annualized_vol"]) / (252.0 ** 0.5))
            if "annualized_vol" in garch_meta
            else 999.0,
        )
    )
    mc_loss_prob = float(
        results["monte_carlo"]["metadata"].get("loss_probability", 1.0)
    )

    # STEP 3: apply veto BEFORE composite
    regime = regime_data["regime"]
    veto = apply_veto(garch_vol, mc_loss_prob, regime)

    # STEP 3b (U7): earnings-blackout guard — categorical, never relaxed.
    earnings_veto = False
    if EARNINGS_BLACKOUT_ENABLED:
        passed, reason = earnings_blackout(
            next_earnings, datetime.now(timezone.utc).date(), EARNINGS_BLACKOUT_DAYS
        )
        if not passed:
            earnings_veto = True
            veto["passed"] = False
            # Preserve any pre-existing vol/tail reason for the audit trail.
            veto["veto_reason"] = (
                reason if veto["veto_reason"] is None
                else f"{veto['veto_reason']}+{reason}"
            )

    # STEP 3c (U11): opt-in news-sentiment veto — categorical, default OFF.
    sentiment_veto_hit = False
    if SENTIMENT_VETO_ENABLED and cached_sentiment is not None:
        from screener.sentiment.scorer import sentiment_veto
        passed_s, reason_s = sentiment_veto(
            cached_sentiment.get("sentiment_score"), SENTIMENT_VETO_THRESHOLD
        )
        if not passed_s:
            sentiment_veto_hit = True
            veto["passed"] = False
            veto["veto_reason"] = (
                reason_s if veto["veto_reason"] is None
                else f"{veto['veto_reason']}+{reason_s}"
            )

    # STEP 4: compute composite ONLY if veto passed
    weights = dict(regime_data["blended_weights"])

    # H4: optionally exclude GARCH from the composite weights
    if GARCH_COMPOSITE_MODE == "veto_only":
        scoring_keys = EXPECTED_SIGNAL_KEYS - {"garch"}
        w_subset = {k: weights[k] for k in scoring_keys}
        w_total = sum(w_subset.values()) or 1.0
        eff_weights = {k: v / w_total for k, v in w_subset.items()}
    else:
        eff_weights = weights

    if not veto["passed"]:
        composite_score = 0.0
        contributions = {k: 0.0 for k in EXPECTED_SIGNAL_KEYS}
    else:
        contributions = {
            k: float(results[k]["score"]) * float(eff_weights.get(k, 0.0))
            for k in EXPECTED_SIGNAL_KEYS
        }
        composite_score = float(sum(contributions.values()))

    logger.debug(
        "%s: score=%.4f veto=%s",
        ticker, composite_score, veto["veto_reason"] or "PASS",
    )

    return {
        "ticker": ticker,
        "composite_score": round(composite_score, 6),
        "passed_veto": bool(veto["passed"]),
        "veto_reason": veto["veto_reason"],
        "earnings_veto": earnings_veto,
        "sentiment_veto": sentiment_veto_hit,
        "regime": regime,
        "regime_confidence": float(regime_data.get("confidence", 0.0)),
        "signal_scores": {k: float(results[k]["score"]) for k in EXPECTED_SIGNAL_KEYS},
        "signal_contributions": contributions,
        "veto_detail": veto,
        "metadata": {k: results[k].get("metadata", {}) for k in EXPECTED_SIGNAL_KEYS},
    }


__all__ = ["SIGNAL_FUNCTIONS", "score_stock"]
