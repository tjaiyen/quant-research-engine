"""The plain-language glossary is the dashboard's content backbone — every entry
must be complete and well-formed so tooltips, Learn mode, and the modal render."""
from __future__ import annotations

import json

from render import glossary


def test_every_entry_has_required_fields():
    for key, e in glossary.GLOSSARY.items():
        for field in ("plain", "term", "short", "long", "example"):
            assert e.get(field), f"{key} missing/empty '{field}'"
        # plain label leads in plain language — keep tooltips short and readable.
        assert len(e["short"]) <= 200, f"{key} tooltip too long"
        assert isinstance(e["plain"], str) and isinstance(e["term"], str)


def test_label_leads_plain_then_term():
    # "Plain (Term)" when the term differs from the plain label.
    assert glossary.label("ic") == "Prediction accuracy (IC)"
    assert glossary.label("sharpe") == "Reward for the risk (Sharpe ratio)"
    assert glossary.label("nonexistent") == "nonexistent"   # graceful fallback


def test_helpers():
    assert glossary.has("regime") and not glossary.has("nope")
    assert glossary.short("regime")                       # non-empty tooltip
    assert glossary.short("nope") == ""


def test_as_json_is_valid_and_roundtrips():
    parsed = json.loads(glossary.as_json())
    assert parsed.keys() == glossary.GLOSSARY.keys()
    assert parsed["ic"]["term"] == "IC"


def test_every_tournament_variant_is_explained():
    # Sync guard: every strategy raced in the tournament must carry a plain-language
    # explanation + example, so the dashboard never shows an unexplained strategy.
    from screener.tournament.variants import default_variants
    labels = [v["label"] for v in default_variants()] + ["CANDIDATE ARIMA+Sharpe"]
    for lbl in labels:
        key = glossary.strategy_key(lbl)
        assert glossary.has(key), f"tournament strategy '{lbl}' ({key}) has no explanation"
        e = glossary.GLOSSARY[key]
        assert e["short"] and e["example"], f"strategy '{lbl}' missing short/example"


def test_strategy_key_is_stable_slug():
    assert glossary.strategy_key("Pure Sharpe") == "strat_pure_sharpe"
    assert glossary.strategy_key("Risk (Sharpe+MC+GARCH)") == "strat_risk_sharpe_mc_garch"


def test_no_jargon_leak_in_plain_labels():
    # The leading plain label should not itself be the bare acronym/jargon.
    jargon = {"IC", "DSR", "CPCV", "GARCH", "ARIMA", "HMM", "P&L", "OOS"}
    for key, e in glossary.GLOSSARY.items():
        assert e["plain"] not in jargon, f"{key} plain label is jargon: {e['plain']}"
