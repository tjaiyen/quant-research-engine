"""Compute the ordered list of trade instructions to move from current → target.

Gate 9 invariant: **all SELL instructions precede all BUY instructions**.
This is required so the order_sequencer can confirm sells before placing
buys, and refresh cash between phases.

Trigger reasons (fixed enum):
  * ``STOP_LOSS``       — current_price <= stop_loss_price
  * ``SIGNAL_EXIT``     — last_score < SIGNAL_EXIT_THRESHOLD (0.45)
  * ``REBALANCE_SELL``  — position no longer in target
  * ``NEW_BUY``         — target ticker not currently held
  * ``REBALANCE_BUY``   — target ticker held but under-weight
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from auto_trader.config import SIGNAL_EXIT_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass
class TradeInstruction:
    """One pending trade. ``trigger_reason`` MUST be from the fixed enum."""

    ticker: str
    action: str                              # "SELL" | "BUY"
    amount_usd: float                        # always positive
    trigger_reason: str                      # see module docstring
    sector: str = "UNKNOWN"
    score: Optional[float] = None
    shares: Optional[float] = None           # filled in for SELLs
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in ("SELL", "BUY"):
            raise ValueError(f"Bad action {self.action!r}")
        valid_triggers = {
            "STOP_LOSS", "SIGNAL_EXIT", "REBALANCE_SELL",
            "NEW_BUY", "REBALANCE_BUY", "UNKNOWN",
        }
        if self.trigger_reason not in valid_triggers:
            raise ValueError(
                f"trigger_reason must be one of {valid_triggers}, "
                f"got {self.trigger_reason!r}"
            )
        if self.amount_usd < 0:
            raise ValueError(f"amount_usd must be non-negative, got {self.amount_usd}")


def compute_delta(
    target_portfolio: dict[str, dict],
    current_positions: list[dict],
    portfolio_value: float,  # noqa: ARG001 - reserved for future percentage-based logic
) -> list[TradeInstruction]:
    """Return ordered list of TradeInstruction; SELLs come before BUYs.

    Args:
        target_portfolio: dict from ``target_builder.build_target_portfolio``.
        current_positions: list of position dicts from broker /
            ``state.portfolio_db.get_all_positions``.
        portfolio_value: total broker portfolio value.

    Returns:
        Ordered list — first all SELL instructions (stop-loss + signal-exit
        first within sells, then rebalance-sells), then all BUY
        instructions (new buys first, then rebalance-buys).
    """
    sells: list[TradeInstruction] = []
    buys: list[TradeInstruction] = []

    target_tickers = set(target_portfolio.keys())
    pos_by_ticker = {p["ticker"]: p for p in current_positions}

    # ── SELLs ───────────────────────────────────────────────────────────────
    # Order within sells: STOP_LOSS first, then SIGNAL_EXIT, then REBALANCE_SELL
    stop_sells: list[TradeInstruction] = []
    exit_sells: list[TradeInstruction] = []
    rebal_sells: list[TradeInstruction] = []

    for ticker, pos in pos_by_ticker.items():
        shares = float(pos.get("shares", 0))
        if shares <= 0:
            continue
        current_price = float(pos.get("current_price") or pos.get("cost_basis") or 0)
        market_value = shares * current_price
        last_score = pos.get("last_score")
        stop = float(pos.get("stop_loss_price") or 0)

        # Stop-loss trigger
        if current_price > 0 and stop > 0 and current_price <= stop:
            stop_sells.append(
                TradeInstruction(
                    ticker=ticker,
                    action="SELL",
                    amount_usd=market_value,
                    shares=shares,
                    trigger_reason="STOP_LOSS",
                    sector=pos.get("sector", "UNKNOWN"),
                    score=last_score,
                    metadata={"current_price": current_price, "stop": stop},
                )
            )
            continue

        # Signal-exit trigger
        if last_score is not None and float(last_score) < SIGNAL_EXIT_THRESHOLD:
            exit_sells.append(
                TradeInstruction(
                    ticker=ticker,
                    action="SELL",
                    amount_usd=market_value,
                    shares=shares,
                    trigger_reason="SIGNAL_EXIT",
                    sector=pos.get("sector", "UNKNOWN"),
                    score=last_score,
                    metadata={"current_price": current_price},
                )
            )
            continue

        # Rebalance: held but no longer in target
        if ticker not in target_tickers:
            rebal_sells.append(
                TradeInstruction(
                    ticker=ticker,
                    action="SELL",
                    amount_usd=market_value,
                    shares=shares,
                    trigger_reason="REBALANCE_SELL",
                    sector=pos.get("sector", "UNKNOWN"),
                    score=last_score,
                    metadata={"current_price": current_price},
                )
            )

    sells = stop_sells + exit_sells + rebal_sells

    # ── BUYs ────────────────────────────────────────────────────────────────
    # Order within buys: NEW_BUY first, then REBALANCE_BUY (top up under-weights)
    new_buys: list[TradeInstruction] = []
    rebal_buys: list[TradeInstruction] = []

    for ticker, target in target_portfolio.items():
        target_alloc = float(target.get("allocation_usd", 0.0))
        if target_alloc <= 0:
            continue

        held = pos_by_ticker.get(ticker)
        if held is None:
            new_buys.append(
                TradeInstruction(
                    ticker=ticker,
                    action="BUY",
                    amount_usd=target_alloc,
                    trigger_reason="NEW_BUY",
                    sector=target.get("sector", "UNKNOWN"),
                    score=float(target.get("composite_score", 0.0)),
                )
            )
        else:
            shares = float(held.get("shares", 0))
            current_price = float(
                held.get("current_price") or held.get("cost_basis") or 0
            )
            current_value = shares * current_price
            shortfall = target_alloc - current_value
            if shortfall > 0:
                rebal_buys.append(
                    TradeInstruction(
                        ticker=ticker,
                        action="BUY",
                        amount_usd=shortfall,
                        trigger_reason="REBALANCE_BUY",
                        sector=target.get("sector", "UNKNOWN"),
                        score=float(target.get("composite_score", 0.0)),
                        metadata={
                            "current_value": current_value,
                            "target_value": target_alloc,
                        },
                    )
                )

    buys = new_buys + rebal_buys

    instructions = sells + buys

    logger.info(
        "Delta: %d sells (%d stop, %d exit, %d rebalance), %d buys (%d new, %d rebalance)",
        len(sells), len(stop_sells), len(exit_sells), len(rebal_sells),
        len(buys), len(new_buys), len(rebal_buys),
    )
    return instructions


__all__ = ["TradeInstruction", "compute_delta"]
