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
    assert 'data-term="monte_carlo"' in out            # signal breakdown bars (plain-labelled)
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


def test_tournament_strategies_carry_explanations():
    from render import glossary
    d = _sample()
    d["tournament"] = {"verdict": "ok", "beat_spy": 0.05, "beat_random": 0.04,
                       "oos_rank": 2, "leaderboard": [
                           {"rank": 1, "label": "Pure Sharpe", "group": "weighting",
                            "total": 0.31, "sharpe": 1.2, "excess": 0.05},
                           {"rank": 2, "label": "SPY buy-hold", "group": "control",
                            "total": 0.26, "sharpe": 0.9, "excess": 0.0}]}
    out = html.dashboard_html(d)
    assert f'data-term="{glossary.strategy_key("Pure Sharpe")}"' in out
    assert f'data-term="{glossary.strategy_key("SPY buy-hold")}"' in out
    # the explanation + example are embedded for the JS popover
    assert "Reward-for-risk only" in out and "calmest high-return" in out


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


# ── educational / interactive layer ──────────────────────────────────────────

def test_educational_affordances_present():
    out = html.dashboard_html(_sample())
    assert 'id="learnBtn"' in out                       # 🎓 Learn-mode toggle
    assert 'id="gloss"' in out and 'id="glossSearch"' in out   # searchable glossary modal
    assert 'id="tip"' in out                             # tooltip/popover element
    assert 'id="intro"' in out                           # onboarding card
    assert out.count('class="i"') > 20                   # many info buttons wired
    assert "localStorage" in out and "Learn mode" in out  # client JS + label
    assert "__GLOSSARY_JSON__" not in out                # placeholder substituted
    assert '"plain"' in out                              # glossary embedded for JS


def test_hierarchy_headline_hero_zones():
    out = html.dashboard_html(_sample())
    assert 'class="headline"' in out                       # glance summary strip
    assert "indexed to 100" in out                         # hero equity caption
    assert all(f'id="{z}"' in out for z in ("money", "today", "working", "hud"))
    assert "kpi-row1" in out and "kpi-row2" in out and "kpi big" in out  # 2-tier KPIs


def test_in_page_nav_and_back_to_top():
    out = html.dashboard_html(_sample())
    # primary nav now points to page sections, not just the .md notes
    for zid in ("equity", "money", "today", "working", "hud"):
        assert f'href="#{zid}"' in out and f'id="{zid}"' in out
    assert 'id="toTop"' in out and "IntersectionObserver" in out
    assert "Obsidian notes" in out                          # outbound notes still reachable


def test_empty_zones_drop_no_bare_headers():
    out = html.dashboard_html({"as_of": "x"})              # no working/hud data
    assert 'id="money"' in out                             # KPIs always present
    assert 'id="working"' not in out and 'id="hud"' not in out


def test_design_tokens_and_light_mode_present():
    out = html.dashboard_html(_sample())
    assert "--bg:" in out and "--pos:" in out and "--fs-kpi:" in out   # token system
    assert "body.light" in out                                          # light overrides
    assert 'id="themeBtn"' in out and "qt_theme" in out                 # toggle + persistence
    assert "var(--surface)" in out and "var(--text)" in out             # rules use tokens


def test_dashboard_stays_offline_self_contained():
    # No external libraries/CDNs/URLs — must open offline from the Drive vault.
    out = html.dashboard_html(_sample())
    assert "http://" not in out and "https://" not in out
    assert "<script src" not in out and "cdn" not in out.lower()
    assert "<link" not in out                            # no external stylesheet


def test_every_rendered_term_has_a_definition():
    """Completeness gate: no metric ships a `?` without a glossary entry."""
    import re
    from render import glossary
    d = _sample()
    d["tournament"] = {"verdict": "ok", "beat_spy": 0.05, "beat_random": 0.04,
                       "oos_rank": 2, "leaderboard": [{"rank": 1, "label": "X",
                       "group": "weighting", "total": 0.3, "sharpe": 1.1, "excess": 0.05}]}
    d["signal_lab"] = {"signals": {"arima": {"ic": 0.06, "verdict": "KEEP"}},
                       "validation": {"candidate_oos": 0.18, "default_oos": 0.12,
                                      "spy_oos": 0.12, "n_oos": 3}}
    d["last_run"] = {"job": "weekly", "ended": "x", "status": "ok"}
    out = html.dashboard_html(d)
    used = set(re.findall(r'data-term="([^"]+)"', out))
    assert used, "expected info buttons in the output"
    missing = used - set(glossary.KEYS)
    assert not missing, f"info buttons reference undefined glossary keys: {missing}"


def test_required_concepts_are_defined():
    # Coverage floor: the core concepts a reader will hit must always be defined.
    from render import glossary
    for key in ("regime", "composite", "veto", "ic", "sharpe", "dsr", "cpcv",
                "alpha", "drawdown", "out_of_sample", "momentum", "unrealized_pnl"):
        assert glossary.has(key), f"missing core glossary term: {key}"


def test_static_completeness_gate_no_undefined_keys():
    """Source-level gate: EVERY glossary key wired into html.py must be defined —
    independent of which sections a given run's data happens to populate."""
    import re
    from render import glossary
    src = open(html.__file__, encoding="utf-8").read()
    keys = set()
    keys |= set(re.findall(r'_ibtn\("([a-z_]+)"\)', src))
    keys |= set(re.findall(r'_term\("([a-z_]+)"\)', src))
    keys |= set(re.findall(r'_th\("([a-z_]+)"', src))
    keys |= set(re.findall(r'key="([a-z_]+)"', src))
    keys |= set(re.findall(r'sub_key="([a-z_]+)"', src))
    keys |= set(re.findall(r'_title\(\s*"[^"]*",\s*"[^"]*",\s*"([a-z_]+)"', src))
    # dynamic veto-reason → key mapping must also only yield defined keys
    keys |= {html._veto_key(r) for r in
             ["EARNINGS_BLACKOUT", "SENTIMENT_VETO", "VETO_VOL", "VETO_TAIL", "other"]}
    assert keys, "expected to find wired glossary keys in html.py source"
    missing = keys - set(glossary.KEYS)
    assert not missing, f"html.py wires undefined glossary keys: {missing}"


def test_signal_bar_keys_are_all_defined():
    # Every per-pick signal bar label must have a glossary entry.
    from render import glossary
    for sig in html._SIGNALS:
        assert glossary.has(sig), f"signal bar '{sig}' has no glossary entry"


def test_company_names_render_next_to_tickers():
    d = _sample()
    d["positions"] = [{"ticker": "JNJ", "shares": 3, "cost_basis": 150,
                       "current_price": 160, "market_value": 480,
                       "unrealized_pnl": 30}]
    d["names"] = {"JNJ": "Johnson Co", "ZZZ": "<b>Evil</b> Inc"}
    out = html.dashboard_html(d)
    assert '<span class="coname">Johnson Co</span>' in out   # name beside ticker
    assert ".coname" in out                                   # styled muted
    # unknown ticker → no name; known name with HTML is escaped (B13)
    d["positions"] = [{"ticker": "ZZZ", "shares": 1, "cost_basis": 1,
                       "current_price": 1, "market_value": 1, "unrealized_pnl": 0}]
    out2 = html.dashboard_html(d)
    assert "<b>Evil</b>" not in out2 and "&lt;b&gt;Evil&lt;/b&gt;" in out2


def test_company_names_optional_backward_compatible():
    # No names map → ticker still renders, no crash.
    out = html.dashboard_html(_sample())   # _sample has no "names" key
    assert "JNJ" in out
