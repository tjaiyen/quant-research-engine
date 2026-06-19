"""Portfolio aggregation + forward simulation.

v1 scope:
- Per-position: current value, P/L $, P/L %, weight.
- Aggregate: total value, total cost, total P/L, HHI concentration,
  top-N weight share, name count.
- Forward: correlated GBM Monte Carlo -> VaR / CVaR / percentile fan.

Assumptions (call out in UI):
- Log-normal returns; constant mu/Sigma from last 252 days.
- No dividends, taxes, transaction costs, rebalancing.
- Real tails are fatter than Gaussian -> VaR tends to underestimate in stress.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from utils.db import fetch_prices, list_holdings
from utils.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Position:
    ticker: str
    shares: float
    cost_basis: float
    last_price: float | None
    market_value: float | None
    cost_value: float
    pnl_abs: float | None
    pnl_pct: float | None
    weight: float | None      # fraction of total market value


@dataclass(frozen=True)
class PortfolioSummary:
    positions: list[Position]
    total_value: float
    total_cost: float
    total_pnl_abs: float
    total_pnl_pct: float | None
    name_count: int
    hhi: float                # 0..1 (Herfindahl on weights; 1 = single name)
    top_weight: float         # largest single-name weight
    concentration_bucket: str # 'diversified', 'moderate', 'concentrated'


def _concentration_bucket(hhi: float, top: float) -> str:
    """Apply decision-first buckets. HHI alone can be misleading for small N."""
    if top >= 0.40 or hhi >= 0.30:
        return "concentrated"
    if top >= 0.25 or hhi >= 0.20:
        return "moderate"
    return "diversified"


def _last_price(ticker: str) -> float | None:
    df = fetch_prices(ticker, limit=1)
    if df.empty or "adj_close" not in df or pd.isna(df["adj_close"].iloc[-1]):
        return None
    return float(df["adj_close"].iloc[-1])


def build_portfolio() -> PortfolioSummary:
    """Read holdings + latest prices, return a full snapshot."""
    h = list_holdings()
    if h.empty:
        return PortfolioSummary(
            positions=[], total_value=0.0, total_cost=0.0,
            total_pnl_abs=0.0, total_pnl_pct=None, name_count=0,
            hhi=0.0, top_weight=0.0, concentration_bucket="diversified",
        )

    rows: list[Position] = []
    total_value = 0.0
    total_cost = 0.0

    for _, r in h.iterrows():
        ticker = r["ticker"]
        shares = float(r["shares"])
        cost_basis = float(r["cost_basis"])
        last = _last_price(ticker)
        mv = shares * last if last is not None else None
        cost_val = shares * cost_basis
        pnl_abs = (mv - cost_val) if mv is not None else None
        pnl_pct = (pnl_abs / cost_val) if (pnl_abs is not None and cost_val > 0) else None

        rows.append(
            Position(
                ticker=ticker, shares=shares, cost_basis=cost_basis,
                last_price=last, market_value=mv, cost_value=cost_val,
                pnl_abs=pnl_abs, pnl_pct=pnl_pct, weight=None,
            )
        )
        if mv is not None:
            total_value += mv
        total_cost += cost_val

    # Second pass: weights (need total_value).
    weighted: list[Position] = []
    for p in rows:
        w = (p.market_value / total_value) if (p.market_value is not None and total_value > 0) else None
        weighted.append(
            Position(
                ticker=p.ticker, shares=p.shares, cost_basis=p.cost_basis,
                last_price=p.last_price, market_value=p.market_value,
                cost_value=p.cost_value, pnl_abs=p.pnl_abs, pnl_pct=p.pnl_pct,
                weight=w,
            )
        )

    total_pnl_abs = total_value - total_cost
    total_pnl_pct = (total_pnl_abs / total_cost) if total_cost > 0 else None

    weights = [p.weight for p in weighted if p.weight is not None]
    hhi = sum(w * w for w in weights) if weights else 0.0
    top_weight = max(weights) if weights else 0.0

    return PortfolioSummary(
        positions=sorted(weighted, key=lambda p: (p.weight or 0), reverse=True),
        total_value=total_value,
        total_cost=total_cost,
        total_pnl_abs=total_pnl_abs,
        total_pnl_pct=total_pnl_pct,
        name_count=len(weighted),
        hhi=hhi,
        top_weight=top_weight,
        concentration_bucket=_concentration_bucket(hhi, top_weight),
    )


# ---------- Forward Monte Carlo simulation ----------

@dataclass(frozen=True)
class PortfolioSimulation:
    horizon_days: int
    n_sims: int
    lookback_days: int
    current_value: float
    sim_paths: np.ndarray            # shape (n_sims, horizon_days + 1); $ portfolio value
    percentiles: dict[int, np.ndarray]  # {p: ndarray(horizon+1)} for p in [5,25,50,75,95]
    var_95: float                    # 1M absolute $ loss at 5th percentile (positive number)
    var_99: float
    cvar_95: float                   # mean $ loss in the worst 5%
    best_5pct_gain: float            # 95th percentile $ P/L
    median_terminal: float           # median terminal $ value
    var_pct_95: float                # VaR as fraction of current value
    cvar_pct_95: float
    # Per-asset tracking (for component risk decomposition).
    asset_names: tuple[str, ...]
    asset_initial_values: np.ndarray  # (n_assets,)
    asset_terminal_values: np.ndarray  # (n_sims, n_assets)


@dataclass(frozen=True)
class RiskContribution:
    ticker: str
    weight: float                 # current $ weight, 0..1
    component_cvar: float         # $ expected loss contribution in the worst 5%
    contribution_pct: float       # fraction of total CVaR (sums to 1.0)
    risk_multiplier: float        # contribution_pct / weight (1.0 = proportional)


# Historical stress windows. Labels + ISO date ranges (inclusive on both ends).
STRESS_WINDOWS: tuple[tuple[str, str, str, str], ...] = (
    ("Current", "", "", "Last 252 trading days"),
    ("Covid 2020", "2020-02-15", "2020-05-15", "Rapid selloff + Fed response"),
    ("Rate shock 2022", "2022-01-01", "2022-12-31", "Multi-quarter decline, correlation break"),
    ("Q4 2018 bear", "2018-10-01", "2018-12-31", "Sharp equity selloff on tightening"),
)


@dataclass(frozen=True)
class StressResult:
    label: str
    description: str
    window_start: str             # ISO or '' for current
    window_end: str
    n_assets: int                 # how many holdings had data in this window
    var_95: float                 # $ 1M VaR at 95%
    var_pct_95: float
    cvar_95: float
    cvar_pct_95: float
    annualized_portfolio_vol: float
    status: str                   # 'ok' | 'insufficient_data' | 'skipped'


def _aligned_log_returns(
    tickers: list[str], lookback: int
) -> tuple[pd.DataFrame, list[str]]:
    """Return aligned daily log-return matrix + surviving ticker list.

    Tickers with insufficient history are dropped with a warning.
    """
    series: dict[str, pd.Series] = {}
    for t in tickers:
        df = fetch_prices(t, limit=lookback + 10)
        if df.empty or "adj_close" not in df:
            log.warning("No prices for %s, dropping from simulation.", t)
            continue
        r = np.log(df["adj_close"] / df["adj_close"].shift(1)).dropna()
        if len(r) < 30:
            log.warning("Too little history for %s (%d days), dropping.", t, len(r))
            continue
        series[t] = r
    if not series:
        return pd.DataFrame(), []
    frame = pd.DataFrame(series).dropna().tail(lookback)
    return frame, list(frame.columns)


def simulate_portfolio(
    horizon_days: int = 21,
    n_sims: int = 5000,
    lookback_days: int = 252,
    seed: int = 42,
) -> PortfolioSimulation | None:
    """Correlated GBM Monte Carlo on current holdings. Returns None if no data."""
    holdings = list_holdings()
    if holdings.empty:
        return None

    requested = holdings["ticker"].tolist()
    rets, tickers = _aligned_log_returns(requested, lookback_days)
    if rets.empty or not tickers:
        return None

    # Restrict holdings to those we have return history for.
    held = holdings.set_index("ticker").loc[tickers]

    # Current per-asset $ values (shares * latest adj_close).
    current_prices = np.zeros(len(tickers))
    for i, t in enumerate(tickers):
        df = fetch_prices(t, limit=1)
        current_prices[i] = float(df["adj_close"].iloc[-1])
    shares = held["shares"].values.astype(float)
    current_asset_values = shares * current_prices
    V0 = float(current_asset_values.sum())
    if V0 <= 0:
        return None

    # Stats from historical log returns.
    mu = rets.mean().values               # (N,)
    Sigma = rets.cov().values              # (N, N)
    # Jitter for numerical stability on near-singular covariance.
    Sigma_j = Sigma + 1e-10 * np.eye(len(tickers))
    try:
        L = np.linalg.cholesky(Sigma_j)
    except np.linalg.LinAlgError:
        log.error("Covariance not PSD; simulation aborted.")
        return None

    rng = np.random.default_rng(seed)
    n_assets = len(tickers)

    # Initial per-asset values tiled across sims.
    asset_vals = np.tile(current_asset_values, (n_sims, 1))  # (n_sims, n_assets)
    sim_paths = np.empty((n_sims, horizon_days + 1))
    sim_paths[:, 0] = V0

    for d in range(horizon_days):
        Z = rng.standard_normal(size=(n_sims, n_assets))
        # Correlated daily log returns: r = mu + Z @ L.T  (since cov(L Z) = Sigma)
        daily_r = mu[None, :] + Z @ L.T
        asset_vals = asset_vals * np.exp(daily_r)
        sim_paths[:, d + 1] = asset_vals.sum(axis=1)

    terminal = sim_paths[:, -1]
    pnl = terminal - V0

    percentiles = {p: np.percentile(sim_paths, p, axis=0) for p in (5, 25, 50, 75, 95)}

    q5 = np.percentile(pnl, 5)
    q1 = np.percentile(pnl, 1)
    var_95 = float(-q5)
    var_99 = float(-q1)
    tail = pnl[pnl <= q5]
    cvar_95 = float(-tail.mean()) if tail.size > 0 else var_95
    best_5pct_gain = float(np.percentile(pnl, 95))
    median_terminal = float(np.median(terminal))

    return PortfolioSimulation(
        horizon_days=horizon_days,
        n_sims=n_sims,
        lookback_days=lookback_days,
        current_value=V0,
        sim_paths=sim_paths,
        percentiles=percentiles,
        var_95=var_95,
        var_99=var_99,
        cvar_95=cvar_95,
        best_5pct_gain=best_5pct_gain,
        median_terminal=median_terminal,
        var_pct_95=var_95 / V0,
        cvar_pct_95=cvar_95 / V0,
        asset_names=tuple(tickers),
        asset_initial_values=current_asset_values.copy(),
        asset_terminal_values=asset_vals.copy(),  # final loop iteration value
    )


@dataclass(frozen=True)
class BenchmarkComparison:
    benchmark: str
    lookback_days: int
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    # Normalized value time series (base 100).
    dates: pd.DatetimeIndex
    portfolio_normalized: np.ndarray
    benchmark_normalized: np.ndarray
    # Scalar metrics.
    portfolio_return: float           # total % over lookback
    benchmark_return: float
    excess_return: float              # portfolio - benchmark total
    portfolio_vol_ann: float
    benchmark_vol_ann: float
    sharpe_portfolio: float
    sharpe_benchmark: float
    beta: float                       # CAPM slope
    alpha_ann: float                  # CAPM intercept, annualized
    tracking_error_ann: float
    information_ratio: float
    r_squared: float


def _portfolio_value_series(
    holdings: pd.DataFrame, lookback: int
) -> pd.DataFrame | None:
    """Reconstruct daily portfolio $ value assuming current shares held throughout.

    Returns DataFrame indexed by date with single column 'value'. None if any
    position lacks enough history.
    """
    series = {}
    for _, h in holdings.iterrows():
        ticker = h["ticker"]
        shares = float(h["shares"])
        df = fetch_prices(ticker, limit=lookback + 10)
        if df.empty or "adj_close" not in df:
            return None
        series[ticker] = df["adj_close"] * shares
    if not series:
        return None
    frame = pd.DataFrame(series).dropna().tail(lookback)
    if frame.empty:
        return None
    return pd.DataFrame({"value": frame.sum(axis=1)})


def _benchmark_value_series(
    ticker: str, dates: pd.DatetimeIndex
) -> pd.Series | None:
    df = fetch_prices(ticker, limit=len(dates) + 50)
    if df.empty or "adj_close" not in df:
        return None
    aligned = df["adj_close"].reindex(dates).dropna()
    if len(aligned) < max(30, len(dates) // 2):
        return None
    return aligned


def compute_benchmark_comparison(
    benchmark: str = "SPY", lookback_days: int = 252
) -> BenchmarkComparison | None:
    """Compare current portfolio to a benchmark over lookback window.

    Assumption: current shares held throughout the window (approximation).
    """
    h = list_holdings()
    if h.empty:
        return None

    port_df = _portfolio_value_series(h, lookback_days)
    if port_df is None or len(port_df) < 30:
        log.warning("Not enough aligned history to build portfolio value series.")
        return None

    bench = _benchmark_value_series(benchmark, port_df.index)
    if bench is None:
        log.warning("Benchmark %s unavailable or misaligned.", benchmark)
        return None

    # Align both to overlapping dates.
    common = port_df.index.intersection(bench.index)
    port_v = port_df["value"].loc[common].values
    bench_v = bench.loc[common].values
    dates = common

    # Normalized to 100 at start.
    port_norm = 100.0 * port_v / port_v[0]
    bench_norm = 100.0 * bench_v / bench_v[0]

    # Daily simple returns.
    port_ret = np.diff(port_v) / port_v[:-1]
    bench_ret = np.diff(bench_v) / bench_v[:-1]

    # Annualization helpers.
    sq = np.sqrt(252.0)
    port_mu = float(port_ret.mean())
    bench_mu = float(bench_ret.mean())
    port_sd = float(port_ret.std(ddof=1))
    bench_sd = float(bench_ret.std(ddof=1))
    port_vol_ann = port_sd * sq
    bench_vol_ann = bench_sd * sq
    sharpe_p = (port_mu / port_sd) * sq if port_sd > 0 else 0.0
    sharpe_b = (bench_mu / bench_sd) * sq if bench_sd > 0 else 0.0

    total_port = float(port_v[-1] / port_v[0] - 1.0)
    total_bench = float(bench_v[-1] / bench_v[0] - 1.0)

    # OLS regression: r_port = alpha + beta * r_bench.
    # Use numpy polyfit for a clean one-liner.
    if bench_sd > 0:
        beta, alpha = np.polyfit(bench_ret, port_ret, deg=1)
        # R^2 = 1 - SS_res / SS_tot.
        pred = alpha + beta * bench_ret
        ss_res = float(((port_ret - pred) ** 2).sum())
        ss_tot = float(((port_ret - port_mu) ** 2).sum())
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    else:
        beta, alpha, r2 = 0.0, 0.0, 0.0

    # Tracking error + information ratio.
    active = port_ret - bench_ret
    te_ann = float(active.std(ddof=1) * sq)
    excess_ann = float(active.mean() * 252.0)
    ir = excess_ann / te_ann if te_ann > 0 else 0.0

    return BenchmarkComparison(
        benchmark=benchmark,
        lookback_days=len(common),
        start_date=dates[0],
        end_date=dates[-1],
        dates=dates,
        portfolio_normalized=port_norm,
        benchmark_normalized=bench_norm,
        portfolio_return=total_port,
        benchmark_return=total_bench,
        excess_return=total_port - total_bench,
        portfolio_vol_ann=port_vol_ann,
        benchmark_vol_ann=bench_vol_ann,
        sharpe_portfolio=sharpe_p,
        sharpe_benchmark=sharpe_b,
        beta=float(beta),
        alpha_ann=float(alpha * 252.0),
        tracking_error_ann=te_ann,
        information_ratio=float(ir),
        r_squared=float(r2),
    )


@dataclass(frozen=True)
class PositionAttribution:
    ticker: str
    weight: float                     # 0..1 of current portfolio
    total_return: float               # position return over lookback
    benchmark_return: float           # benchmark return over same window (constant across rows)
    excess_return: float              # total - benchmark
    beta: float                       # CAPM slope
    alpha_ann: float                  # CAPM intercept × 252
    r_squared: float                  # goodness of fit
    alpha_contribution_ann: float     # weight * alpha_ann
    label: str                        # 'leveraged_bench' | 'alpha_positive' | 'alpha_negative' | 'divergent' | 'is_benchmark'


def _classify_position(beta: float, alpha_ann: float, r2: float, ticker: str, benchmark: str) -> str:
    if ticker.upper() == benchmark.upper():
        return "is_benchmark"
    # High R² and weak alpha -> just a benchmark proxy with leverage.
    if r2 >= 0.70 and abs(alpha_ann) < 0.03:
        return "leveraged_bench"
    # Low R² = idiosyncratic regardless of alpha sign.
    if r2 < 0.35:
        return "divergent"
    if alpha_ann >= 0.03:
        return "alpha_positive"
    if alpha_ann <= -0.03:
        return "alpha_negative"
    return "leveraged_bench"


def _simulate_from_stats(
    current_asset_values: np.ndarray,
    mu: np.ndarray,
    Sigma: np.ndarray,
    horizon_days: int,
    n_sims: int,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Core correlated-GBM Monte Carlo.

    Returns (sim_paths, asset_terminal_values) for downstream stats.
    """
    Sigma_j = Sigma + 1e-10 * np.eye(len(mu))
    L = np.linalg.cholesky(Sigma_j)
    rng = np.random.default_rng(seed)
    V0 = float(current_asset_values.sum())
    n_assets = len(current_asset_values)

    asset_vals = np.tile(current_asset_values, (n_sims, 1))
    sim_paths = np.empty((n_sims, horizon_days + 1))
    sim_paths[:, 0] = V0

    for d in range(horizon_days):
        Z = rng.standard_normal(size=(n_sims, n_assets))
        daily_r = mu[None, :] + Z @ L.T
        asset_vals = asset_vals * np.exp(daily_r)
        sim_paths[:, d + 1] = asset_vals.sum(axis=1)

    return sim_paths, asset_vals


