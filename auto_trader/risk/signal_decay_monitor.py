"""Re-score active positions and detect signal decay.

Called from ``daily_run.py``. For each non-CLOSED position:

  * Pull ~6 months of price history (cockpit prices first, yfinance fallback)
  * Recompute the screener composite via ``screener.engine.composite_scorer.score_stock``
  * Update ``positions.last_score`` and append a row to ``signal_history``
  * Emit alerts:
      - ``EXIT_SIGNAL`` if score < ``SIGNAL_EXIT_THRESHOLD``
      - ``SCORE_DECAY`` if (new - old) ≤ ``SCORE_DECAY_WARN_DELTA`` (-0.20)

Only the most volatile / lowest-scoring ``RESCORE_MAX_POSITIONS`` are
rescored to keep the daily run fast.
"""
from __future__ import annotations

import logging

from auto_trader.config import (
    RESCORE_AT_RISK_THRESHOLD,
    RESCORE_DATA_PERIOD,
    RESCORE_MAX_POSITIONS,
    SCORE_DECAY_WARN_DELTA,
    SIGNAL_EXIT_THRESHOLD,
)
from auto_trader.state.portfolio_db import (
    get_all_positions,
    log_signal_snapshot,
    upsert_position,
)
from auto_trader.utils import now_iso, today_iso, yf_retry

logger = logging.getLogger(__name__)


@yf_retry(max_attempts=3)
def _fetch_history(ticker: str, period: str = RESCORE_DATA_PERIOD):
    """Fetch OHLCV history via cockpit cache → yfinance fallback."""
    # Smart-reuse: cockpit's prices table first
    try:
        from utils.db import fetch_prices

        df = fetch_prices(ticker)
        if df is not None and not df.empty and {"open", "high", "low", "adj_close", "volume"}.issubset(df.columns):
            import pandas as pd

            return pd.DataFrame(
                {
                    "Open": df["open"],
                    "High": df["high"],
                    "Low": df["low"],
                    "Close": df["adj_close"],
                    "Volume": df["volume"],
                },
                index=df.index,
            ).dropna(how="all")
    except Exception as exc:
        logger.debug("cockpit fetch failed for %s (%s); using yfinance", ticker, exc)

    import yfinance as yf

    raw = yf.download(ticker, period=period, auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, type(raw.columns)) and hasattr(raw.columns, "levels"):
        try:
            raw = raw.xs(ticker, level=1, axis=1)
        except KeyError:
            try:
                raw = raw.xs(ticker, level=0, axis=1)
            except KeyError:
                pass
    raw.columns = [
        c.capitalize() if str(c).lower() in {"open", "high", "low", "close", "volume"} else c
        for c in raw.columns
    ]
    return raw.dropna(how="all")


def rescore_positions(regime_data: dict) -> list[dict]:
    """Rescore the at-risk subset of active positions; return alert dicts.

    Returns a list of ``{type, ticker, score, delta?}`` alerts to be
    forwarded to the alert engine.
    """
    positions = get_all_positions()
    # Prioritize the riskiest first (lowest current score)
    positions.sort(key=lambda p: float(p.get("last_score") or 1.0))
    targets = positions[:RESCORE_MAX_POSITIONS]

    if not targets:
        return []

    try:
        from screener.engine.composite_scorer import score_stock
    except Exception as exc:
        logger.warning("Cannot import screener composite_scorer: %s", exc)
        return []

    alerts: list[dict] = []
    for pos in targets:
        ticker = pos["ticker"]
        old_score = float(pos.get("last_score") or 0.0)
        try:
            ph = _fetch_history(ticker)
            if ph is None or len(ph) < 60:
                logger.debug("%s: insufficient history for rescore", ticker)
                continue
            result = score_stock(ticker, regime_data, ph)
            new_score = float(result["composite_score"])
        except Exception as exc:
            logger.warning("%s: rescore failed (%s)", ticker, exc)
            continue

        delta = new_score - old_score

        # Persist the new score on the position + signal_history
        upsert_position({**pos, "last_score": new_score, "last_scored_at": now_iso()})
        sig = result.get("signal_scores", {}) or {}
        log_signal_snapshot(
            {
                "ticker": ticker,
                "snapshot_date": today_iso(),
                "composite_score": new_score,
                "arima_score": sig.get("arima"),
                "kalman_score": sig.get("kalman"),
                "garch_score": sig.get("garch"),
                "mc_score": sig.get("monte_carlo"),
                "sharpe_score": sig.get("sharpe"),
                "regime": regime_data.get("regime"),
                "regime_confidence": float(regime_data.get("confidence", 0.0)),
            }
        )

        # Generate alerts
        if new_score < SIGNAL_EXIT_THRESHOLD:
            alerts.append(
                {
                    "type": "EXIT_SIGNAL",
                    "ticker": ticker,
                    "score": new_score,
                    "delta": delta,
                }
            )
        elif delta <= SCORE_DECAY_WARN_DELTA:
            alerts.append(
                {
                    "type": "SCORE_DECAY",
                    "ticker": ticker,
                    "score": new_score,
                    "delta": delta,
                }
            )

    if alerts:
        logger.info("Signal rescore: %d alerts (of %d rescored)", len(alerts), len(targets))
    return alerts


__all__ = ["rescore_positions"]
