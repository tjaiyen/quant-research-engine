"""Walk-forward cross-validation for the screener.

Spec sketch (doc Phase 1): "Time-series cross-validation".

This implementation walks ``train_end`` forward in 30-day steps. At each
step it trains the regime HMM on the prior ``HMM_LOOKBACK_YEARS`` of SPY
features, scores the universe as it would have at that date, then
evaluates the screener's per-sector top-5 picks against actual realized
returns ``FORECAST_HORIZON_DAYS`` (~20 trading days) into the future.

Outputs a per-window summary plus a roll-up:
  - precision@5 (lift over equal-weight sector return)
  - hit rate by regime
  - veto rate

The walk-forward is intentionally lightweight (no parallelism, default
4 windows × 11 sectors × 20 stocks ≈ 880 scoring calls) so it finishes in
a couple of minutes on a single core. Use ``--windows`` to widen.

Smart-reuse: per-stock prices come from cockpit's ``prices`` table.
yfinance is only used for VIX (HMM feature).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from screener.config import (
    FORECAST_HORIZON_DAYS,
    HMM_FEATURES,
    HMM_LOOKBACK_YEARS,
    HMM_N_ITER,
    HMM_N_STATES,
    OUTPUT_DIR,
    SECTOR_ETFS,
    STOCKS_PER_SECTOR,
    TOP_N_OUTPUT,
    YFIN_MIN_ROWS_REQUIRED,
)
from screener.data.market_features import get_market_features
from screener.engine.composite_scorer import score_stock
from screener.regime.weight_matrix import get_blended_weights

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowResult:
    train_end: str          # ISO date
    eval_end: str           # train_end + horizon
    regime: str
    confidence: float
    picks: list[str]
    pick_realized_avg: float
    universe_realized_avg: float
    lift: float
    veto_rate: float


def _train_hmm_on(features_df: pd.DataFrame) -> tuple:
    """Lightweight HMM trainer that does NOT persist the model."""
    from hmmlearn.hmm import GaussianHMM
    from sklearn.preprocessing import StandardScaler

    X = features_df[HMM_FEATURES].values
    scaler = StandardScaler().fit(X)
    model = GaussianHMM(
        n_components=HMM_N_STATES,
        covariance_type="full",
        n_iter=HMM_N_ITER,
        tol=1e-4,
    )
    model.fit(scaler.transform(X))
    pred = model.predict(scaler.transform(X))

    rows = []
    for s in range(HMM_N_STATES):
        m = pred == s
        rows.append(
            {
                "state": s,
                "mean_return": float(features_df.loc[m, "log_return"].mean()),
                "mean_vol": float(features_df.loc[m, "realized_vol_20d"].mean()),
            }
        )
    df = pd.DataFrame(rows)
    df["return_rank"] = df["mean_return"].rank(ascending=False)
    df["vol_rank"] = df["mean_vol"].rank(ascending=True)
    df["composite"] = df["return_rank"] + df["vol_rank"]
    df = df.sort_values("composite").reset_index(drop=True)
    regime_map = {
        int(df.iloc[0]["state"]): "bull",
        int(df.iloc[1]["state"]): "sideways",
        int(df.iloc[2]["state"]): "bear",
    }
    return model, scaler, regime_map


def _regime_at(features_df: pd.DataFrame, train_end: pd.Timestamp) -> dict:
    """Train an HMM on data up to train_end and classify that final row."""
    train = features_df.loc[:train_end]
    if len(train) < 250:
        raise RuntimeError(f"insufficient features at {train_end.date()}: {len(train)} rows")
    model, scaler, regime_map = _train_hmm_on(train)
    last = train[HMM_FEATURES].values[-1:, :]
    raw = model.predict_proba(scaler.transform(last))[0]
    probs: dict[str, float] = {}
    for s in range(len(raw)):
        if s in regime_map:
            label = regime_map[s]
            probs[label] = probs.get(label, 0.0) + float(raw[s])
    label = max(probs, key=lambda k: probs[k])
    return {
        "regime": label,
        "confidence": float(probs[label]),
        "stable": False,
        "probabilities": probs,
        "blended_weights": get_blended_weights(probs),
    }


def _slice_history_to(df: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    """Cockpit prices DF (Title-cased) truncated to [..., end]."""
    return df.loc[:end]


def _load_universe() -> dict[str, list[str]]:
    """Load holdings.json sectors (skip _meta)."""
    from screener.config import HOLDINGS_PATH

    with open(HOLDINGS_PATH) as f:
        holdings = json.load(f)
    return {k: v for k, v in holdings.items() if not k.startswith("_")}


def _ph_for(ticker: str) -> pd.DataFrame | None:
    try:
        from utils.db import fetch_prices
    except Exception:
        return None
    df = fetch_prices(ticker)
    if df is None or df.empty:
        return None
    if not {"open", "high", "low", "adj_close", "volume"}.issubset(df.columns):
        return None
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


def _realized_return(ph: pd.DataFrame, train_end: pd.Timestamp,
                     horizon_days: int) -> float | None:
    """Forward log-return from the last row ≤ train_end to horizon_days later."""
    sliced = ph.loc[:train_end]
    if sliced.empty:
        return None
    fwd = ph.loc[train_end:].head(horizon_days + 1)
    if len(fwd) <= 1:
        return None
    p0 = float(sliced["Close"].iloc[-1])
    p1 = float(fwd["Close"].iloc[-1])
    if p0 <= 0 or p1 <= 0:
        return None
    return float(np.log(p1 / p0))


def run_walk_forward(
    n_windows: int = 4,
    step_days: int = 30,
    horizon_days: int = FORECAST_HORIZON_DAYS,
    max_per_sector: int | None = None,
) -> list[WindowResult]:
    """Run the walk-forward back-test.

    Args:
        n_windows: number of historical evaluation windows.
        step_days: calendar-day step between windows (going backwards).
        horizon_days: forward window for realized-return evaluation.
        max_per_sector: cap each sector's pool for speed (keeps per-sector
            top-N ranking valid, just from a smaller candidate set).
    """
    features_full = get_market_features(lookback_years=HMM_LOOKBACK_YEARS, min_rows=300)
    sectors = _load_universe()
    if max_per_sector:
        sectors = {s: t[:max_per_sector] for s, t in sectors.items()}
    holdings_flat = {t for tickers in sectors.values() for t in tickers}
    histories: dict[str, pd.DataFrame] = {}
    for t in holdings_flat:
        ph = _ph_for(t)
        if ph is not None and len(ph) >= YFIN_MIN_ROWS_REQUIRED + horizon_days:
            histories[t] = ph

    if not histories:
        raise RuntimeError("No price histories available — run cockpit refresh first")

    last_date = features_full.index[-1] - pd.Timedelta(days=horizon_days + 1)
    windows: list[pd.Timestamp] = []
    for i in range(n_windows):
        candidate = last_date - pd.Timedelta(days=i * step_days)
        # Snap to a date present in the features index
        idx_pos = features_full.index.searchsorted(candidate)
        idx_pos = min(idx_pos, len(features_full.index) - 1)
        windows.append(features_full.index[idx_pos])
    windows.sort()  # chronological

    results: list[WindowResult] = []
    for w in windows:
        try:
            regime_data = _regime_at(features_full, w)
        except Exception as exc:
            logger.warning("Skipping window %s: %s", w.date(), exc)
            continue

        # Score every sector at this train_end
        per_sector_picks: list[tuple[str, list[str], float, float]] = []
        veto_total = 0
        screened_total = 0
        for sector, tickers in sectors.items():
            scored: list[dict] = []
            for t in tickers:
                ph = histories.get(t)
                if ph is None:
                    continue
                ph_train = _slice_history_to(ph, w)
                if len(ph_train) < YFIN_MIN_ROWS_REQUIRED:
                    continue
                screened_total += 1
                try:
                    res = score_stock(t, regime_data, ph_train)
                except Exception:
                    continue
                if not res["passed_veto"]:
                    veto_total += 1
                else:
                    scored.append(res)
            scored.sort(key=lambda x: x["composite_score"], reverse=True)
            picks = [s["ticker"] for s in scored[:TOP_N_OUTPUT]]

            # Realized returns of picks vs sector universe
            pick_returns = [
                r for r in (
                    _realized_return(histories[t], w, horizon_days) for t in picks
                ) if r is not None
            ]
            universe_returns = [
                r for r in (
                    _realized_return(histories[t], w, horizon_days)
                    for t in tickers if t in histories
                ) if r is not None
            ]
            pick_avg = float(np.mean(pick_returns)) if pick_returns else 0.0
            uni_avg = float(np.mean(universe_returns)) if universe_returns else 0.0
            per_sector_picks.append((sector, picks, pick_avg, uni_avg))

        # Aggregate across sectors for this window
        if per_sector_picks:
            mean_pick = float(np.mean([x[2] for x in per_sector_picks]))
            mean_uni = float(np.mean([x[3] for x in per_sector_picks]))
        else:
            mean_pick = mean_uni = 0.0
        veto_rate = veto_total / max(screened_total, 1)
        lift = mean_pick - mean_uni

        results.append(
            WindowResult(
                train_end=str(w.date()),
                eval_end=str(
                    (w + pd.Timedelta(days=horizon_days)).date()
                ),  # label matches the realized-return window (_realized_return uses horizon_days)
                regime=regime_data["regime"],
                confidence=float(regime_data["confidence"]),
                picks=[t for _, picks, *_ in per_sector_picks for t in picks],
                pick_realized_avg=mean_pick,
                universe_realized_avg=mean_uni,
                lift=lift,
                veto_rate=veto_rate,
            )
        )
        logger.info(
            "WF window %s | regime=%s lift=%+.4f (picks=%+.4f vs universe=%+.4f) "
            "veto=%.1f%%",
            w.date(), regime_data["regime"], lift, mean_pick, mean_uni,
            veto_rate * 100,
        )

    return results


def _summarize(results: list[WindowResult]) -> dict:
    if not results:
        return {"n_windows": 0, "mean_lift": 0.0, "by_regime": {}}
    by_regime: dict[str, list[float]] = {}
    for r in results:
        by_regime.setdefault(r.regime, []).append(r.lift)
    return {
        "n_windows": len(results),
        "mean_lift": float(np.mean([r.lift for r in results])),
        "win_rate": float(np.mean([1.0 if r.lift > 0 else 0.0 for r in results])),
        "mean_veto_rate": float(np.mean([r.veto_rate for r in results])),
        "by_regime": {
            label: {
                "n": len(values),
                "mean_lift": float(np.mean(values)),
                "win_rate": float(np.mean([1.0 if v > 0 else 0.0 for v in values])),
            }
            for label, values in by_regime.items()
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Walk-forward back-test for the screener.")
    parser.add_argument("--windows", type=int, default=4, help="Number of evaluation windows (default 4).")
    parser.add_argument("--step", type=int, default=30, help="Calendar-day step between windows (default 30).")
    parser.add_argument(
        "--out",
        type=str,
        default=str(Path(OUTPUT_DIR) / "walk_forward.json"),
        help="Path to write JSON summary.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")

    results = run_walk_forward(n_windows=args.windows, step_days=args.step)
    summary = _summarize(results)
    blob = {
        "summary": summary,
        "windows": [asdict(r) for r in results],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(blob, f, indent=2, default=str)
    logger.info("Walk-forward summary written: %s", args.out)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["WindowResult", "run_walk_forward"]
