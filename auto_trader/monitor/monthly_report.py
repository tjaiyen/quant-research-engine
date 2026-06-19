"""Compose the monthly performance report.

M4: includes a risk snapshot section. M8: number formatters tolerate
``None`` (e.g. when benchmark return is unavailable).

Returned as a plain Markdown-ish string suitable for SMTP/Slack body.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def generate_monthly_report(
    execution: dict,
    perf: dict,
    regime: dict,
    risk_snapshot: dict,
) -> str:
    """Format a multi-section report. Returns plain text."""
    lines: list[str] = []
    lines.append(f"AUTO_TRADER — Monthly Cycle Report ({datetime.now().strftime('%B %Y')})")
    lines.append("=" * 70)

    # Regime
    lines.append("")
    lines.append("REGIME")
    lines.append("-" * 70)
    lines.append(
        f"  Label: {regime.get('label', '?').upper()}    "
        f"Confidence: {_pct(regime.get('confidence'))}   "
        f"Stable: {regime.get('stable')}"
    )

    # Execution
    lines.append("")
    lines.append("EXECUTION")
    lines.append("-" * 70)
    lines.append(
        f"  Sells: {execution.get('n_sells_filled', 0)} / "
        f"{execution.get('n_sells_submitted', 0)} filled"
    )
    lines.append(
        f"  Buys:  {execution.get('n_buys_filled', 0)} / "
        f"{execution.get('n_buys_submitted', 0)} filled"
    )
    if execution.get("n_partial", 0) > 0:
        lines.append(f"  Partial fills: {execution['n_partial']}")
    if execution.get("n_failed", 0) > 0:
        lines.append(f"  Failed: {execution['n_failed']}")

    # Performance
    lines.append("")
    lines.append("PERFORMANCE (trailing 30 days)")
    lines.append("-" * 70)
    if perf.get("error"):
        lines.append(f"  ⚠ {perf['error']}")
    else:
        lines.append(f"  Start value:        {_usd(perf.get('start_value'))}")
        lines.append(f"  End value:          {_usd(perf.get('end_value'))}")
        lines.append(f"  Total return:       {_pct(perf.get('total_return'))}")
        lines.append(f"  Benchmark (SPY):    {_pct(perf.get('benchmark_return'))}")
        lines.append(f"  Alpha:              {_pct(perf.get('alpha'))}")
        lines.append(f"  Realized P&L (YTD): {_usd(perf.get('realized_pnl_ytd'))}")

    # Risk snapshot (M4)
    lines.append("")
    lines.append("RISK SNAPSHOT")
    lines.append("-" * 70)
    lines.append(f"  Portfolio value:    {_usd(risk_snapshot.get('portfolio_value'))}")
    lines.append(f"  Cash (% of port):   {_usd(risk_snapshot.get('cash'))} "
                 f"({_pct(risk_snapshot.get('cash_pct'))})")
    lines.append(f"  Positions:          {risk_snapshot.get('n_positions', 0)}")
    lines.append(f"  Drawdown from peak: {_pct(risk_snapshot.get('drawdown_pct'))}")
    largest = risk_snapshot.get("largest_position_ticker")
    if largest:
        lines.append(
            f"  Largest position:   {largest} ({_pct(risk_snapshot.get('largest_position_pct'))})"
        )
    if risk_snapshot.get("circuit_breaker"):
        lines.append("  ⚠ DRAWDOWN CIRCUIT ACTIVE")
    if risk_snapshot.get("halt_flag"):
        lines.append("  ⚠ HALT FLAG SET")

    lines.append("")
    lines.append(f"Generated at {datetime.now().isoformat()}")
    return "\n".join(lines)


# ── M8: None-safe formatters ───────────────────────────────────────────────


def _usd(amount: Optional[float]) -> str:
    if amount is None:
        return "n/a"
    return f"${amount:,.2f}"


def _pct(fraction: Optional[float]) -> str:
    if fraction is None:
        return "n/a"
    return f"{fraction * 100:+.2f}%"


__all__ = ["generate_monthly_report"]
