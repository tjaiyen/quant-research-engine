"""Run-card artifact tests (U32)."""
from __future__ import annotations

import json

import pytest

from utils import run_card as rc


@pytest.fixture(autouse=True)
def _isolated_card_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "RUN_CARD_DIR", tmp_path / "run_cards")
    yield


def test_write_run_card_creates_strict_json():
    path = rc.write_run_card(
        "sim", {"years": 3, "rebalance": "quarter"},
        metrics={"sharpe": 0.8, "bad": float("nan"), "worse": float("inf")},
        validation={"permutation": {"p_value_max_dd": 0.3}})
    assert path is not None and path.exists()
    card = json.loads(path.read_text())          # strict JSON — NaN would fail
    assert card["schema_version"] == rc.SCHEMA_VERSION
    assert card["command"] == "sim"
    assert card["metrics"]["bad"] is None        # NaN → null
    assert card["metrics"]["worse"] is None      # inf → null
    assert card["validation"]["permutation"]["p_value_max_dd"] == 0.3
    assert len(card["params_hash"]) == 16


def test_params_hash_deterministic_and_order_insensitive():
    a = rc.params_hash({"years": 3, "rebalance": "quarter"})
    b = rc.params_hash({"rebalance": "quarter", "years": 3})
    c = rc.params_hash({"rebalance": "quarter", "years": 5})
    assert a == b != c


def test_write_run_card_never_raises(monkeypatch):
    # Point the card dir somewhere unwritable — must return None, not raise.
    monkeypatch.setattr(rc, "RUN_CARD_DIR", None)
    assert rc.write_run_card("sim", {}) is None
