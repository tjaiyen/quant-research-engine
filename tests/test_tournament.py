"""Tournament logic tests on a synthetic panel where `sharpe` predicts returns —
no live signals, no slow panel build.
"""
from __future__ import annotations

from render import notes
from screener.tournament.attribution import attribute
from screener.tournament.run import run_tournament
from screener.tournament.variants import SIGNALS, default_variants


def _panel():
    # Realistic shape: a per-segment market factor (so returns vary → real
    # Sharpes) plus a cross-sectional alpha where higher `sharpe` signal → higher
    # forward return. SPY earns the market factor; high-sharpe pickers add alpha.
    tickers = {"Tech": ["AAA", "BBB", "CCC", "DDD", "EEE"],
               "Health": ["FFF", "GGG", "HHH", "III", "JJJ"]}
    market = [-0.02, 0.03, 0.01, -0.01, 0.04, 0.00, 0.02, -0.03]
    segs, rows = [], []
    for k, mk in enumerate(market):
        d0 = f"2024-{k+1:02d}-01"
        d1 = f"2024-{k+2:02d}-01"
        segs.append({"d0": d0, "d1": d1, "regime": "sideways" if k % 2 else "bull",
                     "regime_conf": 1.0, "spy_return": mk})
        for sec, ts in tickers.items():
            for j, t in enumerate(ts):
                sharpe = (j + 1) / 5.0                       # 0.2 .. 1.0
                fwd = mk + (sharpe - 0.5) * 0.06             # market + sharpe alpha
                rows.append({
                    "d0": d0, "ticker": t, "sector": sec,
                    "signals": {s: (sharpe if s == "sharpe" else 0.5) for s in SIGNALS},
                    "composite": sharpe, "passed_veto": True, "fwd_return": fwd,
                })
    return {"key": {}, "rebalance": "month", "segments": segs, "rows": rows,
            "universe": [t for ts in tickers.values() for t in ts]}


def test_run_ranks_and_splits():
    tour = run_tournament(_panel(), default_variants())
    assert tour["n_segments"] == 8
    assert 0 < tour["n_in_sample"] < 8                 # OOS split happened
    labels = [r["label"] for r in tour["ranked"]]
    assert "SPY buy-hold" in labels                    # controls present
    assert len(tour["ranked"]) == len(default_variants())
    # a signal-following strategy (not a control) should win and beat SPY
    winner = tour["ranked"][0]
    assert winner["group"] != "control"
    spy = next(r for r in tour["results"] if r["spec"].get("control") == "spy")
    assert winner["full"]["total_return"] > spy["full"]["total_return"]


def test_inverse_underperforms_default():
    res = {r["label"]: r for r in run_tournament(_panel(), default_variants())["results"]}
    default = res["Regime-blended (default)"]["full"]["total_return"]
    inverse = res["Worst-ranked (inverse)"]["full"]["total_return"]
    assert default > inverse                            # ranking has real signal


def test_attribution_detects_predictive_signal():
    panel = _panel()
    attr = attribute(run_tournament(panel, default_variants()), panel)
    assert attr["signal_ic"]["sharpe"] is not None and attr["signal_ic"]["sharpe"] > 0.5
    assert attr["ranking_has_signal"] is True
    assert attr["winner"] and isinstance(attr["verdict"], str)
    assert attr["sector_tilt"] and attr["regime_conditional"]


def test_note_renders_and_empty_is_graceful():
    panel = _panel()
    tour = run_tournament(panel, default_variants())
    md = notes.tournament_note({
        "as_of": "x", "n_segments": tour["n_segments"],
        "n_in_sample": tour["n_in_sample"], "ranked": tour["ranked"],
        "attribution": attribute(tour, panel),
    })
    assert "Strategy tournament" in md and "Leaderboard" in md
    assert "type: tracker-tournament" in md
    assert "no tournament run" in notes.tournament_note({"ranked": []}).lower()


def test_empty_panel_graceful():
    tour = run_tournament({"segments": [], "rows": [], "rebalance": "quarter"},
                          default_variants())
    assert tour["ranked"]   # variants still listed, just zero metrics
    attr = attribute(tour, {"segments": [], "rows": []})
    assert isinstance(attr.get("verdict"), str)
