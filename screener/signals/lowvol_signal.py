"""Low-volatility anomaly — boring stocks outperform their risk (Tier-0 candidate).

Universal interface (Phase 7):
    lowvol_signal(ticker, price_history, horizon) -> dict

The low-vol/low-beta anomaly (Haugen & Baker 1991; Frazzini & Pedersen
"Betting Against Beta" 2014): the least-volatile names have historically
delivered better risk-adjusted — and often better raw — returns than the
most-volatile ones. Price-only and fully causal (uses only bars up to the
as-of date), so it slots into the backtest panel with no look-ahead — unlike
value/quality, which need point-in-time fundamentals (Tier 1) to test honestly.

Raw = annualized daily-log-return volatility over the trailing ~252 bars
(same formula as sharpe_signal). Score = 1/(1+vol): monotone DECREASING in
vol, in (0,1] — calm names score high. The signal-lab IC test uses the
cross-sectional rank, so the map being monotonic is all that matters.

MEASURED-ONLY like momentum: not in EXPECTED_SIGNAL_KEYS / WEIGHT_MATRIX;
promotion into the live composite is a separate step gated on its IC.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from screener.config import FORECAST_HORIZON_DAYS

logger = logging.getLogger(__name__)

MIN_HISTORY_LOWVOL = 64          # ~3 months of bars for a usable vol estimate
LOWVOL_WINDOW_DAYS = 252


def lowvol_signal(
    ticker: str,
    price_history: pd.DataFrame,
    horizon: int = FORECAST_HORIZON_DAYS,  # noqa: ARG001 - interface parity
) -> dict:
    if len(price_history) < MIN_HISTORY_LOWVOL:
        return {
            "score": 0.0,
            "raw": None,
            "metadata": {
                "error": "insufficient_history",
                "rows": len(price_history),
                "required": MIN_HISTORY_LOWVOL,
            },
        }
    try:
        close = price_history["Close"].values.astype(float)
        log_ret = np.log(close[1:] / close[:-1])
        window = min(LOWVOL_WINDOW_DAYS, len(log_ret))
        ann_vol = float(log_ret[-window:].std() * np.sqrt(252.0))
        score = float(1.0 / (1.0 + max(ann_vol, 0.0)))
        return {
            "score": score,
            "raw": ann_vol,
            "metadata": {"window": window, "ann_vol": ann_vol},
        }
    except Exception as exc:  # noqa: BLE001 — a bad ticker never kills a scan
        logger.debug("lowvol %s failed: %s", ticker, exc)
        return {"score": 0.0, "raw": None, "metadata": {"error": str(exc)}}


__all__ = ["lowvol_signal"]
