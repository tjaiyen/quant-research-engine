"""Auto-trader entrypoint.

CLI dispatcher for the four operational entry points. Designed from
scratch (the v2 spec is unavailable; v3 only references it).

Usage::

    python -m auto_trader.trader_main --setup           # paper trade onboarding
    python -m auto_trader.trader_main --daily           # daily monitor
    python -m auto_trader.trader_main --monthly         # monthly buy cycle
    python -m auto_trader.trader_main --emergency-stop  # set halt + cancel
    python -m auto_trader.trader_main --review          # ad-hoc perf review
"""
from __future__ import annotations

import argparse
import sys
from typing import Iterable


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="auto_trader",
        description="Auto-trader CLI — paper-first, mock-broker by default.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--setup", action="store_true",
                       help="Run paper-trade onboarding (init DB, write .paper_start_date)")
    group.add_argument("--daily", action="store_true",
                       help="Run daily monitor (sync, stop-loss scan, signal rescore)")
    group.add_argument("--monthly", action="store_true",
                       help="Run monthly buy cycle (only fires inside the 1st-5th window)")
    group.add_argument("--emergency-stop", action="store_true",
                       help="Set halt flag + cancel open orders")
    group.add_argument("--review", action="store_true",
                       help="Print ad-hoc performance review")
    parser.add_argument("--reason", default="manual",
                        help="(emergency-stop only) reason text")

    args = parser.parse_args(argv)

    if args.setup:
        from auto_trader.scripts import paper_trade_setup
        return paper_trade_setup.main([])
    if args.daily:
        from auto_trader.scripts import daily_run
        return daily_run.main([])
    if args.monthly:
        from auto_trader.scripts import monthly_run
        return monthly_run.main([])
    if args.emergency_stop:
        from auto_trader.scripts import emergency_stop
        return emergency_stop.main(["--reason", args.reason])
    if args.review:
        from auto_trader.scripts import performance_review
        return performance_review.main([])

    parser.error("No subcommand selected")
    return 2


if __name__ == "__main__":
    sys.exit(main())
