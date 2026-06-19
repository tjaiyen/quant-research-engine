"""8-guard pre-execution risk pipeline.

Order matters. Each guard takes the in-flight ``list[TradeInstruction]``
and returns a (possibly trimmed / amount-clipped) version. By the time
the list reaches ``order_sequencer``, all 8 must have approved.

Guards:
  1. Halt + drawdown circuit
  2. Bear-regime new-buy block (when confidence > BEAR_REGIME_CONFIDENCE_HALT)
  3. Cash-reserve floor (M3 — uses live ``buying_power``)
  4. Single-position cap (clip to MAX_SINGLE_STOCK_PCT * portfolio_value)
  5. Sector exposure cap (drop buys that would exceed MAX_SECTOR_PCT)
  6. Max-order-size clip (per MAX_ORDER_SIZE_USD)
  7. Min composite-score (drop BUYs below MIN_COMPOSITE_TO_BUY)
  8. Liquidity check (drop BUYs whose notional > MAX_ADV_PCT × ADV)
     (H8: yfinance MultiIndex single-ticker safe)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable

from auto_trader.allocator.delta_engine import TradeInstruction
from auto_trader.config import (
    BEAR_REGIME_CONFIDENCE_HALT,
    CASH_RESERVE_PCT,
    MAX_ADV_PCT,
    MAX_ORDER_SIZE_USD,
    MAX_POSITIONS,
    MAX_SECTOR_PCT,
    MAX_SINGLE_STOCK_PCT,
    MIN_COMPOSITE_TO_BUY,
    MIN_POSITION_VALUE_USD,
)

logger = logging.getLogger(__name__)


def run_all_guards(
    instructions: list[TradeInstruction],
    current_positions: list[dict],
    portfolio_value: float,
    available_cash: float,
    regime_data: dict,
) -> list[TradeInstruction]:
    """Run all 8 guards in order and return the approved list."""
    from auto_trader.state.portfolio_db import log_system_event

    n_in = len(instructions)
    logger.info("Risk guards: %d instructions entering", n_in)

    instructions = _guard_1_halt_and_drawdown(instructions, portfolio_value)
    instructions = _guard_2_bear_regime(instructions, regime_data)
    instructions = _guard_3_cash_reserve(instructions, available_cash, portfolio_value)
    instructions = _guard_4_single_position(instructions, portfolio_value)
    instructions = _guard_5_sector_exposure(instructions, current_positions, portfolio_value)
    instructions = _guard_6_max_order_size(instructions)
    instructions = _guard_7_min_score(instructions)
    instructions = _guard_8_liquidity_batch(instructions)
    instructions = _drop_dust(instructions)

    n_out = len(instructions)
    logger.info("Risk guards: %d/%d instructions passed", n_out, n_in)
    log_system_event(
        "RISK_GUARD_COMPLETE",
        f"{n_out} instructions approved",
        {
            "n_in": n_in,
            "n_out": n_out,
            "sells": sum(1 for i in instructions if i.action == "SELL"),
            "buys": sum(1 for i in instructions if i.action == "BUY"),
        },
    )
    return instructions


# ── Individual guards ──────────────────────────────────────────────────────


def _guard_1_halt_and_drawdown(
    instructions: list[TradeInstruction], portfolio_value: float,
) -> list[TradeInstruction]:
    from auto_trader.credentials import is_halted
    from auto_trader.risk.drawdown_circuit import is_halted as drawdown_halted

    if is_halted():
        logger.error("GUARD 1: HALT FLAG — blocking all buys")
        return [i for i in instructions if i.action == "SELL"]
    if drawdown_halted(portfolio_value):
        logger.warning("GUARD 1: DRAWDOWN CIRCUIT — blocking all buys")
        return [i for i in instructions if i.action == "SELL"]
    return instructions


def _guard_2_bear_regime(
    instructions: list[TradeInstruction], regime_data: dict,
) -> list[TradeInstruction]:
    regime = regime_data.get("regime", "unknown")
    confidence = float(regime_data.get("confidence", 0.0))
    if regime == "bear" and confidence > BEAR_REGIME_CONFIDENCE_HALT:
        logger.warning(
            "GUARD 2: BEAR (%.1f%%) — blocking NEW_BUY", confidence * 100,
        )
        return [
            i for i in instructions
            if not (i.action == "BUY" and i.trigger_reason == "NEW_BUY")
        ]
    return instructions


def _guard_3_cash_reserve(
    instructions: list[TradeInstruction],
    available_cash: float,
    portfolio_value: float,
) -> list[TradeInstruction]:
    """M3: try live buying_power; fall back to passed estimate."""
    try:
        from auto_trader.broker.alpaca_client import get_account_info

        acct = get_account_info()
        effective_cash = float(acct["buying_power"])
        logger.debug("GUARD 3: live buying_power=$%.2f", effective_cash)
    except Exception as exc:
        logger.warning(
            "GUARD 3: buying_power fetch failed (%s); using estimate", exc,
        )
        effective_cash = available_cash

    reserve_req = portfolio_value * CASH_RESERVE_PCT
    total_sells = sum(i.amount_usd for i in instructions if i.action == "SELL")
    total_buys = sum(i.amount_usd for i in instructions if i.action == "BUY")
    proj_cash = effective_cash + total_sells - total_buys

    if proj_cash < reserve_req:
        shortfall = reserve_req - proj_cash
        logger.warning("GUARD 3: CASH RESERVE shortfall $%.2f", shortfall)
        # Trim BUYs in ascending score order (cheapest signal goes first)
        buys = sorted(
            [i for i in instructions if i.action == "BUY"],
            key=lambda x: x.score or 0,
        )
        remaining = shortfall
        for buy in buys:
            if remaining <= 0:
                break
            trim = min(remaining, buy.amount_usd)
            buy.amount_usd -= trim
            remaining -= trim

    return instructions


def _guard_4_single_position(
    instructions: list[TradeInstruction], portfolio_value: float,
) -> list[TradeInstruction]:
    max_single = portfolio_value * MAX_SINGLE_STOCK_PCT
    for i in instructions:
        if i.action == "BUY" and i.amount_usd > max_single:
            logger.debug(
                "GUARD 4: %s clipped $%.2f → $%.2f (single-pos cap)",
                i.ticker, i.amount_usd, max_single,
            )
            i.amount_usd = max_single
    return instructions


def _guard_5_sector_exposure(
    instructions: list[TradeInstruction],
    current_positions: list[dict],
    portfolio_value: float,
) -> list[TradeInstruction]:
    sector_val: dict[str, float] = defaultdict(float)
    for p in current_positions:
        sec = p.get("sector")
        if sec:
            cp = float(p.get("current_price") or p.get("cost_basis") or 0)
            sector_val[sec] += float(p["shares"]) * cp

    max_sector = portfolio_value * MAX_SECTOR_PCT
    to_remove: set[str] = set()
    by_sector: dict[str, list[TradeInstruction]] = defaultdict(list)
    for i in instructions:
        if i.action == "BUY":
            by_sector[i.sector].append(i)

    for sector, buys in by_sector.items():
        buys.sort(key=lambda x: x.score or 0, reverse=True)  # best score first
        running = sector_val.get(sector, 0.0)
        for buy in buys:
            if running + buy.amount_usd > max_sector:
                logger.warning(
                    "GUARD 5: %s sector limit reached — dropping %s",
                    sector, buy.ticker,
                )
                to_remove.add(buy.ticker)
            else:
                running += buy.amount_usd

    return [i for i in instructions if i.ticker not in to_remove]


def _guard_6_max_order_size(
    instructions: list[TradeInstruction],
) -> list[TradeInstruction]:
    for i in instructions:
        if i.amount_usd > MAX_ORDER_SIZE_USD:
            logger.debug(
                "GUARD 6: %s clipped $%.2f → $%.2f (max order)",
                i.ticker, i.amount_usd, MAX_ORDER_SIZE_USD,
            )
            i.amount_usd = MAX_ORDER_SIZE_USD
    return instructions


def _guard_7_min_score(
    instructions: list[TradeInstruction],
) -> list[TradeInstruction]:
    keep: list[TradeInstruction] = []
    for i in instructions:
        if i.action == "BUY" and (i.score is not None) and i.score < MIN_COMPOSITE_TO_BUY:
            logger.warning(
                "GUARD 7: %s score=%.2f < %.2f — dropping",
                i.ticker, i.score, MIN_COMPOSITE_TO_BUY,
            )
            continue
        keep.append(i)
    return keep


def _guard_8_liquidity_batch(
    instructions: list[TradeInstruction],
) -> list[TradeInstruction]:
    """Drop BUYs whose notional > MAX_ADV_PCT × 20-day average daily volume.

    H8: handle yfinance's MultiIndex single-ticker quirk.
    """
    buy_tickers = sorted({i.ticker for i in instructions if i.action == "BUY"})
    if not buy_tickers:
        return instructions

    try:
        import pandas as pd
        import yfinance as yf
    except Exception:
        logger.debug("GUARD 8: yfinance unavailable — skipping liquidity check")
        return instructions

    # Smart-reuse: cockpit prices first
    adv_by_ticker: dict[str, float] = {}
    try:
        from utils.db import fetch_prices

        for t in buy_tickers:
            df = fetch_prices(t)
            if df is None or df.empty or "volume" not in df.columns or "adj_close" not in df.columns:
                continue
            tail = df.tail(20)
            if len(tail) < 5:
                continue
            adv_by_ticker[t] = float(
                (tail["volume"] * tail["adj_close"]).mean()
            )
    except Exception as exc:
        logger.debug("GUARD 8: cockpit prices fetch failed (%s)", exc)

    # yfinance fallback for any misses
    missing = [t for t in buy_tickers if t not in adv_by_ticker]
    if missing:
        try:
            data = yf.download(
                missing if len(missing) > 1 else missing[0],
                period="1mo",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )
            if data is not None and not data.empty:
                # H8: single ticker → flat columns; multiple → MultiIndex
                if isinstance(data.columns, pd.MultiIndex):
                    for t in missing:
                        try:
                            df = data.xs(t, level=1, axis=1)
                        except KeyError:
                            try:
                                df = data.xs(t, level=0, axis=1)
                            except KeyError:
                                continue
                        if "Close" in df.columns and "Volume" in df.columns:
                            tail = df.tail(20)
                            if len(tail) >= 5:
                                adv_by_ticker[t] = float(
                                    (tail["Volume"] * tail["Close"]).mean()
                                )
                else:
                    # Single-ticker case
                    t = missing[0]
                    if "Close" in data.columns and "Volume" in data.columns:
                        tail = data.tail(20)
                        if len(tail) >= 5:
                            adv_by_ticker[t] = float(
                                (tail["Volume"] * tail["Close"]).mean()
                            )
        except Exception as exc:
            logger.debug("GUARD 8: yfinance fallback failed (%s)", exc)

    keep: list[TradeInstruction] = []
    for i in instructions:
        if i.action == "BUY":
            adv = adv_by_ticker.get(i.ticker)
            if adv is not None and adv > 0:
                cap = adv * MAX_ADV_PCT
                if i.amount_usd > cap:
                    logger.warning(
                        "GUARD 8: %s notional $%.2f > %.2f%% × ADV $%.2f — dropping",
                        i.ticker, i.amount_usd, MAX_ADV_PCT * 100, cap,
                    )
                    continue
        keep.append(i)
    return keep


def _drop_dust(instructions: list[TradeInstruction]) -> list[TradeInstruction]:
    """Remove instructions whose amount is below the minimum position floor."""
    return [
        i for i in instructions
        if i.action == "SELL" or i.amount_usd >= MIN_POSITION_VALUE_USD
    ]


__all__ = ["run_all_guards"]
