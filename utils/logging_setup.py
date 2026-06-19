"""Logging bootstrap. One line: `from utils.logging_setup import get_logger`.

In production (LOG_FORMAT=json), emits JSON Lines for easier log search.
In dev (default), emits the prior human-readable text format.

Environment:
  LOG_FORMAT=json   -> structured JSON output
  LOG_LEVEL=INFO    -> standard level filter
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def _build_formatter() -> logging.Formatter:
    """Return a JSON formatter when LOG_FORMAT=json, else human-readable text."""
    use_json = os.getenv("LOG_FORMAT", "text").lower() == "json"
    if use_json:
        try:
            from pythonjsonlogger.jsonlogger import JsonFormatter

            return JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={"asctime": "ts", "levelname": "level"},
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        except Exception:
            # Fall through to text if python-json-logger isn't installed.
            pass
    return logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _configure(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    fmt = _build_formatter()
    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    rot = RotatingFileHandler(
        log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3
    )
    rot.setFormatter(fmt)
    root.addHandler(rot)

    _CONFIGURED = True


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    _configure(level)
    return logging.getLogger(name)