def _historical_returns_window(
    tickers: list[str], start_iso: str, end_iso: str
) -> tuple[pd.DataFrame, list[str]]:
    """Extract aligned daily log returns over [start_iso, end_iso] for each ticker."""
    series: dict[str, pd.Series] = {}
    start = pd.Timestamp(start_iso)
    end = pd.Timestamp(end_iso)
    for t in tickers:
        # fetch_prices has no date-range arg; grab recent-enough then slice.
        # For older windows we need the whole history.
        df = fetch_prices(t)  # all rows
        if df.empty or "adj_close" not in df:
            continue
        window = df.loc[(df.index >= start) & (df.index <= end), "adj_close"]
        if len(window) < 10:
            continue
        r = np.log(window / window.shift(1)).dropna()
        if len(r) < 10:
            continue
        series[t] = r
    if not series:
        return pd.DataFrame(), []
    frame = pd.DataFrame(series).dropna()
    return frame, list(frame.columns)


def compute_stress_scenarios(
    horizon_days: int = 21, n_sims: int = 2000
) -> list[StressResult]:
    """Run forward MC for each STRESS_WINDOW using that regime's mu/Sigma."""
    holdings = list_holdings()
    if holdings.empty:
        return []
    requested = holdings["ticker"].tolist()

    # Current per-asset $ values (shared across all regimes).
    current_values: dict[str, float] = {}
    for t in requested:
        df = fetch_prices(t, limit=1)
        if df.empty:
            continue
        last = float(df["adj_close"].iloc[-1])
        shares = float(holdings.loc[holdings["ticker"] == t, "shares"].iloc[0])
        current_values[t] = shares * last

    results: list[StressResult] = []
    for label, start, end, desc in STRESS_WINDOWS:
        if label == "Current":
            # Reuse the standard simulation for the baseline row.
            base = simulate_portfolio(horizon_days=horizon_days, n_sims=n_sims)
            if base is None:
                results.append(StressResult(
                    label=label, description=desc, window_start="", window_end="",
                    n_assets=0, var_95=0.0, var_pct_95=0.0,
                    cvar_95=0.0, cvar_pct_95=0.0, annualized_portfolio_vol=0.0,
                    status="skipped",
                ))
                continue
            # Compute portfolio vol from the simulation paths directly.
            daily_rets = np.diff(np.log(base.sim_paths), axis=1)  # (n_sims, horizon)
            port_vol_ann = float(daily_rets.std(ddof=1) * np.sqrt(252))
            results.append(StressResult(
                label=label, description=desc, window_start="", window_end="",
                n_assets=len(base.asset_names),
                var_95=base.var_95, var_pct_95=base.var_pct_95,
                cvar_95=base.cvar_95, cvar_pct_95=base.cvar_pct_95,
                annualized_portfolio_vol=port_vol_ann,
                status="ok",
            ))
            continue

        rets, tickers = _historical_returns_window(requested, start, end)
        if rets.empty or len(tickers) < 2:
            results.append(StressResult(
                label=label, description=desc, window_start=start, window_end=end,
                n_assets=len(tickers), var_95=0.0, var_pct_95=0.0,
                cvar_95=0.0, cvar_pct_95=0.0, annualized_portfolio_vol=0.0,
                status="insufficient_data",
            ))
            continue

        # Build asset_values aligned to the returns columns.
        held_in_window = [t for t in tickers if t in current_values]
        if len(held_in_window) < 2:
            results.append(StressResult(
                label=label, description=desc, window_start=start, window_end=end,
                n_assets=len(held_in_window), var_95=0.0, var_pct_95=0.0,
                cvar_95=0.0, cvar_pct_95=0.0, annualized_portfolio_vol=0.0,
                status="insufficient_data",
            ))
            continue

        asset_values = np.array([current_values[t] for t in held_in_window])
        rets_window = rets[held_in_window]
        mu = rets_window.mean().values
        Sigma = rets_window.cov().values

        try:
            sim_paths, _ = _simulate_from_stats(
                asset_values, mu, Sigma, horizon_days, n_sims, seed=42,
            )
        except np.linalg.LinAlgError:
            results.append(StressResult(
                label=label, description=desc, window_start=start, window_end=end,
                n_assets=len(held_in_window), var_95=0.0, var_pct_95=0.0,
                cvar_95=0.0, cvar_pct_95=0.0, annualized_portfolio_vol=0.0,
                status="insufficient_data",
            ))
            continue

        V0 = float(asset_values.sum())
        terminal = sim_paths[:, -1]
        pnl = terminal - V0
        q5 = np.percentile(pnl, 5)
        var_95 = float(-q5)
        tail = pnl[pnl <= q5]
        cvar_95 = float(-tail.mean()) if tail.size > 0 else var_95

        daily_rets = np.diff(np.log(sim_paths), axis=1)
        port_vol_ann = float(daily_rets.std(ddof=1) * np.sqrt(252))

        results.append(StressResult(
            label=label, description=desc, window_start=start, window_end=end,
            n_assets=len(held_in_window), var_95=var_95, var_pct_95=var_95 / V0,
            cvar_95=cvar_95, cvar_pct_95=cvar_95 / V0,
            annualized_portfolio_vol=port_vol_ann,
            status="ok",
        ))
    return results


