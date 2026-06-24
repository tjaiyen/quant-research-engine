"""Tests for the self-contained HTML dashboard builder."""
from __future__ import annotations

from render import html


def _sample():
    return {
        "as_of": "2026-06-21T12:00:00+00:00",
        "regime": {"label": "sideways", "confidence": 1.0},
        "top_picks": [{"ticker": "JNJ", "sector": "Healthcare", "composite_score": 0.665}],
        "latest_snapshot": {"total_value": 10240.0, "unrealized_pnl": 240.0,
                            "realized_pnl_ytd": 0.0, "drawdown_from_peak": -0.012,
                            "n_positions": 3},
        "snapshots": [{"total_value": 10000, "benchmark_value": 100, "snapshot_date": "2026-06-01"},
                      {"total_value": 10240, "benchmark_value": 102, "snapshot_date": "2026-06-21"}],
        "positions": [],
        "decisions": ["🔭 **2026-06-19** — I screened the market. Regime **SIDEWAYS**."],
        "scorecard": {"horizons": {"7d": {"n": 0}}, "paper": {"status": "no_data"}},
        "copilot": {"available": True, "model": "claude-opus-4-8",
                    "commentary": "My read is cautiously constructive.\n\nI'd watch WBD."},
    }


def test_dashboard_html_is_wellformed():
    out = html.dashboard_html(_sample())
    assert out.startswith("<!DOCTYPE html>")
    assert out.rstrip().endswith("</html>")
    assert "http-equiv=\"refresh\"" in out          # auto-reload
    assert "<svg" in out and "polyline" in out       # equity chart present
    assert "JNJ" in out                              # picks
    assert "<strong>SIDEWAYS</strong>" in out        # decision bold converted
    assert "Co-pilot take" in out and "WBD" in out   # copilot section
    assert "claude-opus-4-8" in out


def test_dashboard_html_handles_empty():
    out = html.dashboard_html({"as_of": "x"})
    assert out.startswith("<!DOCTYPE html>")
    assert "No screener run yet" in out
    assert "builds after the first monthly buy" in out  # sparse equity curve
    assert "$10,000" in out                              # default portfolio value


def test_dashboard_html_escapes_injection():
    # Untrusted-looking text must not break out into markup (B13 hygiene).
    out = html.dashboard_html({
        "as_of": "x", "regime": {"label": "<script>x</script>"},
        "top_picks": [{"ticker": "<b>HACK</b>", "sector": "x", "composite_score": 1}],
    })
    assert "<script>x</script>" not in out
    assert "<b>HACK</b>" not in out
    assert "&lt;b&gt;HACK&lt;/b&gt;" in out
