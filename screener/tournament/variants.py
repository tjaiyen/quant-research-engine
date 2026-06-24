"""The strategy variants that compete — plus the control (null-hypothesis) set.

Every variant is a cheap spec the runner interprets over the cached panel:
re-weight the 5 signals, pick top/bottom-N per sector, size equal/score-weighted,
with guards on/off. Controls (SPY, the whole universe, random-20) are the honest
benchmarks — if the clever variants can't beat *random*, that is the finding.
"""
from __future__ import annotations

SIGNALS = ("arima", "kalman", "garch", "monte_carlo", "sharpe")


def _strat(label, group, weights=None, guards=True, top_n=2,
           sizing="equal", pick="top") -> dict:
    return {"label": label, "group": group, "kind": "strategy",
            "weights": weights, "guards": guards, "top_n": top_n,
            "sizing": sizing, "pick": pick}


def _control(label, control) -> dict:
    return {"label": label, "group": "control", "kind": "control", "control": control}


def default_variants() -> list[dict]:
    eq = {s: 1.0 for s in SIGNALS}
    return [
        # ── controls / null hypotheses ──────────────────────────────────────
        _control("SPY buy-hold", "spy"),
        _control("Equal-weight universe", "universe"),
        _control("Random 20 (seed)", "random"),

        # ── signal-weighting (guards on, top-2/sector, equal-weight) ────────
        _strat("Regime-blended (default)", "weighting", weights=None),
        _strat("Equal 5 signals", "weighting", weights=eq),
        _strat("Pure Sharpe", "weighting", weights={"sharpe": 1.0}),
        _strat("Pure Monte-Carlo", "weighting", weights={"monte_carlo": 1.0}),
        _strat("Pure ARIMA", "weighting", weights={"arima": 1.0}),
        _strat("Pure Kalman", "weighting", weights={"kalman": 1.0}),
        _strat("Pure GARCH", "weighting", weights={"garch": 1.0}),
        _strat("Trend (ARIMA+Kalman)", "weighting", weights={"arima": 1.0, "kalman": 1.0}),
        _strat("Risk (Sharpe+MC+GARCH)", "weighting",
               weights={"sharpe": 1.0, "monte_carlo": 1.0, "garch": 1.0}),

        # ── concentration (default weighting) ───────────────────────────────
        _strat("Top-1 per sector", "concentration", top_n=1),
        _strat("Top-3 per sector", "concentration", top_n=3),
        _strat("Top-5 per sector", "concentration", top_n=5),

        # ── sizing ──────────────────────────────────────────────────────────
        _strat("Score-weighted sizing", "sizing", sizing="score"),

        # ── veto policy ─────────────────────────────────────────────────────
        _strat("Guards off", "veto", guards=False),

        # ── diagnostics ─────────────────────────────────────────────────────
        # If the WORST-ranked names don't underperform the best, the ranking
        # has no signal — a built-in sanity check on the engine itself.
        _strat("Worst-ranked (inverse)", "diagnostic", top_n=2, pick="bottom"),
        _strat("High conviction (top-1, score)", "diagnostic", top_n=1, sizing="score"),
    ]


__all__ = ["default_variants", "SIGNALS"]
