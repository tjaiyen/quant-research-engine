# Architecture

A local, single-process Python engine with a SQLite cache. Heavy compute is
pure-Python (no async, no services beyond yfinance for data). There is **no web
server and no hosting** — a CLI runs the engine on a schedule and renders the
results as Markdown + a self-contained HTML dashboard into an Obsidian vault.

> History: the analytical engine was ported from an earlier Dash/Plotly web app
> ("Quant Cockpit") on Fly.io. The web shell (`ui/`, `app.py`, `Dockerfile`,
> `fly.toml`, gunicorn) was dropped; the engine below is what was kept and grown.

## Data flow

```
        ┌─────────────────────┐
        │  yfinance (Yahoo)   │
        └──────────┬──────────┘
                   ▼
   ┌────────────────────────────────┐
   │ data_providers/                │  ← FROZEN; never edited directly
   │ yfinance_provider.py           │
   └──────────┬─────────────────────┘
              ▼
   ┌─────────────────────┐
   │ data_fetcher.py     │  ← yfinance→Stooq fallback (U8), tier gating
   └──────────┬──────────┘
              ▼
   ┌──────────────────────────────────────┐
   │ store/cockpit.sqlite   (off-Drive)   │  prices · fundamentals · holdings
   │ store/portfolio.db                   │  earnings_calendar · news_sentiment
   │ (WAL, timeout=30; rebuildable cache) │  company_health · screener_runs/results
   └──────────┬───────────────────────────┘
              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ screener/  (the intelligence)                                 │
   │   regime/hmm_*          → market regime (bull/sideways/bear)  │
   │   signals/{arima,kalman,garch,monte_carlo,sharpe,momentum}    │
   │   engine/composite_scorer + industry_ranker                   │
   │     → 5-signal regime-weighted composite, per-sector ranking  │
   │   engine/*_guard (8 vetoes: earnings blackout, stale, …)      │
   └──────────┬───────────────────────────────────────────────────┘
              ▼
   ┌────────────────────────────┐        ┌──────────────────────────────┐
   │ auto_trader/  (paper only) │        │ screener/{backtest,tournament,│
   │   broker/mock  + ledger    │        │   signal_lab, rigor}          │
   │   risk/ 8 guards           │        │   walk-forward · IC · DSR ·   │
   │   allocator/kelly_sizing   │        │   CPCV · cost haircut         │
   │   monitor/ decay + stops   │        │   (on-demand validation)      │
   └──────────┬─────────────────┘        └──────────────┬───────────────┘
              └──────────────┬──────────────────────────┘
                             ▼
        ┌────────────────────────────────────────────┐
        │ render/  (the surface)                      │
        │   notes.py / build.py → Markdown + frontmat.│
        │   html.py → self-contained Dashboard.html   │
        │   glossary.py → educational definitions     │
        └──────────┬─────────────────────────────────┘
                   ▼
        Obsidian vault  ·  90 Tracker/*.md + Dashboard.html
                   ▲
        cli/track.py  ──  the driver (doctor-gated rituals)
```

## Module responsibilities

### Data layer
| Module | Responsibility |
|---|---|
| `data_providers/yfinance_provider.py` | **FROZEN** — daily adjusted prices + `.info` fundamentals. Pure; never edited. |
| `data_providers/stooq_provider.py` | Emergency price fallback (matches the yfinance schema). |
| `data_fetcher.py` | Chokepoint over the providers: yfinance→Stooq fallback, tier gating, re-exports. |
| `utils/db.py` | SQLite connection (WAL, `timeout=30`), idempotent upserts, row→DataFrame reads. |
| `utils/config.py` | `.env` loading (incl. `VAULT_PATH`), typed settings. |
| `schema.sql` (repo root) | Source of truth for table structure. **Additive only**, never DROP. |

