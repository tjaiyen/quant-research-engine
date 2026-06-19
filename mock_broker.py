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

from typing import Optional

MOCK_PRICE: float = 100.0


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
    """In-memory broker. Single-process, single-instance — fine for tests + paper."""

    def __init__(self, cash: float = 10_000.0) -> None:
        self._cash: float = float(cash)
        # ticker → {"qty": float, "cost": float}
        self._positions: dict[str, dict[str, float]] = {}
        self._orders: dict[str, MockOrder] = {}
        self._order_seq: int = 0

    # ── Account / Clock / Calendar ────────────────────────────────────────
    def get_account(self) -> _MockAccount:
        a = _MockAccount()
        invested = sum(p["qty"] * MOCK_PRICE for p in self._positions.values())
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
            p = _MockPosition()
            p.symbol = symbol
            p.qty = str(pos["qty"])
            p.current_price = str(MOCK_PRICE)
            p.avg_entry_price = str(pos["cost"])
            p.market_value = str(pos["qty"] * MOCK_PRICE)
            p.unrealized_pl = str((MOCK_PRICE - pos["cost"]) * pos["qty"])
            p.unrealized_plpc = str(
                (MOCK_PRICE - pos["cost"]) / max(pos["cost"], 1e-6)
            )
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
        if notional is not None:
            shares = float(notional) / MOCK_PRICE
        elif qty is not None:
            shares = float(qty)
        else:
            shares = 0.0

        if side == "buy":
            # C4: WACC accumulation
            if symbol in self._positions:
                old = self._positions[symbol]
                total_cost = old["qty"] * old["cost"] + shares * MOCK_PRICE
                total_shares = old["qty"] + shares
                self._positions[symbol] = {
                    "qty": total_shares,
                    "cost": total_cost / total_shares if total_shares else MOCK_PRICE,
                }
            else:
                self._positions[symbol] = {"qty": shares, "cost": MOCK_PRICE}
            self._cash -= shares * MOCK_PRICE

        elif side == "sell" and symbol in self._positions:
            existing = self._positions[symbol]["qty"]
            actual = min(shares, existing)
            remaining = existing - actual
            self._cash += actual * MOCK_PRICE
            if remaining <= 0:
                del self._positions[symbol]
            else:
                self._positions[symbol]["qty"] = remaining

        order = MockOrder(symbol, side, oid, qty=qty, notional=notional)
        order.time_in_force = time_in_force
        self._orders[oid] = order
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


__all__ = ["MockAlpacaClient", "MockOrder", "MOCK_PRICE"]
