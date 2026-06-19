"""Convert ``TradeInstruction`` into ``submit_order()`` kwargs.

Sells use ``SELL_TIME_IN_FORCE='day'`` and pass ``shares`` (qty).
Buys use ``BUY_TIME_IN_FORCE='opg'`` (Market-on-Open) and pass ``notional``
(USD amount, fractional shares). The two pathways must NEVER collide
(``H6`` mutual exclusion is enforced by ``order_executor.submit_order``).
"""
from __future__ import annotations

from auto_trader.allocator.delta_engine import TradeInstruction
from auto_trader.config import BUY_TIME_IN_FORCE, SELL_TIME_IN_FORCE


def to_submit_kwargs(instruction: TradeInstruction) -> dict:
    """Build the kwargs dict for ``broker.order_executor.submit_order``."""
    common = {
        "ticker": instruction.ticker,
        "side": instruction.action.lower(),
        "trigger_reason": instruction.trigger_reason,
        "score": instruction.score,
    }
    if instruction.action == "SELL":
        return {
            **common,
            "shares": float(instruction.shares or 0.0),
            "time_in_force": SELL_TIME_IN_FORCE,
        }
    # BUY
    return {
        **common,
        "notional": float(instruction.amount_usd),
        "time_in_force": BUY_TIME_IN_FORCE,
    }


__all__ = ["to_submit_kwargs"]
