"""ARIMA price-direction signal.

Universal interface (Phase 7):
    arima_signal(ticker, price_history, horizon) -> dict

Implementation notes:
  - H2: pmdarima import guard with statsmodels fallback (we ship statsmodels
    only; pmdarima is optional).
  - Fits on log(close) when ``ARIMA_USE_LOG_PRICES``.
  - ADF stationarity test selects the differencing order ``d``.
  - Score: sigmoid of (forecast_return / hist_vol) → bounded in (0, 1).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from screener.config import (
    ADF_PVALUE_THRESHOLD,
    ARIMA_ORDER,
    ARIMA_USE_AUTO,
    ARIMA_USE_LOG_PRICES,
    FORECAST_HORIZON_DAYS,
    MIN_HISTORY_ARIMA,
)

logger = logging.getLogger(__name__)

# H2: import guard for pmdarima (optional)
try:  # pragma: no cover
    from pmdarima import auto_arima  # type: ignore[import-untyped]
    HAS_PMDARIMA = True
except Exception:
    HAS_PMDARIMA = False
    auto_arima = None  # type: ignore[assignment]


def arima_signal(
    ticker: str,
    price_history: pd.DataFrame,
    horizon: int = FORECAST_HORIZON_DAYS,
) -> dict:
    if len(price_history) < MIN_HISTORY_ARIMA:
        return {
            "score": 0.0,
            "raw": None,
            "metadata": {
                "error": "insufficient_history",
                "rows": len(price_history),
                "required": MIN_HISTORY_ARIMA,
            },
        }
    try:
        from statsmodels.tsa.arima.model import ARIMA as SARIMA
        from statsmodels.tsa.stattools import adfuller

        close = price_history["Close"].values.astype(float)
        series = np.log(close) if ARIMA_USE_LOG_PRICES else close

        adf_pval = float(adfuller(series)[1])
        d = 1
        if adf_pval > ADF_PVALUE_THRESHOLD:
            adf_diff = float(adfuller(np.diff(series))[1])
            d = 1 if adf_diff < ADF_PVALUE_THRESHOLD else 2

        if ARIMA_USE_AUTO and HAS_PMDARIMA:
            model = auto_arima(  # type: ignore[misc]
                series, d=d, max_p=2, max_q=2,
                stepwise=True, error_action="ignore",
                suppress_warnings=True, n_jobs=1,
            )
            forecast = np.asarray(model.predict(n_periods=horizon))
        else:
            if ARIMA_USE_AUTO and not HAS_PMDARIMA:
                logger.debug("%s: pmdarima missing — statsmodels fallback", ticker)
            order = (ARIMA_ORDER[0], d, ARIMA_ORDER[2])
            res = SARIMA(series, order=order).fit()
            forecast = np.asarray(res.forecast(steps=horizon))

        forecast_price = float(np.exp(forecast[-1])) if ARIMA_USE_LOG_PRICES else float(forecast[-1])
        current_price = float(close[-1])
        forecast_return = (forecast_price - current_price) / max(current_price, 1e-6)
        hist_vol = float(np.std(np.diff(series)) * np.sqrt(252))
        score = float(1.0 / (1.0 + np.exp(-forecast_return / max(hist_vol, 1e-6))))

        return {
            "score": score,
            "raw": forecast,
            "metadata": {
                "forecast_return": float(forecast_return),
                "forecast_price": forecast_price,
                "hist_vol": hist_vol,
                "adf_pvalue": adf_pval,
                "d_used": d,
                "arima_order": (ARIMA_ORDER[0], d, ARIMA_ORDER[2]),
                "log_prices": bool(ARIMA_USE_LOG_PRICES),
                "engine": "pmdarima" if (ARIMA_USE_AUTO and HAS_PMDARIMA) else "statsmodels",
            },
        }
    except Exception as exc:
        logger.warning("%s ARIMA failed: %s", ticker, exc)
        return {"score": 0.0, "raw": None, "metadata": {"error": str(exc)}}


__all__ = ["arima_signal"]
