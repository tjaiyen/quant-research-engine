# Architecture

Single-process Dash app with a SQLite backend. Heavy compute is pure-Python;
no async, no external services beyond yfinance for data.

## Data flow

```
                        ┌─────────────────────┐
                        │  yfinance (Yahoo)   │
                        └──────────┬──────────┘
                                   │
                                   ▼
                  ┌────────────────────────────────┐
                  │ data_providers/                │  ← FROZEN; not modified directly
                  │ yfinance_provider.py           │
                  └──────────┬─────────────────────┘
                             │
                  ┌──────────▼──────────┐
                  │ data_fetcher.py     │  ← Tier detection + feature gating
                  └──────────┬──────────┘
                             │
       ┌─────────────────────┼─────────────────────┐
       │                     │                     │
       ▼                     ▼                     ▼
 tasks/refresh_     tasks/refresh_         tasks/precompute_
   prices.py        sectors.py              mc.py
       │                     │                     │
       └─────────────────────┴─────────────────────┘
                             │
                             ▼
                  ┌──────────────────────────┐
                  │ db/cockpit.sqlite        │
                  │   tickers · prices       │
                  │   fundamentals · holdings│
                  │   mc_results             │
                  │   valuation_cache        │
                  │   sector_perf            │
                  │  (WAL mode, timeout=30)  │
                  └──────────┬───────────────┘
                             │
   ┌─────────────────────────┼──────────────────────────┐
   ▼              ▼          ▼            ▼             ▼
models_*    fundamental.py  models_   sector_      quant_models.py
(per-ticker  + industry_   portfolio  rotation.py   (HMM, BS,
 signals)    config.py)     .py                     MC reader)
   │              │          │            │             │
   └──────────────┼──────────┴────────────┴─────────────┘
                  │
       ┌──────────▼──────────┐
       │ scoring_legacy.py   │  ← 0–1 composite (Overview)
       │ scoring_ars.py      │  ← 0–100 ARS (Tab 6)
       │ suggestions.py      │  ← trim/add engine
       └──────────┬──────────┘
                  │
                  ▼
       ┌──────────────────────────────────────┐
       │ ui/  components, metrics             │
       │      technical, fundamental_tab      │
       │      sector_tab, ars_tab, portfolio  │
       └──────────┬───────────────────────────┘
                  │
                  ▼
            app.py  (Dash entrypoint, 6 tabs)
                  │
                  ▼
        http://127.0.0.1:8050
```

## Module responsibilities

### Data layer
| Module | Responsibility |
|---|---|
| `data_providers/yfinance_provider.py` | **FROZEN** — Fetch daily adjusted prices + `.info` fundamentals. Pure. |
| `data_fetcher.py` | Wraps `yfinance_provider`. 3-tier confidence detection, feature gating, re-exports. |
| `utils/db.py` | SQLite connection (WAL, timeout=30), idempotent upserts, row→DataFrame reads. |
| `utils/config.py` | `.env` loading, typed settings. |
| `utils/logging_setup.py` | Rotating file + console logger. |
| `schema.sql` (repo root) | Source of truth for table structure. **Additive only**, never DROP. Lives at the repo root, NOT under `db/`, so the production volume mount at `/app/db` does not shadow it. |

### Signal layer (per-ticker)
| Module | Input | Output |
|---|---|---|
| `models_technical.py` | Price DataFrame | `TechnicalSignals` (trend, RSI, MACD, momentum) |
| `models_quant.py` | Price DataFrame | `RiskSignals` (vol, drawdown, regime) |
| `models_fundamental.py` | Fundamentals snapshot + peer context | `ValuationSignals` (peer-relative bands) |
| `fundamental.py` | Fundamentals snapshot + price | `FundamentalValuation` (DCF + DDM + multiples + composite) |
| `quant_models.py` | Price DataFrame | `RegimeResult` (HMM or rolling-vol) + Black-Scholes pricing |

### Aggregate layer
| Function | Output | Math |
|---|---|---|
| `build_portfolio()` | `PortfolioSummary` | Σ shares × last adj_close; HHI |
| `simulate_portfolio()` | `PortfolioSimulation` | Correlated GBM Monte Carlo |
| `compute_risk_contributions()` | `list[RiskContribution]` | Component CVaR (Euler, tail-conditional) |
| `compute_benchmark_comparison()` | `BenchmarkComparison` | OLS regression of portfolio returns on SPY |
| `compute_position_attributions()` | `list[PositionAttribution]` | Per-ticker CAPM regression vs SPY |
| `compute_stress_scenarios()` | `list[StressResult]` | Same MC, regime-specific Σ |
| `latest_sector_perf()` | `list[SectorRow]` | Most-recent sector_perf rows by score |

