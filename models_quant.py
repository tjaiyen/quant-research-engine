"""Risk metrics. v1 = realized volatility + max drawdown.

Future additions (per section 4.1): CAPM/beta, Sharpe/Sortino, VaR/CVaR,
Monte Carlo. Keep each metric a pure function for easy testing.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class RiskSignals:
    vol_30d_ann: float | None   # annualized realized vol from 30d returns
    vol_90d_ann: float | None
    max_drawdown_1y: float | None  # negative number, e.g. -0.18 = -18%
    risk_regime: str            # 'low', 'moderate', 'elevated', 'high'


def _log_returns(price: pd.Series) -> pd.Series:
    return np.log(price / price.shift(1)).dropna()


def realized_vol(price: pd.Series, window: int = 30) -> float | None:
    """Annualized realized vol over the last `window` daily returns."""
    rets = _log_returns(price).tail(window)
    if len(rets) < max(5, window // 3):
        return None
    return float(rets.std(ddof=1) * np.sqrt(TRADING_DAYS))


def max_drawdown(price: pd.Series, window_days: int = TRADING_DAYS) -> float | None:
    """Worst peak-to-trough drawdown over the last `window_days`."""
    s = price.tail(window_days).dropna()
    if len(s) < 20:
        return None
    peak = s.cummax()
    dd = s / peak - 1.0
    return float(dd.min())


def _risk_regime(vol30: float | None, dd1y: float | None) -> str:
    """Bucket overall risk. Equity-benchmark-ish thresholds."""
    if vol30 is None:
        return "moderate"
    if vol30 < 0.15 and (dd1y is None or dd1y > -0.10):
        return "low"
    if vol30 < 0.25 and (dd1y is None or dd1y > -0.20):
        return "moderate"
    if vol30 < 0.40:
        return "elevated"
    return "high"


def compute_risk(df: pd.DataFrame) -> RiskSignals:
    if df.empty or "adj_close" not in df:
        return RiskSignals(None, None, None, "moderate")
    price = df["adj_close"]
    v30 = realized_vol(price, 30)
    v90 = realized_vol(price, 90)
    dd1 = max_drawdown(price, TRADING_DAYS)
    return RiskSignals(
        vol_30d_ann=v30,
        vol_90d_ann=v90,
        max_drawdown_1y=dd1,
        risk_regime=_risk_regime(v30, dd1),
    )
