"""Typed screener→trader contract object (Insight U1, surgical scope).

A `Signal` is the validated, type-coerced form of one screener pick. It exists
at the **ingestion boundary** (`auto_trader/compat/screener_compat`) so a
malformed/old-schema cache is caught and coerced once — instead of a bad type
(e.g. a string `composite_score`) silently flowing into the trader's arithmetic.

Deliberately scoped: the rest of the pipeline still passes plain dicts. `Signal`
validates the required fields and `canonical()` emits them back into the row,
preserving any extra keys. Lenient by design (the paper trader values
robustness): bad/missing values default with a logged warning rather than
crashing a whole buy cycle.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The fields a downstream consumer (signal_filter → position_sizer → …) relies on.
REQUIRED_FIELDS = ("ticker", "composite_score", "passed_veto", "signal_scores")


def _as_float(value, default: float, *, ctx: str) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Signal: non-numeric %s=%r — using %s", ctx, value, default)
        return default


@dataclass(frozen=True)
class Signal:
    ticker: str
    composite_score: float
    passed_veto: bool
    signal_scores: dict
    sector: str = "UNKNOWN"
    regime: str = "unknown"
    regime_confidence: float = 0.0
    veto_reason: str | None = None

    @classmethod
    def from_row(cls, row: dict, sector: str | None = None) -> "Signal":
        """Parse + type-coerce one screener pick dict into a validated Signal.

        Missing required keys / bad types default (with a warning), so an older
        or partially-corrupt cache row degrades gracefully but loudly.
        """
        if not isinstance(row, dict):
            raise TypeError(f"Signal.from_row expects a dict, got {type(row).__name__}")
        tk = str(row.get("ticker") or "UNKNOWN")
        ss = row.get("signal_scores")
        if not isinstance(ss, dict):
            if ss is not None:
                logger.warning("Signal %s: signal_scores not a dict (%r) — using {}", tk, ss)
            ss = {}
        return cls(
            ticker=tk,
            composite_score=_as_float(row.get("composite_score"), 0.0, ctx=f"{tk}.composite_score"),
            passed_veto=bool(row.get("passed_veto", False)),
            signal_scores=dict(ss),
            sector=str(row.get("sector") or sector or "UNKNOWN"),
            regime=str(row.get("regime") or "unknown"),
            regime_confidence=_as_float(row.get("regime_confidence"), 0.0, ctx=f"{tk}.regime_confidence"),
            veto_reason=row.get("veto_reason"),
        )

    def canonical(self) -> dict:
        """The validated/coerced required fields + sector, to merge back into a row.

        Extra keys on the original row (veto_detail, signal_contributions, …) are
        intentionally NOT included here — the caller merges these onto the row.
        """
        return {
            "ticker": self.ticker,
            "composite_score": self.composite_score,
            "passed_veto": self.passed_veto,
            "signal_scores": self.signal_scores,
            "sector": self.sector,
        }


__all__ = ["Signal", "REQUIRED_FIELDS"]
