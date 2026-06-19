"""CLI for portfolio holdings.

Usage:
    python -m tasks.manage_holdings list
    python -m tasks.manage_holdings add AAPL 50 180.00 [--opened 2024-03-15]
    python -m tasks.manage_holdings remove AAPL
    python -m tasks.manage_holdings seed          # demo portfolio across watchlist

'add' upserts — calling again replaces shares/cost_basis for that ticker.
"""
from __future__ import annotations

import argparse
import sys

from utils.db import (
    delete_holding,
    init_db,
    list_holdings,
    list_tickers,
    upsert_holding,
)
from utils.logging_setup import get_logger

log = get_logger(__name__)


# Demo portfolio — roughly balanced across the default watchlist.
# Cost basis is illustrative only; edit via `add` when you have real data.
_DEMO = [
    ("AAPL", 25, 175.00),
    ("MSFT", 15, 380.00),
    ("NVDA", 10, 650.00),
    ("SPY", 30, 480.00),
    ("QQQ", 20, 420.00),
]


def _print_holdings() -> None:
    df = list_holdings()
    if df.empty:
        print("No holdings. Add one: python -m tasks.manage_holdings add TICKER SHARES COST")
        return
    print(df.to_string(index=False))


def cmd_list(_args) -> int:
    _print_holdings()
    return 0


def cmd_add(args) -> int:
    upsert_holding(args.ticker, args.shares, args.cost_basis, args.opened)
    log.info(
        "Upserted %s: %s shares @ $%.2f", args.ticker.upper(), args.shares, args.cost_basis
    )
    _print_holdings()
    return 0


def cmd_remove(args) -> int:
    n = delete_holding(args.ticker)
    if n == 0:
        log.warning("No holding found for %s", args.ticker.upper())
        return 1
    log.info("Removed %s", args.ticker.upper())
    _print_holdings()
    return 0


def cmd_seed(_args) -> int:
    for t, shares, cb in _DEMO:
        upsert_holding(t, shares, cb)
    log.info("Seeded %d demo holdings", len(_DEMO))
    _print_holdings()
    return 0


def main(argv: list[str] | None = None) -> int:
    init_db()
    p = argparse.ArgumentParser(description="Manage portfolio holdings.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="Add or update a holding.")
    p_add.add_argument("ticker")
    p_add.add_argument("shares", type=float)
    p_add.add_argument("cost_basis", type=float)
    p_add.add_argument("--opened", help="ISO date, e.g. 2024-03-15")
    p_add.set_defaults(func=cmd_add)

    p_rm = sub.add_parser("remove", help="Remove a holding.")
    p_rm.add_argument("ticker")
    p_rm.set_defaults(func=cmd_remove)

    sub.add_parser("seed", help="Seed a demo portfolio across the watchlist.").set_defaults(
        func=cmd_seed
    )

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
