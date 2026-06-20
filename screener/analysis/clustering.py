"""K-means diversification clustering (Insight U15).

Groups the 220-stock universe by **realized** (annualized volatility, annualized
return) into risk/return cohorts — a different diversification axis than the
screener's per-sector top-N. Descriptive, not predictive: it's a lens to spot
when picks pile into one risk profile.

Data is read from the off-Drive prices cache (no network). `cluster_features` is
pure (testable without a DB); `compute_clusters` does the IO + k-selection.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

_MIN_ROWS = 60          # need a meaningful return/vol estimate
_K_RANGE = range(3, 7)  # candidate cluster counts for silhouette selection
_SEED = 42


def _ann_vol_return(prices_df, lookback: int = 252) -> tuple[float, float] | None:
    """Annualized (vol, return) from a prices DataFrame's adj_close, or None.

    Mirrors the formula in screener/signals/sharpe_signal.py (log returns;
    mean*252, std*sqrt(252)) over the trailing ``lookback`` window.
    """
    if prices_df is None or prices_df.empty or "adj_close" not in prices_df.columns:
        return None
    close = prices_df["adj_close"].dropna().to_numpy(dtype=float)
    if len(close) < _MIN_ROWS:
        return None
    log_ret = np.log(close[1:] / close[:-1])
    window = min(lookback, len(log_ret))
    recent = log_ret[-window:]
    ann_return = float(recent.mean() * 252.0)
    ann_vol = float(recent.std() * np.sqrt(252.0))
    return (ann_vol, ann_return)


def _label(vol: float, ret: float, vol_med: float, ret_med: float) -> str:
    """Quadrant label of a cluster centroid vs the universe medians."""
    v = "higher-risk" if vol >= vol_med else "lower-risk"
    r = "higher-return" if ret >= ret_med else "lower-return"
    return f"{v} / {r}"


def cluster_features(tickers: list[str], vols: list[float], rets: list[float],
                     k: int) -> dict:
    """Pure k-means over standardized (vol, return). Returns the clusters dict.

    Assumes len(tickers)==len(vols)==len(rets) and k <= n. No IO.
    """
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    X = np.column_stack([np.asarray(vols, float), np.asarray(rets, float)])
    Xs = StandardScaler().fit_transform(X)
    km = KMeans(n_clusters=k, n_init=10, random_state=_SEED)
    labels = km.fit_predict(Xs)

    vol_med = float(np.median(vols))
    ret_med = float(np.median(rets))

    clusters: list[dict] = []
    for cid in range(k):
        idx = [i for i, lab in enumerate(labels) if lab == cid]
        if not idx:
            continue
        cv = [vols[i] for i in idx]
        cr = [rets[i] for i in idx]
        mean_vol = float(np.mean(cv))
        mean_ret = float(np.mean(cr))
        members = sorted(tickers[i] for i in idx)
        clusters.append({
            "id": cid,
            "n": len(idx),
            "mean_vol": mean_vol,
            "mean_return": mean_ret,
            "label": _label(mean_vol, mean_ret, vol_med, ret_med),
            "members": members,
        })
    clusters.sort(key=lambda c: c["mean_vol"])  # low-risk first
    return {"k": k, "n_tickers": len(tickers), "clusters": clusters,
            "_labels": labels.tolist(), "_Xs": Xs}


def compute_clusters(k: int | None = None, lookback: int = 252) -> dict:
    """Load the universe from the cache, build (vol, return), cluster.

    If ``k`` is None, pick the best k in 3..6 by silhouette score. Best-effort:
    tickers without enough cached history are skipped (and counted).
    """
    from tasks.seed_universe import load_universe
    from utils.db import fetch_prices

    universe = load_universe()
    tickers, vols, rets = [], [], []
    skipped = 0
    for t in universe:
        try:
            feat = _ann_vol_return(fetch_prices(t), lookback)
        except Exception:
            feat = None
        if feat is None:
            skipped += 1
            continue
        tickers.append(t)
        vols.append(feat[0])
        rets.append(feat[1])

    base = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "lookback": lookback,
        "n_tickers": len(tickers),
        "n_skipped": skipped,
        "silhouette": None,
        "k": 0,
        "clusters": [],
    }
    if len(tickers) < max(_K_RANGE):  # not enough to cluster meaningfully
        return base

    if k is not None:
        result = cluster_features(tickers, vols, rets, min(k, len(tickers)))
        sil = _silhouette(result["_Xs"], result["_labels"])
    else:
        result, sil = _best_k(tickers, vols, rets)

    base.update({"k": result["k"], "silhouette": sil,
                 "clusters": result["clusters"]})
    return base


def _silhouette(Xs, labels) -> float | None:
    try:
        from sklearn.metrics import silhouette_score
        if len(set(labels)) < 2:
            return None
        return float(silhouette_score(Xs, labels))
    except Exception:
        return None


def _best_k(tickers, vols, rets) -> tuple[dict, float | None]:
    """Pick k in _K_RANGE maximizing silhouette."""
    best, best_sil = None, -1.0
    for k in _K_RANGE:
        if k >= len(tickers):
            break
        res = cluster_features(tickers, vols, rets, k)
        sil = _silhouette(res["_Xs"], res["_labels"])
        if sil is not None and sil > best_sil:
            best, best_sil = res, sil
    if best is None:  # silhouette never computable — fall back to k=3
        best = cluster_features(tickers, vols, rets, min(3, len(tickers)))
        return best, None
    return best, best_sil


__all__ = ["compute_clusters", "cluster_features"]
