"""Per-signal Information Coefficient (IC).

Computes the Spearman rank correlation between each signal's score and
the realized 20-day forward return, evaluated cross-sectionally on each
sample date and then aggregated.

Output dimensions:
  - per signal (arima/kalman/garch/monte_carlo/sharpe)
  - per regime (bull/sideways/bear) — uses HMM labels at each sample date
  - "all" aggregate

A high IC (> 0.05) means the signal's ranking is informative about
which stocks will outperform; near-zero IC means it's noise.

Usage::

    python -m screener.backtest.signal_ic --samples 6
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from screener.backtest.walk_forward import _ph_for, _realized_return, _regime_at
from screener.config import (
    EXPECTED_SIGNAL_KEYS,
    FORECAST_HORIZON_DAYS,
    HMM_LOOKBACK_YEARS,
    OUTPUT_DIR,
    YFIN_MIN_ROWS_REQUIRED,
)
from screener.data.market_features import get_market_features
from screener.signals.arima_signal import arima_signal
from screener.signals.garch_signal import garch_signal
from screener.signals.kalman_signal import kalman_signal
from screener.signals.monte_carlo_signal import monte_carlo_signal
from screener.signals.sharpe_signal import sharpe_signal

logger = logging.getLogger(__name__)


_SIGNAL_FNS = {
    "arima": arima_signal,
    "kalman": kalman_signal,
    "garch": garch_signal,
    "monte_carlo": monte_carlo_signal,
    "sharpe": sharpe_signal,
}


def _load_universe() -> list[str]:
    from screener.config import HOLDINGS_PATH

    with open(HOLDINGS_PATH) as f:
        holdings = json.load(f)
    return sorted({t for k, v in holdings.items() if not k.startswith("_") for t in v})


def _spearman(a: list[float], b: list[float]) -> float:
    if len(a) < 5:
        return float("nan")
    s = pd.Series(a).rank(method="average")
    t = pd.Series(b).rank(method="average")
    if s.std() == 0 or t.std() == 0:
        return float("nan")
    return float(s.corr(t))


def compute_signal_ic(
    n_samples: int = 6,
    step_days: int = 30,
    horizon_days: int = FORECAST_HORIZON_DAYS,
) -> dict:
    """Compute per-signal IC across rolling sample dates.

    Returns a dict with keys ``per_sample`` (list per evaluation date),
    ``aggregate`` (mean IC by signal), and ``by_regime``.
    """
    features = get_market_features(lookback_years=HMM_LOOKBACK_YEARS, min_rows=300)
    universe = _load_universe()
    histories = {t: _ph_for(t) for t in universe}
    histories = {
        t: ph for t, ph in histories.items()
        if ph is not None and len(ph) >= YFIN_MIN_ROWS_REQUIRED + horizon_days
    }
    if not histories:
        raise RuntimeError("No price histories available — run cockpit refresh first")

    last_date = features.index[-1] - pd.Timedelta(days=horizon_days + 1)
    sample_dates: list[pd.Timestamp] = []
    for i in range(n_samples):
        candidate = last_date - pd.Timedelta(days=i * step_days)
        idx_pos = features.index.searchsorted(candidate)
        idx_pos = min(idx_pos, len(features.index) - 1)
        sample_dates.append(features.index[idx_pos])
    sample_dates.sort()

    per_sample: list[dict] = []
    by_regime_scores: dict[str, dict[str, list[float]]] = {}

    for w in sample_dates:
        try:
            regime_data = _regime_at(features, w)
        except Exception as exc:
            logger.warning("Skipping sample %s: %s", w.date(), exc)
            continue

        # Cross-sectional signal scores at this date
        cross: dict[str, list[float]] = {k: [] for k in EXPECTED_SIGNAL_KEYS}
        fwd_returns: list[float] = []
        for t, ph in histories.items():
            ph_train = ph.loc[:w]
            if len(ph_train) < YFIN_MIN_ROWS_REQUIRED:
                continue
            r = _realized_return(ph, w, horizon_days)
            if r is None:
                continue
            row_scores: dict[str, float] = {}
            ok = True
            for name, fn in _SIGNAL_FNS.items():
                out = fn(t, ph_train, horizon_days)
                if out["metadata"].get("error"):
                    ok = False
                    break
                row_scores[name] = float(out["score"])
            if not ok:
                continue
            for k, v in row_scores.items():
                cross[k].append(v)
            fwd_returns.append(r)

        ics: dict[str, float] = {}
        for k in EXPECTED_SIGNAL_KEYS:
            ics[k] = _spearman(cross[k], fwd_returns)

        regime = regime_data["regime"]
        by_regime = by_regime_scores.setdefault(regime, {k: [] for k in EXPECTED_SIGNAL_KEYS})
        for k, ic in ics.items():
            if not np.isnan(ic):
                by_regime[k].append(ic)

        per_sample.append(
            {
                "sample_date": str(w.date()),
                "regime": regime,
                "n_stocks": len(fwd_returns),
                "ic": ics,
            }
        )
        logger.info(
            "IC sample %s | regime=%s n=%d arima=%.3f kalman=%.3f garch=%.3f mc=%.3f sharpe=%.3f",
            w.date(), regime, len(fwd_returns),
            ics["arima"], ics["kalman"], ics["garch"], ics["monte_carlo"], ics["sharpe"],
        )

    aggregate: dict[str, float] = {}
    for k in EXPECTED_SIGNAL_KEYS:
        all_ics = [s["ic"][k] for s in per_sample if not np.isnan(s["ic"][k])]
        aggregate[k] = float(np.mean(all_ics)) if all_ics else float("nan")

    by_regime_summary = {
        regime: {k: (float(np.mean(v)) if v else float("nan")) for k, v in d.items()}
        for regime, d in by_regime_scores.items()
    }

    return {
        "n_samples": len(per_sample),
        "horizon_days": horizon_days,
        "per_sample": per_sample,
        "aggregate": aggregate,
        "by_regime": by_regime_summary,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-signal Information Coefficient")
    parser.add_argument("--samples", type=int, default=6)
    parser.add_argument("--step", type=int, default=30)
    parser.add_argument(
        "--out",
        type=str,
        default=str(Path(OUTPUT_DIR) / "signal_ic.json"),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")

    summary = compute_signal_ic(n_samples=args.samples, step_days=args.step)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info("Signal IC summary written: %s", args.out)
    print(json.dumps(summary["aggregate"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["compute_signal_ic"]
