"""Phase K — allocator + delta_engine invariants (Gate 9 sells-before-buys)."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# GATE 9 — Delta engine emits sells before buys
# ---------------------------------------------------------------------------
def test_gate9_sells_precede_buys():
    from auto_trader.allocator.delta_engine import compute_delta

    target = {
        "AAPL": {
            "ticker": "AAPL",
            "composite_score": 0.80,
            "allocation_usd": 200.0,
            "sector": "Technology",
        }
    }
    current = [
        {
            "ticker": "META",
            "shares": 5.0,
            "current_price": 70.0,
            "cost_basis": 100.0,
            "stop_loss_price": 88.0,
            "last_score": 0.30,  # below SIGNAL_EXIT_THRESHOLD
            "sector": "Communications",
        }
    ]
    instr = compute_delta(target, current, 10_000.0)
    actions = [i.action for i in instr]
    assert "SELL" in actions and "BUY" in actions

    first_buy = next((i for i, a in enumerate(actions) if a == "BUY"), len(actions))
    last_sell = max((i for i, a in enumerate(actions) if a == "SELL"), default=-1)
    assert last_sell < first_buy, (
        f"Sells must precede buys; last_sell={last_sell}, first_buy={first_buy}"
    )


def test_stop_loss_triggers_first_among_sells():
    """STOP_LOSS should sort before SIGNAL_EXIT and REBALANCE_SELL."""
    from auto_trader.allocator.delta_engine import compute_delta

    current = [
        {
            "ticker": "AAA",
            "shares": 10.0,
            "current_price": 50.0,
            "stop_loss_price": 60.0,  # current <= stop
            "last_score": 0.70,
            "sector": "X",
        },
        {
            "ticker": "BBB",
            "shares": 10.0,
            "current_price": 50.0,
            "stop_loss_price": 40.0,  # well above stop
            "last_score": 0.30,       # but score below exit threshold
            "sector": "Y",
        },
        {
            "ticker": "CCC",
            "shares": 10.0,
            "current_price": 50.0,
            "stop_loss_price": 40.0,
            "last_score": 0.70,        # safe — gets sold only if not in target
            "sector": "Z",
        },
    ]
    target: dict = {}  # nothing in target → all rebalance-sold
    instr = compute_delta(target, current, 10_000.0)
    triggers = [i.trigger_reason for i in instr if i.action == "SELL"]
    # STOP_LOSS first (AAA), then SIGNAL_EXIT (BBB), then REBALANCE_SELL (CCC)
    assert triggers[0] == "STOP_LOSS"
    assert triggers[1] == "SIGNAL_EXIT"
    assert triggers[2] == "REBALANCE_SELL"


def test_new_buy_emitted_for_target_not_held():
    from auto_trader.allocator.delta_engine import compute_delta

    target = {
        "NEW": {"ticker": "NEW", "composite_score": 0.7, "allocation_usd": 100, "sector": "X"}
    }
    current: list[dict] = []
    instr = compute_delta(target, current, 1000.0)
    assert len(instr) == 1
    assert instr[0].action == "BUY"
    assert instr[0].trigger_reason == "NEW_BUY"


def test_rebalance_buy_when_underweight():
    """If we hold less than target_alloc worth, emit a REBALANCE_BUY for the shortfall."""
    from auto_trader.allocator.delta_engine import compute_delta

    target = {
        "AAPL": {"ticker": "AAPL", "composite_score": 0.8, "allocation_usd": 200, "sector": "Tech"}
    }
    current = [
        {
            "ticker": "AAPL",
            "shares": 1.0,
            "current_price": 100.0,
            "stop_loss_price": 50.0,
            "last_score": 0.80,
            "sector": "Tech",
        }
    ]
    instr = compute_delta(target, current, 1000.0)
    buys = [i for i in instr if i.action == "BUY"]
    assert len(buys) == 1
    assert buys[0].trigger_reason == "REBALANCE_BUY"
    # current value $100, target $200 → shortfall $100
    assert abs(buys[0].amount_usd - 100.0) < 0.01


def test_no_rebalance_buy_when_overweight():
    """Held more than target → no REBALANCE_BUY emitted (target stays in dict)."""
    from auto_trader.allocator.delta_engine import compute_delta

    target = {
        "AAPL": {"ticker": "AAPL", "composite_score": 0.8, "allocation_usd": 100, "sector": "Tech"}
    }
    current = [
        {
            "ticker": "AAPL",
            "shares": 5.0,
            "current_price": 50.0,  # value = $250 > target $100
            "stop_loss_price": 30.0,
            "last_score": 0.80,
            "sector": "Tech",
        }
    ]
    instr = compute_delta(target, current, 1000.0)
    assert all(i.action != "BUY" for i in instr)


def test_trade_instruction_validates_action():
    from auto_trader.allocator.delta_engine import TradeInstruction

    with pytest.raises(ValueError):
        TradeInstruction(ticker="X", action="HOLD", amount_usd=1, trigger_reason="NEW_BUY")


def test_trade_instruction_validates_trigger():
    from auto_trader.allocator.delta_engine import TradeInstruction

    with pytest.raises(ValueError):
        TradeInstruction(ticker="X", action="BUY", amount_usd=1, trigger_reason="MADE_UP")


# ---------------------------------------------------------------------------
# Signal filter
# ---------------------------------------------------------------------------
def test_signal_filter_keeps_top_n_per_sector():
    from auto_trader.allocator.signal_filter import filter_signals

    cache = {
        "regime": {"label": "bull", "confidence": 0.9},
        "sectors": {
            "Tech": [
                {"ticker": "AAA", "passed_veto": True, "composite_score": 0.95, "signal_scores": {}},
                {"ticker": "BBB", "passed_veto": True, "composite_score": 0.85, "signal_scores": {}},
                {"ticker": "CCC", "passed_veto": True, "composite_score": 0.75, "signal_scores": {}},
            ],
            "Health": [
                {"ticker": "DDD", "passed_veto": True, "composite_score": 0.90, "signal_scores": {}},
            ],
        },
    }
    eligible = filter_signals(cache)
    tickers = sorted(s["ticker"] for s in eligible)
    # Tech: top-2 of 3 → AAA, BBB. Health: just DDD. C below top-2 in Tech.
    assert tickers == ["AAA", "BBB", "DDD"]


def test_signal_filter_drops_below_min_score():
    from auto_trader.allocator.signal_filter import filter_signals

    cache = {
        "regime": {"label": "bull", "confidence": 0.9},
        "sectors": {
            "Tech": [
                {"ticker": "GOOD", "passed_veto": True, "composite_score": 0.65, "signal_scores": {}},
                {"ticker": "BAD",  "passed_veto": True, "composite_score": 0.50, "signal_scores": {}},  # < 0.60
            ],
        },
    }
    eligible = filter_signals(cache)
    assert {s["ticker"] for s in eligible} == {"GOOD"}


def test_signal_filter_drops_failed_veto():
    from auto_trader.allocator.signal_filter import filter_signals

    cache = {
        "regime": {"label": "sideways", "confidence": 0.8},
        "sectors": {
            "Tech": [
                {"ticker": "GOOD", "passed_veto": True, "composite_score": 0.7, "signal_scores": {}},
                {"ticker": "VETO", "passed_veto": False, "composite_score": 0.95, "signal_scores": {}},
            ],
        },
    }
    eligible = filter_signals(cache)
    assert {s["ticker"] for s in eligible} == {"GOOD"}


# ---------------------------------------------------------------------------
# Position sizer — basic shape + budget
# ---------------------------------------------------------------------------
def test_position_sizer_equal_mode(monkeypatch):
    monkeypatch.setattr("auto_trader.allocator.position_sizer.POSITION_SIZING_MODE", "equal")
    from auto_trader.allocator.position_sizer import compute_allocations

    eligible = [
        {"ticker": "A", "sector": "X", "composite_score": 0.7, "signal_scores": {}, "regime": "bull"},
        {"ticker": "B", "sector": "X", "composite_score": 0.7, "signal_scores": {}, "regime": "bull"},
    ]
    allocs = compute_allocations(eligible, portfolio_value=10_000.0, cash=1000.0)
    assert len(allocs) == 2
    # 90% deployment of $1000 = $900, split in 2 = $450 each (per-pos cap of 6%*10000=$600 doesn't bite)
    for a in allocs:
        assert abs(a["allocation_usd"] - 450.0) < 0.01


def test_position_sizer_caps_per_position(monkeypatch):
    """Per-position cap of 6% of $10k = $600 — even with one candidate."""
    monkeypatch.setattr("auto_trader.allocator.position_sizer.POSITION_SIZING_MODE", "equal")
    from auto_trader.allocator.position_sizer import compute_allocations

    eligible = [
        {"ticker": "A", "sector": "X", "composite_score": 0.7, "signal_scores": {}, "regime": "bull"},
    ]
    # Cash $10k, deployable 90% = $9k. Per-position cap 6% of $10k = $600.
    allocs = compute_allocations(eligible, portfolio_value=10_000.0, cash=10_000.0)
    assert len(allocs) == 1
    assert allocs[0]["allocation_usd"] <= 600.0
