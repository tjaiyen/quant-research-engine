"""Tests for U15 k-means diversification clustering."""
from __future__ import annotations

import pandas as pd
import pytest

from screener.analysis import clustering
from render import notes


# ── pure cluster_features ────────────────────────────────────────────────────

def test_cluster_features_separates_blobs():
    # Three obviously-separated (vol, return) groups → 3 clean clusters.
    tickers, vols, rets = [], [], []
    blobs = [(0.10, 0.05), (0.40, 0.30), (0.25, -0.10)]
    for bi, (v, r) in enumerate(blobs):
        for j in range(5):
            tickers.append(f"T{bi}_{j}")
            vols.append(v + j * 0.001)
            rets.append(r + j * 0.001)
    res = clustering.cluster_features(tickers, vols, rets, k=3)
    assert res["k"] == 3
    assert len(res["clusters"]) == 3
    # Every member of a blob lands in the same cluster.
    label_of = dict(zip(tickers, res["_labels"]))
    for bi in range(3):
        labs = {label_of[f"T{bi}_{j}"] for j in range(5)}
        assert len(labs) == 1, f"blob {bi} split across clusters"
    # Members partition the universe exactly once.
    allm = [m for c in res["clusters"] for m in c["members"]]
    assert sorted(allm) == sorted(tickers)


def test_cluster_features_deterministic():
    tickers = [f"T{i}" for i in range(12)]
    vols = [0.1 + 0.02 * i for i in range(12)]
    rets = [0.05 - 0.01 * i for i in range(12)]
    a = clustering.cluster_features(tickers, vols, rets, k=3)["_labels"]
    b = clustering.cluster_features(tickers, vols, rets, k=3)["_labels"]
    assert a == b  # fixed random_state


# ── _ann_vol_return ──────────────────────────────────────────────────────────

def test_ann_vol_return_basic():
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    # Steadily rising series → positive return, low vol.
    close = [100.0 * (1.0005 ** i) for i in range(300)]
    df = pd.DataFrame({"adj_close": close}, index=idx)
    out = clustering._ann_vol_return(df)
    assert out is not None
    vol, ret = out
    assert ret > 0 and vol >= 0


def test_ann_vol_return_insufficient():
    df = pd.DataFrame({"adj_close": [100.0, 101.0, 102.0]})
    assert clustering._ann_vol_return(df) is None
    assert clustering._ann_vol_return(pd.DataFrame()) is None


# ── compute_clusters against a seeded temp DB ────────────────────────────────

def test_compute_clusters_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cockpit.sqlite"))
    from utils.db import init_db, upsert_ticker, upsert_prices
    init_db()

    # 10 tickers across 3 risk/return profiles; enough history each.
    idx = pd.date_range("2024-01-01", periods=300, freq="B")
    profiles = [(1.0003, 0), (1.0010, 1), (0.9997, 2)]
    seeded = []
    for i in range(10):
        drift, _ = profiles[i % 3]
        close = [100.0 * (drift ** j) for j in range(300)]
        t = f"SY{i}"
        upsert_ticker(t)
        upsert_prices(t, pd.DataFrame({
            "open": close, "high": close, "low": close, "close": close,
            "adj_close": close, "volume": [1] * 300}, index=idx))
        seeded.append(t)

    # Point the universe loader at our seeded tickers.
    monkeypatch.setattr(clustering, "load_universe", lambda: seeded, raising=False)
    import tasks.seed_universe as su
    monkeypatch.setattr(su, "load_universe", lambda: seeded)

    data = clustering.compute_clusters(k=3, lookback=252)
    assert data["k"] == 3
    assert data["n_tickers"] == 10
    members = [m for c in data["clusters"] for m in c["members"]]
    assert sorted(members) == sorted(seeded)
    assert data["silhouette"] is None or -1.0 <= data["silhouette"] <= 1.0


# ── note rendering ───────────────────────────────────────────────────────────

def test_clusters_note_renders():
    data = {"as_of": "2026-06-19T00:00:00+00:00", "k": 2, "silhouette": 0.41,
            "n_tickers": 6, "n_skipped": 1, "lookback": 252,
            "clusters": [
                {"id": 0, "n": 3, "mean_vol": 0.15, "mean_return": 0.08,
                 "label": "lower-risk / higher-return", "members": ["AAA", "BBB", "CCC"]},
                {"id": 1, "n": 3, "mean_vol": 0.40, "mean_return": -0.05,
                 "label": "higher-risk / lower-return", "members": ["DDD", "EEE", "FFF"]},
            ]}
    md = notes.clusters_note(data)
    assert "type: tracker-clusters" in md
    assert "Diversification clusters" in md
    assert "AAA" in md and "DDD" in md
    assert "lower-risk / higher-return" in md


def test_clusters_note_empty():
    md = notes.clusters_note({"k": 0, "clusters": [], "as_of": "x", "n_tickers": 0})
    assert "Not enough cached price history" in md
    assert "type: tracker-clusters" in md
