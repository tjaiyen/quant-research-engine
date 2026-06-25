"""C4: in-memory mock Alpaca client for offline / test runs.

Used as the default broker during the auto_trader build (the user opted
for ``ALPACA_USE_MOCK=true`` until real paper keys are wired in). Same
surface as ``alpaca_trade_api.REST`` for the methods the auto_trader
exercises:

  * ``get_account()`` → object with .cash, .portfolio_value, .buying_power, .status
  * ``get_clock()`` → object with .is_open
  * ``get_calendar(start, end)`` → list (truthy ⇒ trading day)
  * ``list_positions()`` → list of position-like objects
  * ``submit_order(...)`` → ``MockOrder``
  * ``get_order(order_id)``
  * ``cancel_order(order_id)``
  * ``cancel_all_orders()``
  * ``list_orders(status='open')``

Position accounting:
  * BUY adds to existing position with **WACC** (weighted-avg cost)
  * SELL is partial-aware — leaves remaining shares in place rather
    than deleting the record outright (Gate 14)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MOCK_PRICE: float = 100.0


def _market_price(symbol: str) -> float | None:
    """Latest real close for `symbol` from the cached price history, or None.

    Lets the file-backed paper account mark-to-market against real prices instead
    of the flat MOCK_PRICE placeholder. Guarded + lazy so the module stays
    importable (and in-memory unit tests stay offline) when no price DB exists.
    """
    try:
        from utils.db import fetch_prices
        df = fetch_prices(symbol)
        if df is not None and not df.empty and "adj_close" in df.columns:
            v = float(df["adj_close"].iloc[-1])
            return v if v > 0 else None
    except Exception:
        return None
    return None


class MockOrder:
    def __init__(
        self,
        symbol: str,
        side: str,
        oid: str,
        qty: Optional[float] = None,
        notional: Optional[float] = None,
    ) -> None:
        self.id = oid
        self.symbol = symbol
        self.side = side
        self.status = "filled"
        if notional is not None:
            shares = float(notional) / MOCK_PRICE
        elif qty is not None:
            shares = float(qty)
        else:
            shares = 0.0
        self.qty = str(shares)
        self.notional = str(notional) if notional is not None else None
        self.filled_qty = str(shares)
        self.filled_avg_price = str(MOCK_PRICE)
        self.time_in_force = "day"


class _MockAccount:
    status: str = "ACTIVE"
    pattern_day_trader: bool = False
    trading_blocked: bool = False
    cash: str = "0"
    portfolio_value: str = "0"
    buying_power: str = "0"


class _MockClock:
    is_open: bool = True


class _MockPosition:
    symbol: str = ""
    qty: str = "0"
    current_price: str = "0"
    avg_entry_price: str = "0"
    market_value: str = "0"
    unrealized_pl: str = "0"
    unrealized_plpc: str = "0"


class MockAlpacaClient:
    """Mock broker. Optionally file-backed so paper state survives across the
    separate processes of the autonomous loop (cycle → monitor → report each run
    in their own `track` process). Without ``state_path`` it's pure in-memory —
    that's what unit tests construct, so they stay isolated.
    """

    def __init__(self, cash: float = 10_000.0,
                 state_path: str | Path | None = None,
                 mark_to_market: bool = False) -> None:
        self._cash: float = float(cash)
        # ticker → {"qty": float, "cost": float}
        self._positions: dict[str, dict[str, float]] = {}
        self._orders: dict[str, MockOrder] = {}
        self._order_seq: int = 0
        self._state_path: Path | None = Path(state_path) if state_path else None
        # Mark fills/positions at real cached prices (live account); default OFF
        # keeps the legacy flat-MOCK_PRICE behaviour for unit tests + persistence.
        self._mtm: bool = bool(mark_to_market)
        if self._state_path:
            self._load()

    # ── Persistence (file-backed paper account) ───────────────────────────
    def _load(self) -> None:
        try:
            if self._state_path and self._state_path.exists():
                d = json.loads(self._state_path.read_text())
                self._cash = float(d.get("cash", self._cash))
                self._positions = {
                    k: {"qty": float(v["qty"]), "cost": float(v["cost"])}
                    for k, v in (d.get("positions") or {}).items()
                }
                self._order_seq = int(d.get("order_seq", 0))
        except Exception as exc:           # corrupt/unreadable → keep defaults
            logger.warning("mock broker state load failed (%s); starting fresh", exc)

    def _save(self) -> None:
        if not self._state_path:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "cash": self._cash, "positions": self._positions,
                "order_seq": self._order_seq,
            }))
            tmp.replace(self._state_path)   # atomic
        except Exception as exc:
            logger.warning("mock broker state save failed (%s)", exc)

    # ── Mark-to-market (live file-backed account only) ────────────────────
    def _mark(self, symbol: str, fallback: float) -> float:
        """Real last-close price for the LIVE account; `fallback` otherwise.

        Returns `fallback` unless mark-to-market is enabled (the live account),
        preserving the legacy flat-MOCK_PRICE behaviour for unit tests.
        """
        if not self._mtm:
            return fallback
        return _market_price(symbol) or fallback

    # ── Account / Clock / Calendar ────────────────────────────────────────
    def get_account(self) -> _MockAccount:
        a = _MockAccount()
        invested = sum(p["qty"] * self._mark(s, p["cost"])
                       for s, p in self._positions.items())
        a.cash = str(self._cash)
        a.portfolio_value = str(self._cash + invested)
        a.buying_power = str(self._cash)
        return a

    def get_clock(self) -> _MockClock:
        return _MockClock()

    def get_calendar(self, start, end) -> list:  # noqa: ARG002
        return [True]

    # ── Positions ─────────────────────────────────────────────────────────
    def list_positions(self) -> list[_MockPosition]:
        result: list[_MockPosition] = []
        for symbol, pos in self._positions.items():
            cp = self._mark(symbol, pos["cost"])   # real close, or cost ⇒ P&L 0
            p = _MockPosition()
            p.symbol = symbol
            p.qty = str(pos["qty"])
            p.current_price = str(cp)
            p.avg_entry_price = str(pos["cost"])
            p.market_value = str(pos["qty"] * cp)
            p.unrealized_pl = str((cp - pos["cost"]) * pos["qty"])
            p.unrealized_plpc = str((cp - pos["cost"]) / max(pos["cost"], 1e-6))
            result.append(p)
        return result

    # ── Orders ────────────────────────────────────────────────────────────
    def submit_order(
        self,
        symbol: str,
        side: str,
        type: str = "market",  # noqa: A002 - mirrors Alpaca SDK
        time_in_force: str = "day",
        qty: Optional[float] = None,
        notional: Optional[float] = None,
        **kwargs,
    ) -> MockOrder:
        self._order_seq += 1
        oid = f"mock-{self._order_seq}"
        fill = self._mark(symbol, MOCK_PRICE)   # real fill price for the live account
        if notional is not None:
            shares = float(notional) / fill
        elif qty is not None:
            shares = float(qty)
        else:
            shares = 0.0

        if side == "buy":
            # C4: WACC accumulation
            if symbol in self._positions:
                old = self._positions[symbol]
                total_cost = old["qty"] * old["cost"] + shares * fill
                total_shares = old["qty"] + shares
                self._positions[symbol] = {
                    "qty": total_shares,
                    "cost": total_cost / total_shares if total_shares else fill,
                }
            else:
                self._positions[symbol] = {"qty": shares, "cost": fill}
            self._cash -= shares * fill

        elif side == "sell" and symbol in self._positions:
            existing = self._positions[symbol]["qty"]
            actual = min(shares, existing)
            remaining = existing - actual
            self._cash += actual * fill
            if remaining <= 0:
                del self._positions[symbol]
            else:
                self._positions[symbol]["qty"] = remaining

        order = MockOrder(symbol, side, oid, qty=qty, notional=notional)
        order.time_in_force = time_in_force
        self._orders[oid] = order
        self._save()   # persist the new holdings/cash so the next process sees them
        return order

    def get_order(self, order_id: str) -> MockOrder:
        if order_id in self._orders:
            return self._orders[order_id]
        # Synthesize a minimal record for unknown IDs so callers don't crash.
        return MockOrder("UNK", "buy", order_id)

    def cancel_order(self, order_id: str) -> None:
        self._orders.pop(order_id, None)

    def cancel_all_orders(self) -> None:
        self._orders.clear()

    def list_orders(self, status: str = "open", **_: object) -> list[MockOrder]:  # noqa: ARG002
        return []


def repair_to_real_entry(state_path: str | Path) -> list[dict]:
    """One-time repair: rewrite legacy flat-$100 mock fills to REAL entry prices
    from the cached price history, **preserving the dollars invested** per
    position (new_shares = dollars / real_entry, new_cost = real_entry).

    Idempotent — skips any position whose cost is no longer the $100 placeholder,
    and any ticker with no cached price at its entry date (left untouched).
    Returns the list of repaired positions for reporting.
    """
    path = Path(state_path)
    if not path.exists():
        return []
    d = json.loads(path.read_text())
    positions = d.get("positions") or {}
    try:
        from auto_trader.state.portfolio_db import get_all_positions
        from utils.db import price_on_or_before
    except Exception as exc:
        logger.warning("repair: deps unavailable (%s)", exc)
        return []
    entry = {p["ticker"]: p.get("entry_date") for p in get_all_positions()}
    changed = []
    for ticker, pos in positions.items():
        if abs(float(pos["cost"]) - MOCK_PRICE) > 1e-6:
            continue                                  # already real
        ed = entry.get(ticker)
        real = price_on_or_before(ticker, ed) if ed else None
        if not real or real <= 0:
            logger.info("repair: no cached entry price for %s (%s) — skipped", ticker, ed)
            continue
        dollars = float(pos["qty"]) * float(pos["cost"])
        pos["qty"] = dollars / real
        pos["cost"] = float(real)
        changed.append({"ticker": ticker, "entry_date": ed,
                        "entry_price": float(real), "shares": pos["qty"],
                        "dollars": dollars})
    if changed:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(path)
    return changed


__all__ = ["MockAlpacaClient", "MockOrder", "MOCK_PRICE", "repair_to_real_entry"]