def compute_position_attributions(
    benchmark: str = "SPY", lookback_days: int = 252
) -> list[PositionAttribution]:
    """Per-position CAPM attribution vs a benchmark.

    Reuses the portfolio's current weights for alpha contribution. Positions
    with insufficient aligned history are skipped with a warning.
    """
    h = list_holdings()
    if h.empty:
        return []

    # Current weights (market value basis).
    summary = build_portfolio()
    weight_map = {p.ticker: (p.weight or 0.0) for p in summary.positions}

    # Benchmark returns, aligned.
    bench_df = fetch_prices(benchmark, limit=lookback_days + 10)
    if bench_df.empty:
        log.warning("Benchmark %s unavailable.", benchmark)
        return []
    bench_px = bench_df["adj_close"].tail(lookback_days)
    bench_ret = bench_px.pct_change().dropna()
    total_bench = float(bench_px.iloc[-1] / bench_px.iloc[0] - 1.0)

    out: list[PositionAttribution] = []
    for ticker in h["ticker"].tolist():
        asset_df = fetch_prices(ticker, limit=lookback_days + 10)
        if asset_df.empty or "adj_close" not in asset_df:
            log.warning("No prices for %s in attribution.", ticker)
            continue
        asset_px = asset_df["adj_close"].reindex(bench_px.index).dropna()
        if len(asset_px) < 30:
            log.warning("Too little aligned history for %s attribution.", ticker)
            continue

        asset_ret = asset_px.pct_change().dropna()
        # Re-align after drops.
        common = asset_ret.index.intersection(bench_ret.index)
        r_i = asset_ret.loc[common].values
        r_b = bench_ret.loc[common].values

        if len(r_i) < 30 or r_b.std(ddof=1) == 0:
            continue

        beta, alpha = np.polyfit(r_b, r_i, deg=1)
        alpha_ann = float(alpha * 252.0)

        pred = alpha + beta * r_b
        ss_res = float(((r_i - pred) ** 2).sum())
        ss_tot = float(((r_i - r_i.mean()) ** 2).sum())
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        total_ret = float(asset_px.iloc[-1] / asset_px.iloc[0] - 1.0)
        weight = float(weight_map.get(ticker, 0.0))

        out.append(
            PositionAttribution(
                ticker=ticker,
                weight=weight,
                total_return=total_ret,
                benchmark_return=total_bench,
                excess_return=total_ret - total_bench,
                beta=float(beta),
                alpha_ann=alpha_ann,
                r_squared=float(r2),
                alpha_contribution_ann=weight * alpha_ann,
                label=_classify_position(float(beta), alpha_ann, float(r2), ticker, benchmark),
            )
        )
    out.sort(key=lambda a: a.alpha_contribution_ann, reverse=True)
    return out


