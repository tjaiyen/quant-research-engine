"""Build the signal panel — the expensive step, computed once and cached.

For each rebalance date it scores every (sampled) ticker causally (history sliced
to the date — no look-ahead) and records the 5 raw signal scores, the default
composite, the veto flag, and the forward return to the next rebalance. Every
tournament variant is then a cheap pass over these rows.

Heavy reuse of the U4 / walk-forward machinery: `_load_universe`, `_ph_for`,
`_regime_at`, `_slice_history_to`, `get_market_features`, `_rebalance_dates`,
`price_on_or_before`, and `composite_scorer.score_stock`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from screener.config import HMM_LOOKBACK_YEARS, YFIN_MIN_ROWS_REQUIRED

logger = logging.getLogger(__name__)

_CACHE = Path(__file__).resolve().parents[2] / "store" / "tournament_panel.json"
_STEP = {"month": 30, "quarter": 91}


def _fwd_return(ticker: str, d0, d1) -> float | None:
    from utils.db import price_on_or_before
    p0 = price_on_or_before(ticker, d0.isoformat())
    p1 = price_on_or_before(ticker, d1.isoformat())
    if p0 and p1:
        return p1 / p0 - 1.0
    return None


def build_signal_panel(years: int = 3, rebalance: str = "quarter",
                       max_per_sector: int = 10, use_cache: bool = True,
                       cache_path: Path | None = None,
                       score_fn=None, regime_fn=None) -> dict:
    """Return the causal signal panel (built once, cached to store/)."""
    cache_path = Path(cache_path) if cache_path else _CACHE
    key = {"years": years, "rebalance": rebalance, "max_per_sector": max_per_sector}
    if use_cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("key") == key and cached.get("rows"):
                logger.info("tournament panel: cache hit (%s)", cache_path)
                return cached
        except Exception as exc:
            logger.debug("panel cache unreadable (%s); rebuilding", exc)

    from screener.backtest.portfolio_backtest import _rebalance_dates
    from screener.backtest.walk_forward import (
        _load_universe, _ph_for, _regime_at, _slice_history_to,
    )
    from screener.data.market_features import get_market_features

    if score_fn is None:
        from screener.engine.composite_scorer import score_stock as score_fn
    if regime_fn is None:
        regime_fn = _regime_at

    sectors = _load_universe()
    sectors = {s: t[:max_per_sector] for s, t in sectors.items()}
    flat = {t for ts in sectors.values() for t in ts}
    sector_of = {t: s for s, ts in sectors.items() for t in ts}
    histories = {t: _ph_for(t) for t in flat}
    histories = {t: ph for t, ph in histories.items() if ph is not None}

    features = get_market_features(lookback_years=HMM_LOOKBACK_YEARS, min_rows=300)
    dates = _rebalance_dates(features.index, years, _STEP.get(rebalance, 91))

    panel: dict = {"key": key, "as_of": datetime.now(timezone.utc).isoformat(),
                   "years": years, "rebalance": rebalance,
                   "max_per_sector": max_per_sector,
                   "segments": [], "rows": [],
                   "universe": sorted(histories.keys())}
    if len(dates) < 2:
        return panel

    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        try:
            regime = regime_fn(features, d0)
        except Exception as exc:
            logger.debug("regime skip at %s: %s", d0.date(), exc)
            continue
        spy_ret = _fwd_return("SPY", d0, d1)
        panel["segments"].append({
            "d0": str(d0.date()), "d1": str(d1.date()),
            # _regime_at returns the label under "regime" (not "label").
            "regime": regime.get("regime") or regime.get("label"),
            "regime_conf": regime.get("confidence"),
            "spy_return": spy_ret,
        })
        for t, ph in histories.items():
            ph_train = _slice_history_to(ph, d0)
            if len(ph_train) < YFIN_MIN_ROWS_REQUIRED:
                continue
            try:
                res = score_fn(t, regime, ph_train)
            except Exception:
                continue
            fwd = _fwd_return(t, d0, d1)
            if fwd is None:
                continue
            panel["rows"].append({
                "d0": str(d0.date()), "ticker": t, "sector": sector_of.get(t),
                "signals": res.get("signal_scores", {}),
                "composite": res.get("composite_score"),
                "passed_veto": bool(res.get("passed_veto")),
                "fwd_return": fwd,
            })
        logger.info("panel: %s scored %d rows", d0.date(),
                    sum(1 for r in panel["rows"] if r["d0"] == str(d0.date())))

    if use_cache:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(panel))
            logger.info("tournament panel cached → %s (%d rows)", cache_path,
                        len(panel["rows"]))
        except Exception as exc:
            logger.warning("panel cache write failed: %s", exc)
    return panel


__all__ = ["build_signal_panel"]
