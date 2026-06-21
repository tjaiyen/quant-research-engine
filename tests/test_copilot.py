"""Tests for the AI co-pilot — graceful degradation, prompt, parse, note render.

No real Anthropic SDK or API key is touched: the absent-SDK path runs for real
and the success path injects a fake `anthropic` module.
"""
from __future__ import annotations

import sys
import types

from render import notes
from screener.copilot import advisor


# ── graceful degradation ─────────────────────────────────────────────────────

def test_review_graceful_without_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)   # import → ImportError
    out = advisor.copilot_review({"regime": {"label": "sideways"}})
    assert out["available"] is False
    assert "SDK" in out["reason"]


def _fake_anthropic(text="My read is the picks look solid.", stop="end_turn"):
    mod = types.ModuleType("anthropic")

    class AuthenticationError(Exception):
        pass

    class _Block:
        def __init__(self, t, x):
            self.type, self.text = t, x

    class _Resp:
        stop_reason = stop
        content = [_Block("thinking", ""), _Block("text", text)]

    class _Msgs:
        def create(self, **kw):
            return _Resp()

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Msgs()

    mod.AuthenticationError = AuthenticationError
    mod.Anthropic = _Client
    return mod


def test_review_success(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic())
    out = advisor.copilot_review({"regime": {"label": "sideways"}})
    assert out["available"] is True
    assert "My read" in out["commentary"]
    assert out["model"] == advisor.COPILOT_MODEL


def test_review_handles_refusal(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic(stop="refusal"))
    out = advisor.copilot_review({"regime": {}})
    assert out["available"] is False


# ── prompt builder ───────────────────────────────────────────────────────────

def test_build_user_prompt():
    p = advisor._build_user_prompt({
        "regime": {"label": "bear", "confidence": 0.8},
        "top_picks": [{"ticker": "JNJ", "sector": "Healthcare", "composite_score": 0.7}],
        "portfolio": {"total_value": 10000, "n_positions": 2},
        "recent_decisions": ["I screened the market — sideways"],
    })
    assert "bear" in p and "JNJ" in p and "I screened the market" in p


# ── note render ──────────────────────────────────────────────────────────────

def test_copilot_note_off():
    md = notes.copilot_note({"available": False, "reason": "no or invalid ANTHROPIC_API_KEY"},
                            {"as_of": "x"})
    assert "type: tracker-copilot" in md
    assert "AI co-pilot — off" in md
    assert "requirements-copilot" in md
    assert "no or invalid ANTHROPIC_API_KEY" in md


def test_copilot_note_available():
    md = notes.copilot_note(
        {"available": True, "commentary": "I'd hold steady this week.", "model": "claude-opus-4-8"},
        {"regime": {"label": "sideways"}, "as_of": "x"})
    assert "My take" in md
    assert "I'd hold steady this week." in md
    assert "advisory only" in md.lower()
