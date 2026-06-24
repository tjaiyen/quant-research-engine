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
        "summary": {"total_screened": 220, "total_passed_veto": 55,
                    "veto_rate_pct": 75.0, "total_skipped": 3, "total_failed": 10,
                    "total_sectors": 11},
        "sectors": {"Healthcare": [
            {"rank": 1, "ticker": "JNJ", "composite_score": 0.665, "passed_veto": True,
             "veto_reason": None,
             "signal_scores": {"arima": 0.5, "kalman": 0.51, "garch": 0.46,
                               "monte_carlo": 0.99, "sharpe": 0.86}},
            {"rank": 2, "ticker": "ABBV", "composite_score": 0.40, "passed_veto": False,
             "veto_reason": "EARNINGS_BLACKOUT", "signal_scores": {}}]},
        "sentiment": [{"ticker": "WBD", "sentiment_score": -0.4, "label": "NEGATIVE",
                       "n_headlines": 6}],
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
    # comprehensive sections
    assert "Screener" in out and "220" in out         # screener stats
    assert "By sector" in out and "Healthcare" in out  # sector table
    assert "monte carlo" in out                        # signal breakdown bars
    assert "EARNINGS_BLACKOUT" in out                  # veto reasons
    assert "Positions" in out                          # positions section (empty ok)
    assert "News sentiment" in out                     # sentiment section
    assert 'class="nav"' in out                        # nav links to notes


def test_dashboard_html_handles_empty():
    out = html.dashboard_html({"as_of": "x"})
    assert out.startswith("<!DOCTYPE html>")
    assert "No screener run yet" in out
    assert "builds after the first monthly buy" in out  # sparse equity curve
    assert "$10,000" in out                              # default portfolio value


def test_run_banner_states():
    assert "FAILED" in html._run_banner({"job": "monthly", "ended": "x", "status": "fail"})
    assert "No scheduled run" in html._run_banner(
        {"job": "daily", "ended": "x", "status": "ok", "stale": True, "age_h": 50})
    assert "healthy" in html._run_banner(
        {"job": "daily", "ended": "x", "status": "ok", "stale": False})
    assert html._run_banner({}) == ""   # no beacon → no banner


def test_dashboard_html_shows_tournament_card():
    d = _sample()
    d["tournament"] = {"verdict": "Top-1 won, beat SPY.", "beat_spy": 0.05,
                       "beat_random": 0.04, "oos_rank": 2,
                       "leaderboard": [{"rank": 1, "label": "Top-1 per sector",
                                        "group": "concentration", "total": 0.31,
                                        "sharpe": 1.2, "excess": 0.05},
                                       {"rank": 2, "label": "SPY buy-hold",
                                        "group": "control", "total": 0.26,
                                        "sharpe": 0.9, "excess": 0.0}]}
    out = html.dashboard_html(d)
    assert "Strategy tournament" in out and "Top-1 per sector" in out
    assert "hypothesis, not proof" in out


def test_dashboard_html_shows_run_banner():
    d = _sample()
    d["last_run"] = {"job": "weekly", "ended": "2026-06-21T18:00:00",
                     "status": "fail"}
    out = html.dashboard_html(d)
    assert 'class="runbar fail"' in out and "FAILED" in out


def test_dashboard_html_escapes_injection():
    # Untrusted-looking text must not break out into markup (B13 hygiene).
    out = html.dashboard_html({
        "as_of": "x", "regime": {"label": "<script>x</script>"},
        "top_picks": [{"ticker": "<b>HACK</b>", "sector": "x", "composite_score": 1}],
    })
    assert "<script>x</script>" not in out
    assert "<b>HACK</b>" not in out
    assert "&lt;b&gt;HACK&lt;/b&gt;" in out
