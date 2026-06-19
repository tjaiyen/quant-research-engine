"""Compute per-position USD allocations from filtered signals.

Three modes (driven by ``POSITION_SIZING_MODE``):

  * ``equal``        — split deployable cash equally across N picks
  * ``score_weight`` — weight each position by ``composite_score``
  * ``score_vol``    — weight by ``score`` and inverse-vol (vol-parity);
                       clamped to ``[VOL_PARITY_FLOOR, VOL_PARITY_CEILING]``

Each mode respects:

  * ``MAX_SINGLE_STOCK_PCT`` — per-position cap (% of portfolio_value)
  * ``MAX_MONTHLY_DEPLOYMENT_PCT`` — total deployment cap (% of cash)
  * ``MIN_POSITION_VALUE_USD`` — drop allocations below this floor

The vol proxy for ``score_vol`` is ``signal_scores['garch']`` (the GARCH
score is ``1 - min(daily_vol/0.60, 1.0)``, so a higher score means lower
vol — we invert to get a vol-parity weight).
"""
from __future__ import annotations

import logging
from typing import Iterable

from auto_trader.config import (
    MAX_MONTHLY_DEPLOYMENT_PCT,
    MAX_SINGLE_STOCK_PCT,
    MIN_POSITION_VALUE_USD,
    POSITION_SIZING_MODE,
    VOL_PARITY_CEILING,
    VOL_PARITY_FLOOR,
)

logger = logging.getLogger(__name__)


def compute_allocations(
    eligible: Iterable[dict],
    portfolio_value: float,
    cash: float,
) -> list[dict]:
    """Return list of dicts ``{ticker, sector, composite_score, allocation_usd, ...}``.

    Args:
        eligible: filtered candidates from ``signal_filter.filter_signals``.
        portfolio_value: account.portfolio_value from broker.
        cash: account.cash from broker (the cash this cycle can deploy).

    The total spend is capped at ``MAX_MONTHLY_DEPLOYMENT_PCT * cash``.
    Returns empty list if no eligible candidates or no deployable cash.
    """
    candidates = [c for c in eligible if isinstance(c, dict) and "ticker" in c]
    if not candidates:
        logger.info("No eligible candidates — empty allocation")
        return []

    deployable_cash = max(0.0, float(cash) * MAX_MONTHLY_DEPLOYMENT_PCT)
    if deployable_cash <= 0:
        logger.info("No deployable cash — empty allocation")
        return []

    n = len(candidates)
    mode = POSITION_SIZING_MODE
    weights: list[float]

    if mode == "equal":
        weights = [1.0 / n] * n

    elif mode == "score_weight":
        scores = [max(0.0, float(c["composite_score"])) for c in candidates]
        total = sum(scores) or 1.0
        weights = [s / total for s in scores]

    elif mode == "score_vol":
        # GARCH score in screener is `1 - min(daily_vol/0.60, 1.0)`. Higher
        # garch score = lower vol. So a vol-parity weight is proportional
        # to garch_score (inverse-vol proxy), modulated by composite_score.
        per: list[float] = []
        for c in candidates:
            score = max(0.0, float(c["composite_score"]))
            ss = c.get("signal_scores", {}) or {}
            garch = float(ss.get("garch", 0.5))
            # vol_factor: 0..1 — clamped to [floor, ceiling] to avoid extremes
            vol_factor = max(VOL_PARITY_FLOOR, min(VOL_PARITY_CEILING, garch + 0.5))
            per.append(score * vol_factor)
        total = sum(per) or 1.0
        weights = [w / total for w in per]
    else:
        raise ValueError(f"Unknown POSITION_SIZING_MODE: {mode}")

    # Apply per-position cap
    per_cap = portfolio_value * MAX_SINGLE_STOCK_PCT
    raw_alloc = [
        min(per_cap, deployable_cash * w) for w in weights
    ]
    # Renormalize to the deployment budget after capping
    total_raw = sum(raw_alloc) or 1.0
    if total_raw > deployable_cash:
        raw_alloc = [a * deployable_cash / total_raw for a in raw_alloc]

    out: list[dict] = []
    for c, alloc in zip(candidates, raw_alloc):
        if alloc < MIN_POSITION_VALUE_USD:
            continue
        out.append(
            {
                "ticker": c["ticker"],
                "sector": c.get("sector", "UNKNOWN"),
                "composite_score": float(c["composite_score"]),
                "regime": c.get("regime", "unknown"),
                "signal_scores": c.get("signal_scores", {}),
                "allocation_usd": round(float(alloc), 2),
            }
        )

    logger.info(
        "Position sizer (%s): %d allocations totaling $%.2f (deployable=$%.2f, cap/pos=$%.2f)",
        mode, len(out), sum(a["allocation_usd"] for a in out),
        deployable_cash, per_cap,
    )
    return out


__all__ = ["compute_allocations"]
