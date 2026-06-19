"""Audit yfinance health for every ticker in the screener universe.

Read-only diagnostic. For each ticker in `screener/data/holdings.json`:

  * Calls `data_providers.yfinance_provider.fetch_daily_adjusted` (compact
    mode = ~6 months, ~1 sec/ticker, ~5 min total for the full universe)
  * Records: status, row count, last date, error class
  * Optionally substitutes: when `--substitute` is set, replaces failures
    with successful candidates (CTRA/DINO/EQT for Energy, TRV/PARA for
    other sectors) — but does NOT mutate holdings.json. That's a
    separate Slice 2 step.

Usage:
    # Audit only (no holdings.json change)
    python -m tasks.audit_universe

    # Audit + verify replacement candidates work (still no holdings.json change)
    python -m tasks.audit_universe --substitute

    # Audit a subset (debugging)
    python -m tasks.audit_universe AAPL HES MMC

Output: writes `logs/universe_audit_YYYYMMDD.json` and prints a summary.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from data_providers.yfinance_provider import (
    ProviderError,
    TickerNotFound,
    fetch_daily_adjusted,
)
from utils.logging_setup import get_logger

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOLDINGS_PATH = PROJECT_ROOT / "screener" / "data" / "holdings.json"
LOGS_DIR = PROJECT_ROOT / "logs"

# Inter-ticker pause to keep yfinance happy on bulk audits
INTER_TICKER_DELAY_SEC = 0.3

# Substitution candidate pools — list-per-sector so we can try multiple
# fallbacks if the first candidate is also delisted (which happened for
# PARA in the first audit pass — Paramount-Skydance merger closed).
SUBSTITUTE_CANDIDATES: dict[str, list[str]] = {
    "Energy": ["CTRA", "DINO", "EQT"],
    "Financials": ["TRV", "AJG", "MET"],          # P&C / life insurers
    "Communications": ["ROKU", "LYV", "PINS", "SNAP"],  # streaming/ad-tech (all S&P 500)
    # Other sectors can be added here when they start drifting.
}


def _load_holdings() -> dict:
    with HOLDINGS_PATH.open() as f:
        return json.load(f)


def _flat_universe(holdings: dict) -> list[tuple[str, str]]:
    """Return ``[(ticker, sector), …]`` skipping the ``_meta`` block."""
    out: list[tuple[str, str]] = []
    for sector, tickers in holdings.items():
        if sector.startswith("_"):
            continue
        if not isinstance(tickers, list):
            continue
        for t in tickers:
            t = str(t).upper().strip()
            if t:
                out.append((t, sector))
    return out


def check_one(ticker: str) -> dict:
    """Return audit dict for one ticker. Never raises."""
    started = time.time()
    try:
        df = fetch_daily_adjusted(ticker, output_size="compact")
        if df is None or df.empty:
            return {
                "ticker": ticker,
                "status": "empty",
                "rows": 0,
                "last_date": None,
                "elapsed_s": round(time.time() - started, 2),
                "error": "yfinance returned empty DataFrame",
            }
        last_date = df.index.max()
        return {
            "ticker": ticker,
            "status": "ok",
            "rows": int(len(df)),
            "last_date": str(last_date.date()) if hasattr(last_date, "date") else str(last_date),
            "elapsed_s": round(time.time() - started, 2),
            "error": None,
        }
    except TickerNotFound as exc:
        return {
            "ticker": ticker,
            "status": "not_found",
            "rows": 0,
            "last_date": None,
            "elapsed_s": round(time.time() - started, 2),
            "error": str(exc),
        }
    except ProviderError as exc:
        return {
            "ticker": ticker,
            "status": "provider_error",
            "rows": 0,
            "last_date": None,
            "elapsed_s": round(time.time() - started, 2),
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "ticker": ticker,
            "status": "error",
            "rows": 0,
            "last_date": None,
            "elapsed_s": round(time.time() - started, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


def audit(symbols: Iterable[str] | None = None) -> dict:
    """Audit the given symbols (or the full universe). Returns the summary dict."""
    holdings = _load_holdings()
    universe = _flat_universe(holdings)
    universe_set = {t for t, _ in universe}

    if symbols is not None:
        # Build sector lookup for the subset
        chosen = [(s.upper(), _find_sector(s.upper(), holdings)) for s in symbols]
    else:
        chosen = universe

    results: list[dict] = []
    log.info("Auditing %d tickers…", len(chosen))
    for i, (ticker, sector) in enumerate(chosen):
        rec = check_one(ticker)
        rec["sector"] = sector
        results.append(rec)
        log_fn = log.info if rec["status"] == "ok" else log.warning
        log_fn(
            "  [%d/%d] %s (%s): %s — %s rows",
            i + 1, len(chosen), ticker, sector, rec["status"], rec["rows"],
        )
        if i < len(chosen) - 1:
            time.sleep(INTER_TICKER_DELAY_SEC)

    failures = [r for r in results if r["status"] != "ok"]
    summary = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "n_audited": len(results),
        "n_ok": len(results) - len(failures),
        "n_failed": len(failures),
        "failures_by_sector": _group_failures_by_sector(failures),
        "results": results,
        "universe_size": len(universe_set),
    }
    return summary


def _find_sector(ticker: str, holdings: dict) -> str:
    for sector, tickers in holdings.items():
        if sector.startswith("_") or not isinstance(tickers, list):
            continue
        if ticker in tickers:
            return sector
    return "UNKNOWN"


def _group_failures_by_sector(failures: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for f in failures:
        grouped.setdefault(f.get("sector", "UNKNOWN"), []).append(f["ticker"])
    return grouped


def verify_substitutes(failures: list[dict], holdings: dict) -> dict[str, str]:
    """For each failure, propose + verify a substitute via yfinance.

    Returns ``{failed_ticker: chosen_substitute}``. Tickers without a
    successful substitute are omitted (caller treats those as "drop, no
    replacement").
    """
    substitutes: dict[str, str] = {}
    used_substitutes: set[str] = set()
    for f in failures:
        sector = f.get("sector", "UNKNOWN")
        in_sector = set(holdings.get(sector, []))

        # Pick a candidate pool (filtered to ones not already in the sector)
        pool = SUBSTITUTE_CANDIDATES.get(sector, [])
        candidates = [c for c in pool if c not in in_sector]

        # Verify candidates via yfinance, skipping any already used (so we
        # don't pick the same substitute twice in the same audit pass)
        chosen = None
        for cand in candidates:
            if cand in used_substitutes:
                continue
            check = check_one(cand)
            if check["status"] == "ok":
                chosen = cand
                used_substitutes.add(cand)
                log.info(
                    "Substitution: %s → %s (sector=%s, %s rows)",
                    f["ticker"], cand, sector, check["rows"],
                )
                break
            else:
                log.warning(
                    "Substitute candidate %s for %s also failed: %s",
                    cand, f["ticker"], check["status"],
                )
            time.sleep(INTER_TICKER_DELAY_SEC)

        if chosen:
            substitutes[f["ticker"]] = chosen
        else:
            log.warning(
                "No working substitute found for %s (sector=%s)",
                f["ticker"], sector,
            )
    return substitutes


def write_report(summary: dict, path: Path | None = None) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        today = datetime.now().strftime("%Y%m%d")
        path = LOGS_DIR / f"universe_audit_{today}.json"
    with path.open("w") as f:
        json.dump(summary, f, indent=2, default=str)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit universe ticker health via yfinance.")
    parser.add_argument("symbols", nargs="*", help="Optional subset of symbols.")
    parser.add_argument(
        "--substitute", action="store_true",
        help="Also verify substitution candidates for any failures.",
    )
    args = parser.parse_args(argv)

    syms = [s.upper() for s in args.symbols] if args.symbols else None
    summary = audit(symbols=syms)

    if args.substitute and summary["n_failed"] > 0:
        log.info("Verifying substitution candidates for %d failures…", summary["n_failed"])
        holdings = _load_holdings()
        failures = [r for r in summary["results"] if r["status"] != "ok"]
        substitutes = verify_substitutes(failures, holdings)
        summary["substitutes"] = substitutes

    path = write_report(summary)

    print()
    print("=" * 64)
    print(f"UNIVERSE AUDIT — {summary['audited_at']}")
    print("=" * 64)
    print(f"  Audited:  {summary['n_audited']}")
    print(f"  OK:       {summary['n_ok']}")
    print(f"  Failed:   {summary['n_failed']}")
    if summary["n_failed"] > 0:
        print()
        print("  Failures by sector:")
        for sector, tickers in summary["failures_by_sector"].items():
            print(f"    {sector}: {tickers}")
    if "substitutes" in summary:
        print()
        print("  Verified substitutes:")
        for old, new in summary["substitutes"].items():
            print(f"    {old} → {new}")
        unresolved = [
            r["ticker"] for r in summary["results"]
            if r["status"] != "ok" and r["ticker"] not in summary["substitutes"]
        ]
        if unresolved:
            print(f"  Unresolved (no working substitute): {unresolved}")
    print()
    print(f"  Report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
