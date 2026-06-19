"""Ad-hoc CLI: pretty-print monthly performance.

Usage::

    python -m auto_trader.scripts.performance_review
"""
from __future__ import annotations

import json
import sys
from typing import Iterable


def main(argv: Iterable[str] | None = None) -> int:  # noqa: ARG001
    from auto_trader.monitor.performance_engine import compute_monthly_performance
    from auto_trader.monitor.position_tracker import get_position_pnl_summary
    from auto_trader.risk.risk_report import generate_risk_snapshot
    from auto_trader.state.portfolio_db import initialize_db

    initialize_db()

    perf = compute_monthly_performance()
    pnl = get_position_pnl_summary()
    risk = generate_risk_snapshot(
        portfolio_value=pnl["total_value"],
        cash=0.0,  # ad-hoc; cash unknown without live broker call
    )

    print("=" * 60)
    print("AUTO_TRADER — Performance Review")
    print("=" * 60)
    print(json.dumps({"performance": perf, "pnl": pnl, "risk": risk},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
