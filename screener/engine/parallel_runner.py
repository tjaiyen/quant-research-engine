"""A3: optional parallel sector execution via ProcessPoolExecutor.

Use when sequential runtime is unacceptable (>30 min). Each worker is a
separate process — ``regime_data`` must be picklable (it is — plain dict
of floats and strings; the HMM model is *not* passed in, only the result
of ``hmm_predictor.get_regime`` is).

Falls back to recording an empty-result dict on individual worker failures
rather than crashing the whole run.
"""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

from screener.engine.industry_ranker import rank_industry

logger = logging.getLogger(__name__)


def _score_sector_worker(args: tuple[str, list[str], dict]) -> tuple[str, dict]:
    sector, tickers, regime_data = args
    return sector, rank_industry(sector, tickers, regime_data)


def rank_all_sectors_parallel(
    regime_data: dict,
    holdings: dict,
    max_workers: int = 4,
) -> dict[str, dict]:
    """Parallel version of ``rank_all_sectors``.

    Args:
        regime_data: dict from ``hmm_predictor.get_regime`` — picklable.
        holdings: parsed ``holdings.json`` dict.
        max_workers: process pool size (cap at ``len(sectors)``).
    """
    sectors = {k: v for k, v in holdings.items() if not k.startswith("_")}
    tasks = [(sector, tickers, regime_data) for sector, tickers in sectors.items()]
    results: dict[str, dict] = {}

    with ProcessPoolExecutor(max_workers=max(1, min(max_workers, len(tasks)))) as exe:
        future_map = {
            exe.submit(_score_sector_worker, task): task[0] for task in tasks
        }
        for future in as_completed(future_map):
            sector = future_map[future]
            try:
                sector_returned, result = future.result()
                results[sector_returned] = result
                logger.info(
                    "Parallel: %s complete (%d passed)",
                    sector_returned, len(result["passed"]),
                )
            except Exception as exc:
                logger.error("Parallel worker failed for %s: %s", sector, exc)
                results[sector] = {
                    "passed": [],
                    "skipped": [],
                    "failed": [],
                    "total_screened": 0,
                }
    return results


__all__ = ["rank_all_sectors_parallel"]
