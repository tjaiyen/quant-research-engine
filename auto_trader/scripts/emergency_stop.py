"""C6: Emergency stop — halt flag set FIRST, then cancel open orders.

The order is critical: even if cancellation hangs or the network drops,
the halt flag is already on disk, so subsequent scripts (and live order
submissions) will refuse to proceed.

Usage::

    python -m auto_trader.scripts.emergency_stop          # set halt + cancel
    python -m auto_trader.scripts.emergency_stop --clear  # clear halt
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Iterable

from auto_trader.config import LOG_DIR, LOG_LEVEL
from auto_trader.utils import setup_logging

logger = logging.getLogger(__name__)


def stop(reason: str = "manual") -> dict:
    """C6: SET HALT FLAG FIRST, then attempt to cancel open orders."""
    from auto_trader.credentials import set_halt
    from auto_trader.state.portfolio_db import log_system_event

    set_halt(reason)  # synchronous file write — happens BEFORE any network call
    logger.warning("HALT FLAG SET — reason=%s", reason)

    cancelled = 0
    try:
        from auto_trader.broker.order_executor import cancel_all_orders

        cancelled = cancel_all_orders()
    except Exception as exc:
        logger.error("cancel_all_orders failed (halt is still on): %s", exc)

    log_system_event("EMERGENCY_STOP", f"Halt set; orders cancelled={cancelled}",
                     {"reason": reason, "cancelled": cancelled})
    return {"halted": True, "cancelled": cancelled, "reason": reason}


def clear() -> dict:
    from auto_trader.credentials import clear_halt
    from auto_trader.state.portfolio_db import log_system_event

    clear_halt()
    log_system_event("EMERGENCY_STOP_CLEARED", "Halt cleared", {})
    logger.warning("HALT FLAG CLEARED")
    return {"halted": False}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emergency stop — halt + cancel.")
    parser.add_argument("--clear", action="store_true", help="Clear the halt flag")
    parser.add_argument("--reason", default="manual", help="Reason text for the halt")
    args = parser.parse_args(argv)

    setup_logging(f"{LOG_DIR}emergency_stop.log", LOG_LEVEL)

    if args.clear:
        result = clear()
    else:
        result = stop(reason=args.reason)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
