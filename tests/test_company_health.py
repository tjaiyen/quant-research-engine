"""Company-health scoring + render (Phase 21): grade vs sector floors, D/E
normalisation, graceful degradation, note + dashboard section."""
from __future__ import annotations

from industry_config import SECTOR_QUALITY_FLOORS
from screener.health.scorer import grade, score_ticker_health, _normalise_debt_to_equity


def test_grade_strong_vs_weak_vs_sector_floor():
    f = SECTOR_QUALITY_FLOORS["Industrials"]
    strong = grade({"operating_margin": 0.12, "roe": 0.18,
                    "debt_to_equity": 0.6, "current_ratio": 1.4}, f)
    weak = grade({"operating_margin": 0.02, "roe": 0.03,
                  "debt_to_equity": 3.0, "current_ratio": 0.5}, f)
    assert strong["health_label"] == "STRONG" and strong["floors_passed"] == 4
    assert weak["health_label"] == "WEAK" and weak["floors_passed"] == 0


def test_floors_that_dont_apply_are_skipped():
    # Banks: only an ROE floor (others None) — missing margins must not penalise.
    fin = SECTOR_QUALITY_FLOORS["Financials"]
    g = grade({"roe": 0.10}, fin)
    assert g["floors_total"] == 1 and g["health_label"] == "STRONG"
    # No usable metrics → UNAVAILABLE, not a 0 score.
    assert grade({}, SECTOR_QUALITY_FLOORS["Industrials"])["health_label"] == "UNAVAILABLE"


def test_debt_to_equity_percent_normalisation():
    assert _normalise_debt_to_equity(60.0) == 0.6      # yfinance percent → ratio
    assert _normalise_debt_to_equity(0.6) == 0.6       # already a ratio
    assert _normalise_debt_to_equity(None) is None


def test_score_ticker_health_graceful_when_no_data(monkeypatch):
    # yfinance unavailable → UNAVAILABLE, never raises.
    import screener.health.scorer as s
    monkeypatch.setattr(s, "_yf_metrics", lambda t: None)
    snap = score_ticker_health("ZZZ", "Industrials")
    assert snap["health_label"] == "UNAVAILABLE" and snap["roe"] is None


def test_health_note_and_section_render():
    from render import notes
    from render import html
    rows = [{"ticker": "GD", "name": "General Dynamics", "health_label": "STRONG",
             "roe": 0.18, "operating_margin": 0.12, "debt_to_equity": 0.6,
             "floors_passed": 4, "floors_total": 4, "pe": 19.0,
             "next_earnings": "2026-07-23"}]
    md = notes.company_health_note({"as_of": "x", "rows": rows})
    assert "Company health" in md and "General Dynamics" in md and "2026-07-23" in md
    assert "type: tracker-company-health" in md
    assert "no health data yet" in notes.company_health_note({"rows": []}).lower()
    out = html._company_health_section(rows)
    assert "Company health" in out and "GD" in out and "2026-07-23" in out
    assert "class='pos'" in out                         # STRONG → green
    assert html._company_health_section([]) == ""       # empty → nothing


def test_health_glossary_terms_defined():
    from render import glossary
    for k in ("health_score", "roe", "debt_to_equity", "operating_margin",
              "next_earnings", "earnings_surprise"):
        assert glossary.has(k), f"missing glossary term: {k}"


def test_earnings_verdict_thresholds():
    from screener.health.earnings import verdict
    assert verdict(4.7) == "beat" and verdict(-3.6) == "miss"
    assert verdict(0.3) == "in-line" and verdict(None) == "n/a"


def test_earnings_history_db_roundtrip():
    from utils.db import init_db, upsert_earnings_history, fetch_earnings_history
    init_db()
    upsert_earnings_history("ZZZE", [
        {"report_date": "2025-04-23", "eps_estimate": 3.5, "eps_actual": 3.66, "surprise_pct": 4.7},
        {"report_date": "2025-01-29", "eps_estimate": 4.05, "eps_actual": 4.15, "surprise_pct": 2.45},
    ])
    h = fetch_earnings_history("ZZZE", limit=4)
    assert len(h) == 2 and h[0]["report_date"] == "2025-04-23"  # newest first
    assert h[0]["surprise_pct"] == 4.7
    upsert_earnings_history("ZZZE", [])     # replace-all with empty clears it
    assert fetch_earnings_history("ZZZE") == []


def test_earnings_renders_in_note_and_section():
    from render import notes, html
    rows = [{"ticker": "GD", "name": "General Dynamics", "health_label": "STRONG",
             "roe": 0.18, "debt_to_equity": 0.6, "pe": 19.0, "next_earnings": "2026-07-22",
             "last_surprise_pct": 4.7,
             "earnings": [{"report_date": "2025-04-23", "eps_estimate": 3.5,
                           "eps_actual": 3.66, "surprise_pct": 4.7, "verdict": "beat"},
                          {"report_date": "2024-10-23", "eps_estimate": 3.47,
                           "eps_actual": 3.35, "surprise_pct": -3.59, "verdict": "miss"}]}]
    md = notes.company_health_note({"as_of": "x", "rows": rows})
    assert "Last earnings" in md and "Recent earnings" in md and "beat" in md and "miss" in md
    out = html._company_health_section(rows)
    assert "Last earnings" in out and "+4.7% beat" in out and "class='pos'" in out
