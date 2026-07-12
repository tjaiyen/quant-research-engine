"""Curated candidate factors (U33) — CANDIDATES only, never in the composite.

Twelve US-daily-OHLCV factors selected from the well-known families in the
HKUDS/Vibe-Trading alpha zoos (alpha101/academic, MIT) — re-implemented as
pure causal functions on the cached price frame (same shape as
``momentum_signal``: trailing windows ending at the last bar of the
already-sliced history; no fundamentals, no look-ahead).

They enter the tournament panel ONLY behind ``CANDIDATE_FACTORS_ENABLED``
(default off) as measured columns for the signal-lab IC/Bonferroni/lifecycle
machinery to judge. Promotion into WEIGHT_MATRIX stays the manual, DSR/CPCV-
gated path. NOTE: enabling widens the Bonferroni family, which raises the
significance bar for the existing 5 signals too — deliberate, documented in
SignalLab.md.

Scores are sigmoid-squashed to (0,1); the signal-lab IC uses cross-sectional
rank, so squashes are monotonic-only by design (same convention as momentum).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_ROWS = 273          # 252d window + 21d buffer — factors below need ≤ this


def _sig(x: float, scale: float) -> float:
    """Monotonic sigmoid squash to (0,1)."""
    return float(1.0 / (1.0 + np.exp(-x / scale)))


def _rets(close: np.ndarray) -> np.ndarray:
    return np.diff(close) / close[:-1]


# Each factor: (df → raw float) on the CAUSAL trailing window. Positive raw
# is squashed toward 1 (the "good" side per the anomaly's usual sign).

def _st_reversal(df: pd.DataFrame) -> float:
    """Short-term reversal: last ~21d losers outperform (Jegadeesh 1990)."""
    c = df["Close"].values
    return -(c[-1] / c[-22] - 1.0)


def _volume_price_corr(df: pd.DataFrame) -> float:
    """Price-volume divergence (alpha101 family): rally on falling volume
    fades; corr(close, volume) over 10d, negated."""
    c, v = df["Close"].values[-10:], df["Volume"].values[-10:].astype(float)
    if np.std(c) < 1e-12 or np.std(v) < 1e-12:
        return 0.0
    return -float(np.corrcoef(c, v)[0, 1])


def _range_vol(df: pd.DataFrame) -> float:
    """Low intraday-range volatility (Parkinson-style), 21d, inverted."""
    h, l = df["High"].values[-21:], df["Low"].values[-21:]
    with np.errstate(divide="ignore", invalid="ignore"):
        rng = np.log(h / l)
    rng = rng[np.isfinite(rng)]
    return -float(np.sqrt(np.mean(rng ** 2))) if rng.size else 0.0


def _amihud_illiq(df: pd.DataFrame) -> float:
    """Amihud (2002) illiquidity premium: mean |ret| / dollar volume, 21d,
    log-scaled (raw spans orders of magnitude)."""
    c = df["Close"].values[-22:]
    v = df["Volume"].values[-21:].astype(float)
    r = np.abs(_rets(c))
    dollar = c[1:] * v
    ok = dollar > 0
    if not ok.any():
        return 0.0
    return float(np.log1p(np.mean(r[ok] / dollar[ok]) * 1e9))


def _overnight_gap(df: pd.DataFrame) -> float:
    """Mean overnight return (open vs prior close), 21d — overnight drift
    persistence (Lou, Polk, Skouras 2019)."""
    o, c = df["Open"].values[-21:], df["Close"].values[-22:-1]
    ok = c > 0
    return float(np.mean(o[ok] / c[ok] - 1.0)) if ok.any() else 0.0


def _high_52w(df: pd.DataFrame) -> float:
    """Proximity to the 252d high (George & Hwang 2004) — anchoring
    under-reaction near the high. Raw in (0,1] already; centred at 0.5."""
    c = df["Close"].values[-252:]
    hi = float(np.max(c))
    return (c[-1] / hi - 0.5) if hi > 0 else 0.0


def _mean_rev_20(df: pd.DataFrame) -> float:
    """Bollinger-style 20d mean reversion: z-score of price vs 20d mean,
    negated (stretched-below → expected bounce)."""
    c = df["Close"].values[-20:]
    sd = float(np.std(c, ddof=1))
    return -((c[-1] - float(np.mean(c))) / sd) if sd > 1e-12 else 0.0


def _volume_trend(df: pd.DataFrame) -> float:
    """Rising participation: 21d avg volume vs 63d avg volume, log-ratio."""
    v = df["Volume"].values.astype(float)
    short, long = np.mean(v[-21:]), np.mean(v[-63:])
    return float(np.log(short / long)) if short > 0 and long > 0 else 0.0


def _close_position(df: pd.DataFrame) -> float:
    """Mean intraday close position (close−low)/(high−low), 10d — buying
    pressure (alpha101 family). Centred at 0.5."""
    h, l, c = (df[k].values[-10:] for k in ("High", "Low", "Close"))
    rng = h - l
    ok = rng > 1e-12
    return float(np.mean((c[ok] - l[ok]) / rng[ok]) - 0.5) if ok.any() else 0.0


def _turnover_stability(df: pd.DataFrame) -> float:
    """Stable volume (low 21d volume coefficient-of-variation), negated CV."""
    v = df["Volume"].values[-21:].astype(float)
    m = float(np.mean(v))
    return -(float(np.std(v, ddof=1)) / m) if m > 0 else 0.0


def _max_ret_21(df: pd.DataFrame) -> float:
    """Lottery-stock MAX effect (Bali, Cakici, Whitelaw 2011): max daily
    return over 21d, negated — extreme-day chasers underperform."""
    r = _rets(df["Close"].values[-22:])
    return -float(np.max(r)) if r.size else 0.0


def _idio_vol(df: pd.DataFrame) -> float:
    """Low-volatility anomaly proxy: 63d daily return std, negated (raw
    total vol — not market-residualised; that would need a joined index)."""
    r = _rets(df["Close"].values[-64:])
    return -float(np.std(r, ddof=1)) if r.size > 2 else 0.0


# name → (fn, min_rows, sigmoid scale)
FACTORS: dict[str, tuple] = {
    "st_reversal":     (_st_reversal, 23, 0.08),
    "vol_price_corr":  (_volume_price_corr, 10, 0.5),
    "range_vol":       (_range_vol, 21, 0.02),
    "amihud_illiq":    (_amihud_illiq, 23, 2.0),
    "overnight_gap":   (_overnight_gap, 23, 0.005),
    "high_52w":        (_high_52w, 252, 0.2),
    "mean_rev_20":     (_mean_rev_20, 20, 1.0),
    "volume_trend":    (_volume_trend, 63, 0.4),
    "close_position":  (_close_position, 10, 0.15),
    "turnover_stab":   (_turnover_stability, 21, 0.5),
    "max_ret_21":      (_max_ret_21, 23, 0.03),
    "idio_vol":        (_idio_vol, 64, 0.01),
}


def candidate_factor_scores(ticker: str, price_history: pd.DataFrame) -> dict:
    """All computable candidate-factor scores for one ticker, (0,1)-squashed.

    Factors whose window exceeds the available history are omitted (the
    signal-lab drops missing values per date); any per-factor failure is
    logged and skipped — a candidate must never sink a panel row.
    """
    out: dict[str, float] = {}
    n = len(price_history)
    for name, (fn, min_rows, scale) in FACTORS.items():
        if n < min_rows:
            continue
        try:
            out[name] = _sig(fn(price_history), scale)
        except Exception as exc:
            logger.debug("%s candidate factor %s failed: %s", ticker, name, exc)
    return out


__all__ = ["candidate_factor_scores", "FACTORS", "MIN_ROWS"]
