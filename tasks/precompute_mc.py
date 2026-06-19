"""Precompute Monte Carlo scenarios and write to mc_results cache.

Per the cache-only MC policy:
  - This is the ONLY place that writes to mc_results.
  - UI callbacks must read from mc_results via quant_models.fetch_cached_mc().

Usage:
    python -m tasks.precompute_mc                    # all default stress scenarios
    python -m tasks.precompute_mc --horizon 21       # custom horizon
    python -m tasks.precompute_mc --n-sims 10000     # custom sim count

Scenario key is a deterministic SHA-256 hash of:
    holdings_signature + horizon + n_sims + lookback + stress_label
so calling this repeatedly with the same portfolio replaces cleanly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone

import numpy as np

from models_portfolio import (
    STRESS_WINDOWS,
    compute_stress_scenarios,
    simulate_portfolio,
    compute_risk_contributions,
)
from utils.db import get_conn, init_db, list_holdings
from utils.logging_setup import get_logger

log = get_logger(__name__)


def _holdings_signature() -> str:
    """Deterministic fingerprint of current holdings (ticker + shares)."""
    h = list_holdings()
    if h.empty:
        return "empty"
    sig = "|".join(
        f"{r['ticker']}:{float(r['shares']):.4f}"
        for _, r in h.sort_values("ticker").iterrows()
    )
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def _scenario_key(
    holdings_sig: str,
    horizon: int,
    n_sims: int,
    lookback: int,
    stress_label: str,
) -> str:
    blob = f"{holdings_sig}|h={horizon}|n={n_sims}|lb={lookback}|s={stress_label}"
    return hashlib.sha256(blob.encode()).hexdigest()


def _write_baseline_mc(horizon: int, n_sims: int, lookback: int) -> int:
    """Run + write the 'Current' MC scenario, including risk contributions."""
    sim = simulate_portfolio(horizon_days=horizon, n_sims=n_sims, lookback_days=lookback)
    if sim is None:
        log.warning("simulate_portfolio returned None — skipping baseline write")
        return 0

    contribs = compute_risk_contributions(sim)
    components_payload = [
        {
            "ticker": c.ticker,
            "weight": c.weight,
            "component_cvar": c.component_cvar,
            "contribution_pct": c.contribution_pct,
            "risk_multiplier": c.risk_multiplier,
        }
        for c in contribs
    ]

    holdings_sig = _holdings_signature()
    key = _scenario_key(holdings_sig, horizon, n_sims, lookback, "Current")

    # Convert percentile arrays to lists for JSON.
    percentiles_payload = {
        str(p): arr.tolist() for p, arr in sim.percentiles.items()
    }

    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO mc_results (
                scenario_key, run_at, stress_label, horizon_days, n_sims,
                lookback_days, current_value,
                var_95, var_99, cvar_95, var_pct_95, cvar_pct_95,
                median_terminal, best_5pct_gain, portfolio_vol_ann,
                percentiles_json, components_json, asset_names_json,
                status, ttl_hours
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key, datetime.now(timezone.utc).isoformat(), "Current",
                horizon, n_sims, lookback, sim.current_value,
                sim.var_95, sim.var_99, sim.cvar_95,
                sim.var_pct_95, sim.cvar_pct_95,
                sim.median_terminal, sim.best_5pct_gain,
                # Annualize from path daily returns
                float(np.diff(np.log(sim.sim_paths), axis=1).std(ddof=1) * np.sqrt(252)),
                json.dumps(percentiles_payload),
                json.dumps(components_payload),
                json.dumps(list(sim.asset_names)),
                "ok", 24,
            ),
        )
    log.info("Wrote baseline MC: VaR95=%s key=%s", sim.var_95, key[:8])
    return 1


def _write_stress_mc(horizon: int, n_sims: int, lookback: int) -> int:
    """Run + write all stress regimes from compute_stress_scenarios."""
    results = compute_stress_scenarios(horizon_days=horizon, n_sims=n_sims)
    if not results:
        return 0

    holdings_sig = _holdings_signature()
    written = 0
    for r in results:
        if r.label == "Current":
            continue  # baseline written separately with full payload
        key = _scenario_key(holdings_sig, horizon, n_sims, lookback, r.label)
        with get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO mc_results (
                    scenario_key, run_at, stress_label, horizon_days, n_sims,
                    lookback_days, current_value,
                    var_95, var_99, cvar_95, var_pct_95, cvar_pct_95,
                    median_terminal, best_5pct_gain, portfolio_vol_ann,
                    percentiles_json, components_json, asset_names_json,
                    status, ttl_hours
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    key, datetime.now(timezone.utc).isoformat(), r.label,
                    horizon, n_sims, lookback, 0.0,  # current_value not directly exposed
                    r.var_95, 0.0, r.cvar_95,
                    r.var_pct_95, r.cvar_pct_95,
                    0.0, 0.0,
                    r.annualized_portfolio_vol,
                    None, None, None,
                    r.status, 24,
                ),
            )
        written += 1
        log.info("Wrote stress '%s' VaR95=%s key=%s", r.label, r.var_95, key[:8])
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Precompute MC scenarios.")
    parser.add_argument("--horizon", type=int, default=21,
                        help="forward horizon in trading days (default 21)")
    parser.add_argument("--n-sims", type=int, default=5000,
                        help="number of simulation paths (default 5000)")
    parser.add_argument("--lookback", type=int, default=252,
                        help="historical window for mu/Sigma (default 252)")
    parser.add_argument("--baseline-only", action="store_true",
                        help="skip stress scenarios, write only the current regime")
    args = parser.parse_args(argv)

    init_db()

    if list_holdings().empty:
        log.warning("No holdings — nothing to compute. Seed with manage_holdings first.")
        return 0

    written = _write_baseline_mc(args.horizon, args.n_sims, args.lookback)
    if not args.baseline_only:
        written += _write_stress_mc(args.horizon, args.n_sims, args.lookback)

    log.info("Done. Wrote %d MC rows.", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
