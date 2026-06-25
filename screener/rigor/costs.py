"""Transaction-cost stress (U27) — a turnover × round-trip-bps haircut.

The tournament/U4 sims are frictionless: each rebalance silently swaps holdings
at zero cost. Real quarterly rebalancing trades a fraction of the book every
period, and commission+spread+slippage eats into the gross return. This applies
the institutional brief's "Why Your Backtest Lies" correction at the only
fidelity a daily-data paper tool can justify: a parametric per-rebalance haircut
of `turnover × cost_bps`, NOT a microstructure/LOB sim (that is the declined
institutional path, U30).

`turnover` = fraction of the new book that is *newly bought* this rebalance — so
the cost is priced round-trip (the matching sells of dropped names are implicit
in the buys that replace them). A first rebalance buys everything (turnover 1.0
→ full cost); a buy-and-hold control changes nothing (turnover 0 → no cost); a
high-churn variant (e.g. random-20 each period) pays close to the full haircut
every rebalance. That is exactly the differential the brief warns about.
"""
from __future__ import annotations


def turnover(prev_holds, new_holds) -> float:
    """Fraction of `new_holds` that was not in `prev_holds` (∈ [0, 1])."""
    new = set(new_holds or [])
    if not new:
        return 0.0
    prev = set(prev_holds or [])
    return len(new - prev) / len(new)


def cost_haircut(prev_holds, new_holds, cost_bps: float) -> float:
    """Return the per-rebalance cost drag (a return decrement) for this trade.

    `cost_bps` is the round-trip cost in basis points (commission + half-spread
    + slippage, both legs). 0 ⇒ no haircut (frictionless, the legacy behaviour).
    """
    if not cost_bps:
        return 0.0
    return turnover(prev_holds, new_holds) * (cost_bps / 10_000.0)


__all__ = ["turnover", "cost_haircut"]