### Signal layer (per-ticker, causal)
| Module | Output |
|---|---|
| `screener/regime/hmm_*` | `RegimeResult` — HMM market regime (bull / sideways / bear) + confidence. |
| `screener/signals/arima_signal` … `sharpe_signal`, `momentum_signal` | One score in `[0,1]` per signal (ARIMA, Kalman, GARCH, Monte-Carlo, Sharpe, 12-1 momentum). |
| `screener/engine/composite_scorer.py` | Regime-weighted 5-signal composite + the veto application. |
| `screener/engine/*_guard.py` | 8 vetoes (earnings blackout, delisting/stale, sector exposure, …) — a veto zeroes the composite and is never relaxed. |

### Aggregate / selection layer
| Module | Output |
|---|---|
| `screener/engine/industry_ranker.py` | Per-sector top-N ranking from the composite (the screener's picks). |
| `auto_trader/allocator/kelly_sizing.py` | Position sizing from conviction + risk. |
| `auto_trader/risk/*` | The 8 risk guards run before any (paper) fill. |
| `auto_trader/state/portfolio_db.py` | Append-only paper ledger: positions, fills, equity-curve snapshots, system events. |

### Validation layer (on-demand, off the hot path)
| Module | Produces |
|---|---|
| `screener/backtest/*` | Walk-forward lift, signal IC, regime predictive power, strategy portfolio sim. |
| `screener/tournament/*` | ~20 strategy variants raced over history (causal panel), winner + attribution. |
| `screener/signal_lab/*` | Per-signal IC, quintile spread, Bonferroni-corrected significance flag. |
| `screener/rigor/*` | Transaction-cost haircut, Deflated Sharpe Ratio, Combinatorial Purged CV. |

### Surface layer
| Module | Role |
|---|---|
| `render/build.py` | Reads the cache + ledger, assembles the data, writes every note atomically. |
| `render/notes.py` | Pure engine-object → Markdown (YAML frontmatter for Dataview). |
| `render/html.py` | Pure → a single self-contained `Dashboard.html` (inline CSS/JS, hand-rolled SVG charts, tooltips + glossary; no external libraries). |
| `render/glossary.py` | One definition registry; a completeness gate fails the build if any metric ships without a `?`. |
| `cli/track.py` | The driver — `doctor · refresh · seed · screen · paper · report · score · backtest · tournament · …`. |

## Invariants & governance

**Off-Drive invariant.** Code, venv, and the SQLite cache live in the repo
(off any cloud-synced folder); only the rendered Markdown + HTML live in the
Obsidian vault. `doctor.py` enforces this (store on a local filesystem, vault on
the sync mount) and runs before every DB/vault-touching command.

**Frozen provider / additive schema.** `data_providers/yfinance_provider.py` is
never modified; new data needs (sentiment, health, earnings) add their own
fetchers. `schema.sql` is `CREATE TABLE IF NOT EXISTS` only — never an ALTER or
DROP — so `init_db()` is idempotent across versions.

**Paper-only.** `auto_trader` defaults to a mock broker. The two hard
live-trading gates in `auto_trader/credentials.py` (a minimum paper duration +
an explicit `LIVE_TRADING_CONFIRMED` token) are preserved and never weakened.

**Untrusted external data = data, never instructions.** All yfinance / scraped
text is treated as data; the renderer only ever *writes* derived notes and never
reads vault notes back as instructions. User-supplied strings (tickers, regime
labels) are HTML-escaped in the dashboard.

**Numerical discipline.** Deterministic seeds in Monte-Carlo / clustering;
portfolio weights sum to 1.0 or are `None` (never faked); validation is
in-sample-aware and reported with controls (SPY / random) — the engine is framed
as evidence of method, not a profit claim.

## SQLite governance
- **WAL mode** + `timeout=30s` on every connection (concurrent reads + one writer).
- All schema additions are `CREATE TABLE IF NOT EXISTS`; migration runs on every
  `init_db()` (idempotent). The cache is rebuildable from yfinance — never the
  source of truth.

## Scheduling
`bin/scheduled-run.sh <daily|weekly|monthly>` + three `deploy/*.plist` launchd
agents run the cadence (weekly screen, daily monitor, monthly buy cycle), each
doctor-gated, idempotent, with no missed-run catch-up (cold-start safe).
