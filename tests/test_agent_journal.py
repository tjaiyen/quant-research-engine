"""Tests for the autonomous self-narrating agent: decision feed + journal events."""
from __future__ import annotations

from render import notes


# ── agent_log_note (pure, first-person) ──────────────────────────────────────

def test_agent_log_note_empty():
    md = notes.agent_log_note([])
    assert "type: tracker-decisions" in md
    assert "No decisions logged yet" in md


def test_agent_log_note_screen_entry():
    d = [{"when": "2026-06-20T18:00:00+00:00", "kind": "screen",
          "regime": "sideways", "regime_conf": 1.0, "total_screened": 220,
          "total_passed": 55, "veto_rate": 75.0,
          "top": [{"ticker": "JNJ"}, {"ticker": "EA"}]}]
    md = notes.agent_log_note(d)
    assert "I screened the market" in md
    assert "SIDEWAYS" in md and "55" in md and "JNJ" in md


def test_decision_text_trades_first_person():
    d = {"when": "2026-07-01T13:30:00", "kind": "trades", "trades": [
        {"action": "BUY", "ticker": "AAPL", "trigger_reason": "NEW_BUY"},
        {"action": "SELL", "ticker": "XYZ", "trigger_reason": "STOP_LOSS"},
    ]}
    txt = notes._decision_text(d)
    assert "opened **AAPL**" in txt
    assert "stopped out of **XYZ**" in txt


def test_decision_text_daily():
    d = {"when": "2026-07-02T20:30:00", "kind": "daily",
         "details": {"stop_hits": 1, "decay_alerts": 2}, "total_value": 10240.0}
    txt = notes._decision_text(d)
    assert "Daily check" in txt and "1" in txt and "2" in txt


# ── get_system_events getter ─────────────────────────────────────────────────

def test_get_system_events(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DB_PATH", str(tmp_path / "portfolio.db"))
    from auto_trader.state import portfolio_db as pdb
    pdb.initialize_db()
    pdb.log_system_event("DAILY_MONITOR", "Complete",
                         {"stop_hits": 1, "decay_alerts": 0})
    rows = pdb.get_system_events()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "DAILY_MONITOR"
    assert rows[0]["details"] == {"stop_hits": 1, "decay_alerts": 0}  # parsed JSON


# ── journal_note renders the events table ────────────────────────────────────

def test_journal_note_with_events():
    md = notes.journal_note(
        "2026-07-01",
        [{"executed_at": "2026-07-01T13:30:00", "action": "BUY", "ticker": "AAPL",
          "shares": 5, "price": 190.0, "total_value": 950.0}],
        events=[{"event_time": "2026-07-01T13:31:00", "event_type": "RISK_GUARD_COMPLETE",
                 "description": "2 buys trimmed"}],
    )
    assert "## System events" in md
    assert "RISK_GUARD_COMPLETE" in md
