"""Append-only JSONL execution audit log.

Every event placed by ``order_executor.submit_order`` ends up here in
addition to the ``system_events`` SQLite table. The JSONL file is the
human-readable / grep-able record; ``system_events`` is the queryable one.

Fully written in Slice 6 — this file is also imported by Slice 3's
``order_executor.py`` (via ``log_execution_event``), so it needs to exist
from the broker-layer point onward.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from auto_trader.config import EXECUTION_LOG_PATH
from auto_trader.utils import now_iso

logger = logging.getLogger(__name__)


def log_execution_event(
    event_type: str,
    ticker: str,
    side: str,
    amount_usd: float,
    details: Optional[dict] = None,
) -> None:
    """Append one event to the JSONL audit log.

    Best-effort — failures are logged but don't propagate (audit must
    never block an order).
    """
    payload = {
        "ts": now_iso(),
        "event_type": event_type,
        "ticker": ticker,
        "side": side,
        "amount_usd": float(amount_usd),
        "details": details or {},
    }
    try:
        path = Path(EXECUTION_LOG_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception as exc:  # pragma: no cover
        logger.error("execution_log write failed: %s", exc)


__all__ = ["log_execution_event"]
