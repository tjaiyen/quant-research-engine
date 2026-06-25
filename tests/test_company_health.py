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
    for k in ("health_score", "roe", "debt_to_equity", "operating_margin", "next_earnings"):
        assert glossary.has(k), f"missing glossary term: {k}"
