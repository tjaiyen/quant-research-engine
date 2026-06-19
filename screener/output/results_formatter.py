"""Results formatter / serializer / cockpit-adapter.

Spec invariants:
  - L2: UTC timezone standardization on all timestamps.
  - A4: gzip-compressed signal audit trail with 30-day retention pruning.
  - H7: handles ``rank_industry`` dict-return structure.

The cockpit's Screener tab consumes the SQLite ``screener_results`` table
populated by the orchestrator. This module also writes a JSON file as a
secondary artifact (per-run snapshot) and a gzip audit file (full signal
detail per stock × signal).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from screener.config import AUDIT_RETAIN_DAYS, OUTPUT_DIR, TOP_N_OUTPUT

logger = logging.getLogger(__name__)


def format_results(ranked_sectors: dict, regime_data: dict) -> dict:
    """Assemble the final structured output blob.

    Args:
        ranked_sectors: ``{sector: rank_industry_dict}`` (H7 dict structure).
        regime_data: dict from ``hmm_predictor.get_regime``.
    """
    all_passed: list[dict] = [
        s
        for sec_data in ranked_sectors.values()
        for s in sec_data.get("passed", [])
    ]
    total_screened = sum(d.get("total_screened", 0) for d in ranked_sectors.values())
    total_skipped = sum(len(d.get("skipped", [])) for d in ranked_sectors.values())
    total_failed = sum(len(d.get("failed", [])) for d in ranked_sectors.values())
    veto_rate = round(1 - len(all_passed) / max(total_screened, 1), 4)

    top_overall = sorted(
        all_passed, key=lambda x: x["composite_score"], reverse=True
    )[:TOP_N_OUTPUT]

    sectors_out: dict[str, list[dict]] = {}
    for sector, sec_data in ranked_sectors.items():
        stocks = sec_data.get("passed", [])
        sectors_out[sector] = [
            {
                "rank": i + 1,
                "ticker": s["ticker"],
                "composite_score": s["composite_score"],
                "passed_veto": s["passed_veto"],
                "veto_reason": s.get("veto_reason"),
                "veto_relaxed": s.get("veto_relaxed", False),
                "relaxation_passes": s.get("relaxation_passes", 0),
                "signal_scores": s["signal_scores"],
                "signal_contributions": s["signal_contributions"],
                "regime": s["regime"],
                "regime_confidence": s["regime_confidence"],
            }
            for i, s in enumerate(stocks)
        ]

    # L2: UTC timestamp
    now = datetime.now(timezone.utc)

    return {
        "generated_at": now.isoformat(),
        "timezone": "UTC",
        "regime": {
            "label": regime_data["regime"],
            "confidence": regime_data["confidence"],
            "stable": regime_data.get("stable", False),
            "probabilities": regime_data["probabilities"],
            "blended_weights": regime_data["blended_weights"],
        },
        "sectors": sectors_out,
        "summary": {
            "total_sectors": len(ranked_sectors),
            "total_screened": total_screened,
            "total_passed_veto": len(all_passed),
            "total_skipped": total_skipped,
            "total_failed": total_failed,
            "veto_rate_pct": round(veto_rate * 100, 1),
            "top_overall": [
                {
                    "rank": i + 1,
                    "ticker": s["ticker"],
                    "composite_score": s["composite_score"],
                    "sector": next(
                        (
                            sec
                            for sec, d in ranked_sectors.items()
                            if any(p["ticker"] == s["ticker"] for p in d.get("passed", []))
                        ),
                        "Unknown",
                    ),
                }
                for i, s in enumerate(top_overall)
            ],
        },
    }


def save_audit_trail(ranked_sectors: dict, results: dict) -> Path:
    """A4: write per-stock signal detail to a gzipped JSON, prune old files."""
    audit_dir = Path(OUTPUT_DIR) / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    audit_path = audit_dir / f"signal_audit_{today}.json.gz"

    audit_data = {
        "generated_at": results["generated_at"],
        "regime": results["regime"]["label"],
        "sectors": {},
    }
    for sector, sec_data in ranked_sectors.items():
        audit_data["sectors"][sector] = {
            "passed": [
                {
                    "ticker": s["ticker"],
                    "signals": s.get("metadata", {}),
                    "score": s["composite_score"],
                }
                for s in sec_data.get("passed", [])
            ],
            "skipped": sec_data.get("skipped", []),
            "failed": sec_data.get("failed", []),
        }

    with gzip.open(audit_path, "wt", encoding="utf-8") as f:
        json.dump(audit_data, f, default=str)
    logger.info("Signal audit trail saved: %s", audit_path)

    # Prune old audit files
    cutoff = datetime.now() - timedelta(days=AUDIT_RETAIN_DAYS)
    for old_file in audit_dir.glob("signal_audit_*.json.gz"):
        if datetime.fromtimestamp(old_file.stat().st_mtime) < cutoff:
            try:
                old_file.unlink()
                logger.debug("Pruned old audit: %s", old_file)
            except Exception as exc:
                logger.debug("Could not prune %s: %s", old_file, exc)

    return audit_path


def to_dataframe(formatted_results: dict) -> pd.DataFrame:
    """Flatten the formatted results to one row per stock (CSV-friendly)."""
    rows: list[dict] = []
    for sector, stocks in formatted_results["sectors"].items():
        for s in stocks:
            row = {"sector": sector}
            row.update(
                {
                    k: v
                    for k, v in s.items()
                    if k not in {"signal_scores", "signal_contributions"}
                }
            )
            row.update({f"sig_{k}": v for k, v in s.get("signal_scores", {}).items()})
            rows.append(row)
    return pd.DataFrame(rows)


def to_json(formatted_results: dict, path: str | None = None) -> str:
    out = json.dumps(formatted_results, indent=2, default=str)
    if path:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
    return out


def _bucket_for_score(score: float) -> str:
    """Map composite_score to BUY/HOLD/AVOID for the integration adapter."""
    if score >= 0.65:
        return "BUY"
    if score >= 0.45:
        return "HOLD"
    return "AVOID"


def format_for_app(formatted_results: dict) -> pd.DataFrame:
    """Adapter to the cockpit ``mock_existing_app`` contract.

    Returns a DataFrame with columns ``ticker, sector, score, recommendation,
    confidence`` plus the additional rich columns from ``to_dataframe`` for
    the cockpit's richer Screener tab.
    """
    df = to_dataframe(formatted_results)
    if df.empty:
        return pd.DataFrame(
            columns=["ticker", "sector", "score", "recommendation", "confidence"]
        )
    df["score"] = df["composite_score"].astype(float)
    df["confidence"] = df["regime_confidence"].astype(float)
    df["recommendation"] = df["composite_score"].apply(_bucket_for_score)
    return df


def write_to_sqlite(formatted_results: dict, elapsed_seconds: float) -> str:
    """Persist a run into cockpit's ``screener_results`` + ``screener_runs`` tables.

    Returns the ``run_at`` ISO timestamp used as the primary key.
    """
    from utils.db import get_conn  # local import — keeps screener decoupled

    run_at = formatted_results["generated_at"]
    summary = formatted_results["summary"]

    # Build the per-ticker rows with the top_overall_rank annotation
    overall_rank: dict[str, int] = {
        s["ticker"]: int(s["rank"]) for s in summary["top_overall"]
    }
    ticker_rows: list[tuple] = []
    for sector, stocks in formatted_results["sectors"].items():
        for s in stocks:
            ticker_rows.append(
                (
                    run_at,
                    s["ticker"],
                    sector,
                    int(s["rank"]),
                    float(s["composite_score"]),
                    formatted_results["regime"]["label"],
                    float(formatted_results["regime"]["confidence"]),
                    int(bool(s["passed_veto"])),
                    s.get("veto_reason"),
                    int(bool(s.get("veto_relaxed", False))),
                    int(s.get("relaxation_passes", 0)),
                    json.dumps(s.get("signal_scores", {})),
                    json.dumps(s.get("signal_contributions", {})),
                    overall_rank.get(s["ticker"]),
                )
            )

    run_row = (
        run_at,
        formatted_results["regime"]["label"],
        float(formatted_results["regime"]["confidence"]),
        int(bool(formatted_results["regime"].get("stable", False))),
        int(summary["total_sectors"]),
        int(summary["total_screened"]),
        int(summary["total_passed_veto"]),
        int(summary["total_skipped"]),
        int(summary["total_failed"]),
        float(summary["veto_rate_pct"]),
        float(elapsed_seconds),
        f"{OUTPUT_DIR.rstrip('/')}/screener_output_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json",
        json.dumps(formatted_results, default=str),
    )

    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO screener_results "
            "(run_at, ticker, sector, rank, composite_score, regime, regime_confidence, "
            " passed_veto, veto_reason, veto_relaxed, relaxation_passes, "
            " signal_scores_json, signal_contributions_json, top_overall_rank) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ticker_rows,
        )
        conn.execute(
            "INSERT OR REPLACE INTO screener_runs "
            "(run_at, regime_label, regime_confidence, regime_stable, "
            " total_sectors, total_screened, total_passed_veto, total_skipped, "
            " total_failed, veto_rate_pct, elapsed_seconds, output_path, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            run_row,
        )
    logger.info(
        "screener_results: %d ticker rows + 1 run row written for %s",
        len(ticker_rows), run_at,
    )
    return run_at


__all__ = [
    "format_results",
    "save_audit_trail",
    "to_dataframe",
    "to_json",
    "format_for_app",
    "write_to_sqlite",
]
