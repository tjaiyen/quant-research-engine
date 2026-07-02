"""Tier-0 upgrades (Phase 30): low-vol signal, slippage realism, alerts, digest."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import mock_broker
from mock_broker import MockAlpacaClient


def _history(vol_daily: float, n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0003, vol_daily, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame({"Close": close})


# ── low-vol signal ───────────────────────────────────────────────────────────

def test_lowvol_calm_beats_volatile():
    from screener.signals.lowvol_signal import lowvol_signal
    calm = lowvol_signal("CALM", _history(0.005))
    wild = lowvol_signal("WILD", _history(0.04))
    assert 0.0 < wild["score"] < calm["score"] <= 1.0
    assert calm["raw"] < wild["raw"]                 # raw = ann vol


def test_lowvol_insufficient_history_and_bad_input():
    from screener.signals.lowvol_signal import lowvol_signal
    short = lowvol_signal("X", _history(0.01, n=10))
    assert short["score"] == 0.0
    assert short["metadata"]["error"] == "insufficient_history"
    bad = lowvol_signal("X", pd.DataFrame({"Close": ["a", "b"] * 40}))
    assert bad["score"] == 0.0                       # never raises


# ── slippage on live (mark-to-market) fills only ─────────────────────────────

def test_slippage_adverse_on_live_fills(monkeypatch):
    monkeypatch.setattr(mock_broker, "_market_price", lambda s: 100.0)
    monkeypatch.setattr(mock_broker, "SLIPPAGE_BPS", 10.0)
    c = MockAlpacaClient(cash=10_000.0, mark_to_market=True)
    buy = c.submit_order("AAA", "buy", qty=1)
    assert float(buy.filled_avg_price) == pytest.approx(100.10)   # buys pay up
    sell = c.submit_order("AAA", "sell", qty=1)
    assert float(sell.filled_avg_price) == pytest.approx(99.90)   # sells receive less
    # round-trip costs exactly the spread — the realism being modeled
    assert 10_000.0 - c._cash == pytest.approx(0.20)


def test_slippage_never_touches_test_mode(monkeypatch):
    monkeypatch.setattr(mock_broker, "SLIPPAGE_BPS", 10.0)
    c = MockAlpacaClient(cash=10_000.0)              # mtm OFF (unit-test mode)
    assert float(c.submit_order("AAA", "buy", qty=1).filled_avg_price) == 100.0


# ── macOS notification channel ───────────────────────────────────────────────

def test_macos_alert_channel(monkeypatch):
    from auto_trader.monitor import alert_engine as ae
    calls = []
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: calls.append(a[0]) or None)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setenv("ALERT_MACOS", "true")
    assert ae._try_macos('Drift "x"', "body") is True
    assert calls and calls[0][0] == "osascript"
    assert 'Quant Tracker' in calls[0][2]
    assert '\\"x\\"' in calls[0][2]                  # quotes escaped
    monkeypatch.setenv("ALERT_MACOS", "false")
    assert ae._try_macos("s", "b") is False          # kill-switch respected
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("ALERT_MACOS", "true")
    assert ae._try_macos("s", "b") is False          # non-macOS → other channels


# ── weekly digest note ───────────────────────────────────────────────────────

def test_digest_note_renders_and_degrades():
    from render import notes
    full = notes.digest_note({
        "as_of": "2026-07-01T20:00:00Z",
        "snapshot": {"total_value": 10038.41, "unrealized_pnl": 62.47,
                     "realized_pnl_ytd": -24.06},
        "regime": {"label": "sideways"},
        "fleet": [{"label": "Pure Sharpe", "ret_pct": 1.2, "excess_pct": 0.3},
                  {"label": "Pending", "ret_pct": None}],
        "recon": {"ok": True},
        "trades_7d": [{"action": "BUY", "executed_at": "2026-07-01T13:25:00"},
                      {"action": "SELL", "executed_at": "2026-07-01T13:20:00"}],
    })
    assert "type: tracker-digest" in full
    assert "$10,038.41" in full and "$38.41" in full
    assert "books reconciled" in full
    assert "Pure Sharpe" in full and "Pending" not in full   # live rows only
    assert "1 buys / 1 sells" in full
    empty = notes.digest_note({})
    assert "Weekly digest" in empty                  # sparse → no crash
