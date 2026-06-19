"""Central config loader. Reads .env once and exposes typed settings."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    log_level: str
    watchlist: tuple[str, ...]


def _parse_watchlist(raw: str) -> tuple[str, ...]:
    return tuple(s.strip().upper() for s in raw.split(",") if s.strip())


def load_settings() -> Settings:
    db_rel = os.getenv("DB_PATH", "db/cockpit.sqlite")
    # Support both relative (resolves under ROOT) and absolute paths so the
    # production volume mount (e.g. /app/db/cockpit.sqlite) works.
    db_path_arg = Path(db_rel)
    db_path = db_path_arg if db_path_arg.is_absolute() else (ROOT / db_path_arg)
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        db_path=db_path,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        watchlist=_parse_watchlist(os.getenv("WATCHLIST", "AAPL,MSFT,NVDA,SPY,QQQ")),
    )


SETTINGS = load_settings()
