"""Signal-lab tests on a synthetic panel where `arima` predicts and
`monte_carlo` predicts backwards — plus the WEIGHT_MATRIX_MODE flag.
"""
from __future__ import annotations

from render import notes
from screener.signal_lab.lab import analyze_signals, recommend_weights


def _panel():
    tickers = {"Tech": ["A", "B", "C", "D", "E"], "Health": ["F", "G", "H", "I", "J"]}
    market = [-0.02, 0.03, 0.01, -0.01, 0.04, 0.00, 0.02, -0.03]
    segs, rows = [], []
    for k, mk in enumerate(market):
        d0 = f"2024-{k+1:02d}-01"
        segs.append({"d0": d0, "d1": f"2024-{k+2:02d}-01",
                     "regime": "bull" if k % 2 else "bear", "regime_conf": 1.0,
                     "spy_return": mk})
        for sec, ts in tickers.items():
            for j, t in enumerate(ts):
                a = (j + 1) / 5.0                      # arima predicts forward return
                fwd = mk + (a - 0.5) * 0.06
                rows.append({
                    "d0": d0, "ticker": t, "sector": sec,
                    "signals": {"arima": a, "monte_carlo": 1.0 - a,   # counter-predicts
                                "kalman": 0.5, "garch": 0.5,          # constant → no IC
                                "sharpe": a * 0.5 + 0.25},
                    "composite": a, "passed_veto": True, "fwd_return": fwd,
                })
    return {"rebalance": "month", "segments": segs, "rows": rows}


def test_analyze_detects_good_and_bad_signals():
    a = analyze_signals(_panel())["signals"]
    assert a["arima"]["ic"] > 0.3 and "KEEP" in a["arima"]["verdict"]
    assert a["monte_carlo"]["ic"] < -0.3 and ("DROP" in a["monte_carlo"]["verdict"]
                                              or "FLIP" in a["monte_carlo"]["verdict"])
    assert a["arima"]["quintile_spread"] > 0 > a["monte_carlo"]["quintile_spread"]


def test_recommend_drops_negative_signals():
    w = recommend_weights(analyze_signals(_panel()))
    assert w["arima"] > 0 and w["monte_carlo"] == 0.0
    assert w["kalman"] == 0.0 and w["garch"] == 0.0     # zero-IC → floored out
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_note_renders():
    panel = _panel()
    data = {"as_of": "x", **{k: analyze_signals(panel)[k] for k in ("n_dates", "n_rows", "signals", "correlation")},
            "candidate_weights": recommend_weights(analyze_signals(panel)),
            "validation": {"candidate_oos": 0.18, "default_oos": 0.12, "spy_oos": 0.12, "n_oos": 3}}
    md = notes.signal_lab_note(data)
    assert "Signal Lab" in md and "arima" in md
    assert "type: tracker-signal-lab" in md
    assert "no analysis yet" in notes.signal_lab_note({"signals": {}}).lower()


def test_weight_matrix_mode_flag(monkeypatch):
    from screener.regime.weight_matrix import get_blended_weights
    probs = {"bull": 1.0, "sideways": 0.0, "bear": 0.0}
    monkeypatch.setenv("WEIGHT_MATRIX_MODE", "current")   # explicit, default-independent
    base = get_blended_weights(probs)
    monkeypatch.setenv("WEIGHT_MATRIX_MODE", "candidate")
    cand = get_blended_weights(probs)
    assert cand["monte_carlo"] == 0.0 and cand["arima"] > 0.5
    assert base != cand
    assert abs(sum(cand.values()) - 1.0) < 1e-9


# ── U31 lifecycle (alive / reversed / dead per year) ─────────────────────────

def _decay_panel():
    """arima predicts strongly in 2023 and is pure noise in 2024."""
    import random
    rng = random.Random(7)
    tickers = [f"T{i}" for i in range(12)]
    segs, rows = [], []
    for year, predictive in (("2023", True), ("2024", False)):
        for k in range(8):
            d0 = f"{year}-{k+1:02d}-01"
            segs.append({"d0": d0, "d1": f"{year}-{k+2:02d}-01",
                         "regime": "bull", "regime_conf": 1.0, "spy_return": 0.01})
            for j, t in enumerate(tickers):
                a = (j + 1) / len(tickers)
                fwd = ((a - 0.5) * 0.08 if predictive
                       else rng.uniform(-0.04, 0.04))
                rows.append({"d0": d0, "ticker": t, "sector": "Tech",
                             "signals": {"arima": a}, "composite": a,
                             "passed_veto": True, "fwd_return": fwd})
    return {"rebalance": "month", "segments": segs, "rows": rows}


def test_lifecycle_flags_mid_history_decay():
    from screener.signal_lab.lifecycle import signal_lifecycle
    lc = signal_lifecycle(_decay_panel())
    assert lc["years"] == ["2023", "2024"]
    arima = lc["signals"]["arima"]
    assert arima["2023"]["category"] == "alive"       # perfect predictor
    assert arima["2024"]["category"] in ("dead", "reversed")  # noise year
    assert arima["2023"]["n"] == 8


def test_lifecycle_insufficient_bucket_never_forced():
    from screener.signal_lab.lifecycle import categorise
    out = categorise([0.5, 0.5])                      # 2 < MIN_DATES
    assert out["category"] == "insufficient" and out["ic"] is None
    # a zero-variance series is maximal consistency, not absent evidence:
    # consistently +3% IC clears alive; consistently -3% is reversed
    assert categorise([0.03, 0.03, 0.03, 0.03])["category"] == "alive"
    assert categorise([-0.03, -0.03, -0.03, -0.03])["category"] == "reversed"
    # dead-zone mean with zero variance stays dead
    assert categorise([0.01, 0.01, 0.01, 0.01])["category"] == "dead"


def test_note_renders_lifecycle_table():
    panel = _decay_panel()
    from screener.signal_lab.lifecycle import signal_lifecycle
    a = analyze_signals(panel)
    data = {"as_of": "x", "n_dates": a["n_dates"], "n_rows": a["n_rows"],
            "signals": a["signals"], "correlation": a["correlation"],
            "candidate_weights": recommend_weights(a),
            "lifecycle": signal_lifecycle(panel), "validation": {}}
    md = notes.signal_lab_note(data)
    assert "Lifecycle (per year)" in md
    assert "🟢 alive" in md


def test_verdict_negative_band_never_weak_keep():
    # ICs in (-0.03, -0.02] predicted backwards but read "weak keep" before
    # the 2026-07-12 fix; pin the whole negative range.
    from screener.signal_lab.lab import _verdict
    assert "backwards" in _verdict(-0.025)
    assert "backwards" in _verdict(-0.05)
    assert _verdict(-0.01).startswith("DROP — no edge")
    assert _verdict(0.03) == "weak keep — small edge"
    assert _verdict(0.06) == "KEEP — real edge"