### Decision layer
| Module | Produces | Scale |
|---|---|---|
| `scoring_legacy.py` | `CompositeScore` (Overview chips, watchlist ranking) | 0–1 |
| `scoring_ars.py` | `ARSScore` (Tab 6 gauge + NL summary) | 0–100, 5 components, sector tilts |
| `suggestions.py` | `TrimCandidate` / `AddCandidate` | both gated at 0.50 intensity |

### UI layer
| Module | Tab |
|---|---|
| `ui/components.py` | Color tokens, KPI card, score chips, suggestions card (Tab 1 helpers) |
| `ui/metrics.py` | Overview return calcs |
| `ui/technical.py` | Tab 2 |
| `ui/fundamental_tab.py` | Tab 3 (verdict chip, band chart, multiples table, DCF sensitivity, regime chip) |
| `ui/sector_tab.py` | Tab 4 (signal table, RRG quadrant, returns heatmap, RS chart) |
| `ui/ars_tab.py` | Tab 5 (gauge, NL card, component table, Top-N) |
| `ui/portfolio.py` | Tab 6 (KPIs, benchmark, attribution, MC fan, stress comparison, risk contribution, holdings) |

### App shell
| File | Role |
|---|---|
| `app.py` | Layout, **6 tabs**, callbacks, global tier badge in header |

## Frozen infrastructure

These files must not be modified:
- `Dockerfile`, `fly.toml`, `.dockerignore`, `DEPLOY.md`
- `tasks/refresh_prices.py`, `tasks/manage_holdings.py`
- Docker `HEALTHCHECK curl /` endpoint
- Gunicorn entrypoint (`from app import server`)
- `data_providers/yfinance_provider.py`
- Existing schema tables (`tickers`, `prices`, `fundamentals`, `holdings`)

## Cache-only Monte Carlo policy

Monte Carlo is expensive and must never run live in a Dash callback. The flow is:

```
tasks/precompute_mc.py  →  mc_results table  →  quant_models.fetch_cached_mc()  →  UI
   (writes only)            (cache)              (reads only)                       (renders)
```

`models_portfolio.simulate_portfolio()` is the legacy live path — kept for the
Portfolio tab callback that already exists. New MC consumers must read the
cache through `quant_models`.

## SQLite governance

- **WAL mode** (`PRAGMA journal_mode=WAL`) enabled in `_connect()`.
- **timeout=30** seconds on every connection — pairs with WAL to allow
  concurrent reads + one writer without lock starvation.
- All schema additions are `CREATE TABLE IF NOT EXISTS`. Never drop.
- Migration runs on every `init_db()` call (idempotent).

## Data tier gating

`data_fetcher.detect_data_tier()` returns 1, 2, or 3 based on env keys:

| Tier | Trigger | Confidence | Disabled features |
|---|---|---|---|
| 1 | `REFINITIV_API_KEY` or `BLOOMBERG_API_KEY` set | 1.00 | none |
| 2 | `FMP_API_KEY` or `POLYGON_API_KEY` set | 0.75 | iv_surface, intraday_bars, consensus, fundamentals_history |
| 3 | yfinance default (current) | 0.50 | + forward_peg, ev_ebitda_history, options_chain, earnings_surprises |

The badge `⚠️ REDUCED DATA CONFIDENCE` appears in the global header at Tier 3.

## Numerical governance

- Scoring outputs in `[0, 1]` (legacy) or `[0, 100]` (ARS) — verified in health check.
- Portfolio weights sum to 1.0 (or `None` — never fake).
- SPY self-regression is a built-in unit test: β≈1, α≈0, R²≈1.
- Component CVaR satisfies `Σ_i C_i = portfolio CVaR` (linearity of expectation).
- Stress regimes verified to produce VaR ≥ 0.8× baseline.
- Deterministic seed (`42`) in Monte Carlo.

## Concurrency model

- Single-process Dash with debug reloader during dev.
- SQLite WAL mode + 30s timeout for safe concurrent read + one writer.
- Live compute (signals, scoring, fundamental valuation) ≈ 100 ms per ticker.
- Live MC is forbidden — read from cache.
- Heavy callbacks wrapped in `dcc.Loading`.
- Production: gunicorn with **workers=1** until memory profiled post-deploy.

## Adding a new tab

1. Create `ui/<name>_tab.py` with a `<name>_tab_content(...)` builder.
2. Add a `dcc.Tab(label=..., value=...)` to the `tabs` list in `app.py`.
3. Add a placeholder `html.Div(id=...)` and route it in `_render_tab`.
4. Add a callback bound to `Input("ticker-picker", "value")` and/or `Input("main-tabs", "value")` that returns the rendered content.

## Adding a new signal dimension

1. Create a new `models_<name>.py` returning a frozen dataclass.
2. Add a constant to `BASE_WEIGHTS` in `scoring_ars.py` and a tilt entry in
   `industry_config.WeightTilt`.
3. Extend `compute_ars()` to source the new component.
4. Add a chip / column in `ui/ars_tab.py:component_breakdown`.
