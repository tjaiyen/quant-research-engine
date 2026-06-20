"""Tests for the U20 investment-review slide deck (render/slides.py)."""
from __future__ import annotations

from render import slides
from screener.backtest.scorecard import summarize


def _scorecard(buckets):
    h = {}
    for k in ("7d", "28d", "84d", "to_date"):
        s = summarize(buckets.get(k, []))
        s["attempted"] = len(buckets.get(k, []))
        s["coverage"] = 1.0 if buckets.get(k) else None
        h[k] = s
    return {"horizons": h, "n_runs": 4, "n_graded_runs": 4}


def test_review_deck_full():
    regime = {"label": "sideways", "confidence": 1.0}
    picks = [{"rank": 1, "ticker": "JNJ", "sector": "Healthcare", "composite_score": 0.66}]
    good = [{"forward_return": 0.1, "alpha": 0.05} for _ in range(6)]
    sc = _scorecard({"7d": good, "28d": good})
    snaps = [
        {"snapshot_date": "2026-06-01", "total_value": 10000.0, "benchmark_value": 100.0, "n_positions": 5},
        {"snapshot_date": "2026-06-30", "total_value": 11000.0, "benchmark_value": 105.0, "n_positions": 5},
    ]
    md = slides.review_deck(regime, picks, sc, snaps, as_of="2026-06-19T00:00:00+00:00")

    # Frontmatter + slide separators present.
    assert md.startswith("---\ntheme:")
    assert "\n\n---\n\n" in md                 # at least one slide break
    assert md.count("\n---\n") >= 4            # several slides
    # Content slides.
    assert "Market Regime" in md and "SIDEWAYS" in md
    assert "JNJ" in md
    assert "Are the Picks Working?" in md
    # Equity-curve chart block with both series.
    assert "```chart" in md
    assert "Paper portfolio" in md and "SPY" in md
    assert "2026-06-19" in md                  # the date


def test_review_deck_sparse_no_crash():
    # Everything empty (fresh install): must render, no exceptions.
    sc = _scorecard({})
    md = slides.review_deck({}, [], sc, [], as_of="2026-06-19T00:00:00+00:00")
    assert "Weekly Investment Review" in md
    assert "Too early" in md                   # scorecard verdict
    assert "equity curve appears once" in md   # chart placeholder, not a crash
    assert "_No rows._" in md                  # empty picks table


def test_review_deck_with_backtest_section():
    sc = _scorecard({})
    bt = {"walk_forward": {"n_windows": 3, "win_rate": 0.33, "mean_lift": -0.01}}
    md = slides.review_deck({}, [], sc, [], backtest=bt, as_of="x")
    assert "Evidence of Skill" in md
    assert "3" in md  # n_windows
