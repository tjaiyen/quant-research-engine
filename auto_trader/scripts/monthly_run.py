"""Monthly buy cycle.

Loads cached screener results (the screener runs the night before via
``pre_run_screener.py``). C1: cache normalized via the compat shim before
use. C7: 10h staleness check.

Runs daily but no-ops outside the 1st-5th of the month and outside the
MOO submission window.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from auto_trader.compat.screener_compat import (
    normalize_screener_cache,
    validate_cache_contract,
)
from auto_trader.config import (
    LOG_DIR,
    LOG_LEVEL,
    MIN_CASH_TO_TRADE,
    MONTHLY_CYCLE_DAY,
    MONTHLY_CYCLE_WINDOW_DAYS,
    SCREENER_CACHE_MAX_AGE_HOURS,
    get_screener_cache_path,
)
from auto_trader.utils import setup_logging

logger = logging.getLogger(__name__)


def load_screener_cache() -> Optional[dict]:
    cache_path = get_screener_cache_path()
    try:
        with open(cache_path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        logger.error(
            "Screener cache not found: %s. Run pre_run_screener.py first.",
            cache_path,
        )
        return None

    # C7: staleness check
    cached_at = raw.get("_cached_at", raw.get("generated_at", ""))
    if cached_at:
        try:
            ts = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            if age_h > SCREENER_CACHE_MAX_AGE_HOURS:
                logger.error(
                    "Cache is %.1fh old > %dh. Run pre_run_screener.py.",
                    age_h, SCREENER_CACHE_MAX_AGE_HOURS,
                )
                return None
        except Exception as exc:
            logger.debug("Staleness parse failed (%s); proceeding", exc)

    cache = normalize_screener_cache(raw)
    valid, errors = validate_cache_contract(cache)
    if not valid:
        logger.error("Cache failed contract validation: %s", errors)
        return None

    logger.info("Screener cache loaded and validated")
    return cache


def run_monthly_cycle() -> dict:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    setup_logging(f"{LOG_DIR}monthly_{today}.log", LOG_LEVEL)

    from auto_trader.broker.alpaca_client import get_account_info
    from auto_trader.broker.market_calendar import get_monthly_cycle_date
    from auto_trader.broker.portfolio_state import sync_portfolio_state
    from auto_trader.allocator.delta_engine import compute_delta
    from auto_trader.allocator.position_sizer import compute_allocations
    from auto_trader.allocator.signal_filter import filter_signals
    from auto_trader.allocator.target_builder import build_target_portfolio
    from auto_trader.credentials import is_halted
    from auto_trader.execution.order_scheduler import wait_for_moo_window
    from auto_trader.execution.order_sequencer import execute_sequence
    from auto_trader.monitor.alert_engine import send_alert
    from auto_trader.monitor.monthly_report import generate_monthly_report
    from auto_trader.monitor.performance_engine import compute_monthly_performance
    from auto_trader.risk.exposure_guard import run_all_guards
    from auto_trader.risk.risk_report import generate_risk_snapshot
    from auto_trader.state.portfolio_db import initialize_db, log_system_event

    logger.info("=" * 60)
    logger.info("MONTHLY BUY CYCLE — START")
    logger.info(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    if is_halted():
        logger.error("HALT FLAG ACTIVE — cycle aborted")
        return {"status": "halted"}

    cycle_date = get_monthly_cycle_date(MONTHLY_CYCLE_DAY, MONTHLY_CYCLE_WINDOW_DAYS)
    if cycle_date != date.today():
        logger.info("Not monthly cycle day (next=%s) — skipping", cycle_date)
        return {"status": "skipped", "next_cycle_date": str(cycle_date) if cycle_date else None}

    initialize_db()

    cache = load_screener_cache()
    if not cache:
        send_alert(
            "MONTHLY CYCLE BLOCKED",
            "Screener cache missing, stale, or invalid. Run pre_run_screener.py.",
            force=True,
        )
        return {"status": "no_cache"}

    regime_data = cache["regime"]
    logger.info(
        "Regime: %s (%.1f%%)",
        regime_data["label"].upper(), regime_data["confidence"] * 100,
    )

    state = sync_portfolio_state()
    account = get_account_info()
    cash = account["cash"]
    if cash < MIN_CASH_TO_TRADE:
        msg = (
            f"DEPOSIT NOT RECEIVED: Cash ${cash:.2f} < ${MIN_CASH_TO_TRADE:.2f}. "
            "Check ACH transfer. Cycle aborted."
        )
        send_alert("MONTHLY CYCLE: Deposit not received", msg, force=True)
        return {"status": "no_cash"}

    eligible = filter_signals(cache)
    if not eligible:
        send_alert(
            "MONTHLY CYCLE: No eligible stocks",
            f"Regime: {regime_data['label']}. No stocks passed signal filter.",
            "MONTHLY_COMPLETE",
        )
        return {"status": "no_eligible"}

    allocated = compute_allocations(
        eligible, account["portfolio_value"], cash,
    )
    target_portfolio = build_target_portfolio(allocated)

    instructions = compute_delta(
        target_portfolio, state["positions"], account["portfolio_value"],
    )
    safe = run_all_guards(
        instructions, state["positions"],
        account["portfolio_value"], cash, regime_data,
    )
    if not safe:
        send_alert(
            "MONTHLY CYCLE: All trades blocked",
            "Risk guards eliminated all instructions.",
            "MONTHLY_COMPLETE",
        )
        return {"status": "all_blocked"}

    if not wait_for_moo_window(timeout_seconds=1800):
        send_alert(
            "MONTHLY CYCLE: MOO window missed",
            "Orders not submitted.",
            force=True,
        )
        return {"status": "moo_missed"}

    execution = execute_sequence(safe, state["positions"], regime_data["label"])

    perf = compute_monthly_performance()
    risk_snapshot = generate_risk_snapshot(account["portfolio_value"], cash)
    report = generate_monthly_report(execution, perf, regime_data, risk_snapshot)

    send_alert(
        f"Monthly Cycle Complete — {datetime.now().strftime('%B %Y')}",
        report,
        "MONTHLY_COMPLETE",
    )
    log_system_event(
        "MONTHLY_CYCLE",
        "Complete",
        {"execution": execution, "performance": perf},
    )
    logger.info("Monthly cycle complete")
    return {
        "status": "ok",
        "execution": execution,
        "performance": perf,
        "regime": regime_data,
    }


def main(argv: Iterable[str] | None = None) -> int:
    try:
        run_monthly_cycle()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.exception("Monthly cycle FAILED: %s", exc)
        try:
            from auto_trader.monitor.alert_engine import send_alert

            send_alert("MONTHLY CYCLE FAILED", str(exc), force=True)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
