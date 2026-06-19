"""Technical indicators over a daily price DataFrame.

Pure functions — take a price DataFrame (adj_close required), return
scalar signals or Series. Keep logic simple and auditable.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TechnicalSignals:
    sma_20: float | None
    sma_50: float | None
    sma_200: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    ret_1m: float | None
    ret_3m: float | None
    trend_regime: str          # 'bullish', 'neutral', 'bearish'
    momentum_regime: str       # 'strong', 'mild', 'weak'


def _sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window, min_periods=window).mean()


def _rsi(s: pd.Series, window: int = 14) -> pd.Series:
    """Classic Wilder RSI."""
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(
    s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series]:
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def _ret(s: pd.Series, days: int) -> float | None:
    if len(s) <= days:
        return None
    a, b = s.iloc[-1], s.iloc[-1 - days]
    if pd.isna(a) or pd.isna(b) or b == 0:
        return None
    return float(a / b - 1.0)


def _last(s: pd.Series) -> float | None:
    if s.empty or pd.isna(s.iloc[-1]):
        return None
    return float(s.iloc[-1])


def _trend_regime(price: float | None, sma50: float | None, sma200: float | None) -> str:
    if price is None or sma50 is None or sma200 is None:
        return "neutral"
    # Golden/Death cross logic + price position.
    if sma50 > sma200 and price > sma50:
        return "bullish"
    if sma50 < sma200 and price < sma50:
        return "bearish"
    return "neutral"


def _momentum_regime(r1m: float | None, r3m: float | None) -> str:
    # Rough buckets based on annualized-equivalent speed.
    score = 0.0
    if r1m is not None:
        score += r1m
    if r3m is not None:
        score += r3m / 3  # normalize to monthly-equivalent
    if score > 0.05:
        return "strong"
    if score < -0.05:
        return "weak"
    return "mild"


def compute_technical(df: pd.DataFrame) -> TechnicalSignals:
    """Return current technical snapshot. Assumes `adj_close` column."""
    if df.empty or "adj_close" not in df:
        return TechnicalSignals(None, None, None, None, None, None, None, None, "neutral", "mild")

    price = df["adj_close"]
    sma20, sma50, sma200 = _sma(price, 20), _sma(price, 50), _sma(price, 200)
    rsi = _rsi(price, 14)
    macd_line, signal_line = _macd(price)
    r1m = _ret(price, 21)
    r3m = _ret(price, 63)

    last_price = _last(price)
    last_sma50 = _last(sma50)
    last_sma200 = _last(sma200)

    return TechnicalSignals(
        sma_20=_last(sma20),
        sma_50=last_sma50,
        sma_200=last_sma200,
        rsi_14=_last(rsi),
        macd=_last(macd_line),
        macd_signal=_last(signal_line),
        ret_1m=r1m,
        ret_3m=r3m,
        trend_regime=_trend_regime(last_price, last_sma50, last_sma200),
        momentum_regime=_momentum_regime(r1m, r3m),
    )
