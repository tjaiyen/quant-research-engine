"""Run cards: one portable JSON artifact per analytical run (U32).

A run card freezes what a `sim` / `tournament` / `backtest` / `signal-lab`
run actually saw — params, git commit, a params hash, headline metrics, and
any validation block — so a number quoted from a note weeks later can be
traced back to the exact configuration that produced it. Engine artifact:
lives in ``store/run_cards/`` (NOT the vault; renders stay pure).

Slim port of HKUDS/Vibe-Trading ``agent/backtest/run_card.py`` (MIT):
artifact-refs and strategy-file hashing dropped — the git commit covers code
identity here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_CARD_DIR = REPO_ROOT / "store" / "run_cards"


def _git_head() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def _json_safe(obj):
    """NaN/inf → None, recursively — run cards must be strict JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def params_hash(params: dict) -> str:
    """sha256 of the sorted-JSON params — same params, same hash."""
    blob = json.dumps(_json_safe(params), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def write_run_card(command: str, params: dict, metrics: dict | None = None,
                   warnings: list[str] | None = None,
                   validation: dict | None = None) -> Path | None:
    """Write ``store/run_cards/<command>-<utc-ts>.json``; None on any failure.

    Never raises — a run card is bookkeeping and must not break the run.
    """
    try:
        ts = datetime.now(timezone.utc)
        card = _json_safe({
            "schema_version": SCHEMA_VERSION,
            "command": command,
            "created_at": ts.isoformat(),
            "git_head": _git_head(),
            "params": params,
            "params_hash": params_hash(params),
            "metrics": metrics or {},
            "warnings": warnings or [],
            "validation": validation or {},
        })
        RUN_CARD_DIR.mkdir(parents=True, exist_ok=True)
        path = RUN_CARD_DIR / f"{command}-{ts.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(card, indent=2, sort_keys=True))
        return path
    except Exception as exc:
        logger.debug("run card not written: %s", exc)
        return None


__all__ = ["write_run_card", "params_hash", "SCHEMA_VERSION", "RUN_CARD_DIR"]
