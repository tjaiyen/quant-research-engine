# Quant Tracker

An **Obsidian-native paper-trading research system**. The regime-aware screener,
signal models, risk guards, and paper auto-trader ported from the *Quant Cockpit*
(its Dash/Plotly web shell and Fly.io deployment were dropped) — driven by a local
CLI that renders all results as Markdown into an Obsidian vault. No web server,
no hosting.

Built for educational/research use. Paper-trading only. **Not investment advice.**

## Architecture

- **Engine** (ported, off-Drive): `screener/` (HMM regime + ARIMA/Kalman/GARCH/
  Monte-Carlo/Sharpe + 8-guard veto), `auto_trader/` (mock broker, risk guards,
  Kelly-style sizing, append-only ledger), `models_*`/`fundamental`/`quant_models`/
  `scoring_*` (signals + valuation + scoring), `tasks/` (yfinance refresh).
- **Store** (off-Drive, rebuildable): SQLite under `store/` — `cockpit.sqlite`
  (prices, fundamentals, screener runs) + `portfolio.db` (paper positions, fills,
  equity curve). Never committed; never on Drive.
- **Renderer** (new): `render/` turns engine objects into Markdown notes with YAML
  frontmatter and writes them atomically into the vault's `90 Tracker/` folder,
  where **Dataview** renders the dashboards.
- **CLI** (new): `cli/track.py` — the engine driver.

The **off-Drive invariant**: code, venv, and SQLite live here at
`~/dev/quant-tracker`; only Markdown lives in the Google-Drive vault. `doctor.py`
enforces it and runs before every DB/vault-touching command.

## Quick start

```bash
cd ~/dev/quant-tracker
python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env   # defaults are paper/mock; DBs point at store/

./track doctor         # off-Drive preflight (store local, vault canonical)
./track refresh        # pull watchlist prices + sector ETF performance (daily)
./track seed --full    # first run: seed the 220-stock universe into the cache (~30 min)
./track screen         # run the regime-aware screener (weekly; ~minutes, network)
./track paper monitor  # daily: stops, decay rescore, equity snapshot
./track paper cycle    # monthly buy cycle (no-op outside the 1st–5th window)
./track report         # regenerate the Obsidian notes in `90 Tracker/`
./track score          # grade past picks vs actual returns (Scorecard.md)
./track backtest       # retrospective skill check (Backtest.md; ~15 min, sampled)
./track status         # quick terminal summary
```

## Vault output (`Investment_AI/90 Tracker/`)

`Dashboard.md` · `Regime.md` · `Screener/Run-<date>.md` · `Positions/<TICKER>.md` ·
`Journal/<date>.md` · `Performance.md` — all auto-generated; do not hand-edit.
Dashboard/Performance need the Dataview community plugin.

## Tests

```bash
./.venv/bin/python -m pytest -q                                  # engine + render
TRADER_DB_PATH=store/test.db ./.venv/bin/python -m pytest auto_trader/tests -q
```

## Safety

Paper-only by default (`TRADING_MODE=paper`, `ALPACA_USE_MOCK=true`). The two hard
live-trading gates in `auto_trader/credentials.py` (3-month paper duration +
explicit `LIVE_TRADING_CONFIRMED`) are preserved unchanged. Tier-3 yfinance data
carries forward (forward PEG / IV surface / consensus stay gated).

> Data flow and module responsibilities are documented in
> [ARCHITECTURE.md](ARCHITECTURE.md) (describes the ported engine layers; the
> Dash UI layer it references has been removed).