def compute_risk_contributions(sim: PortfolioSimulation) -> list[RiskContribution]:
    """Decompose portfolio tail loss into per-asset contributions.

    Uses Component CVaR: for the worst 5% of trials, compute the mean per-asset
    P/L. These sum exactly to -CVaR (by linearity of expectation). Positive
    values = loss contribution.
    """
    # Per-asset P/L per trial: shape (n_sims, n_assets).
    asset_pnl = sim.asset_terminal_values - sim.asset_initial_values[None, :]
    # Portfolio P/L per trial.
    portfolio_pnl = asset_pnl.sum(axis=1)

    # Select the worst 5% of trials by total portfolio P/L.
    threshold = np.percentile(portfolio_pnl, 5)
    tail_mask = portfolio_pnl <= threshold
    if tail_mask.sum() == 0:
        return []

    # Mean per-asset P/L in the tail (negative for losing assets).
    tail_mean_per_asset = asset_pnl[tail_mask].mean(axis=0)  # (n_assets,)
    # Loss contribution (positive number).
    component_losses = -tail_mean_per_asset
    total_loss = component_losses.sum()  # equals CVaR by construction

    if total_loss <= 0:
        return []

    total_value = float(sim.asset_initial_values.sum())
    out: list[RiskContribution] = []
    for i, ticker in enumerate(sim.asset_names):
        w = float(sim.asset_initial_values[i]) / total_value
        c = float(component_losses[i])
        pct = c / total_loss
        mult = pct / w if w > 0 else 0.0
        out.append(
            RiskContribution(
                ticker=ticker,
                weight=w,
                component_cvar=c,
                contribution_pct=pct,
                risk_multiplier=mult,
            )
        )
    # Sort by absolute contribution descending.
    out.sort(key=lambda rc: rc.component_cvar, reverse=True)
    return out
