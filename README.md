# Quant Tracker

A local, paper-only **equity research engine**: a regime-aware screener (HMM +
five quantitative signals + an 8-guard veto layer) feeding a paper auto-trader,
with the results rendered as a self-contained HTML dashboard and Markdown notes
in an Obsidian vault. No web server, no hosting, no API keys to run the screener.

What makes it more than a backtest toy is the **validation layer**: the engine
doesn't just produce picks, it measures — honestly — whether those picks have any
edge, using walk-forward cross-validation, a strategy tournament, per-signal
Information Coefficients, transaction-cost haircuts, the Deflated Sharpe Ratio,
and Combinatorial Purged Cross-Validation.

**The honest result:** on 3 years of daily data, the portfolio-level edge
*survives* the multiple-comparison-corrected Deflated Sharpe test (DSR ≈ 0.99) and
is **not** cost-fragile (low turnover), but **no individual signal clears a
Bonferroni-corrected significance bar** (ARIMA's IC info-ratio is +2.13, under the
2.58 threshold). So the conclusion the tool reaches about itself is: *suggestive,
not proven — re-weighting five weak signals can't manufacture an edge; the real
next step is new signals, and forward paper data is the arbiter.* Building the
thing is half the work; **proving what it is and isn't** is the other half.

> Paper-trading research only. **Not investment advice.** No real-money execution.

## What this demonstrates

- **Quantitative modeling** — an HMM market-regime classifier; a 5-signal
  composite (ARIMA, Kalman filter, GARCH, Monte-Carlo, Sharpe) blended by regime;
  a 12-1 momentum signal IC-gated and held pending forward evidence.
- **Validation rigor** — walk-forward, IC + quintile spread, a 20-variant
  strategy tournament with controls (SPY / random), cost haircuts, **Deflated
  Sharpe Ratio** and **Combinatorial Purged CV** (De Prado), and Bonferroni
  multiple-testing correction. The codebase argues *against itself* where the
  evidence is weak.
- **Systems & engineering discipline** — a frozen data-provider boundary, an
  additive-only SQLite schema, a deterministic doctor preflight that enforces an
  off-disk-cache invariant, an append-only paper ledger, ~180 tests, and
  launchd-scheduled autonomous runs.
- **Self-contained tooling** — a single `Dashboard.html` with hand-rolled inline
  SVG charts, tooltips, a learn-mode, and a glossary — zero external JS/CSS,
  works offline.

> **▶ See it:** open **[`examples/Dashboard.html`](examples/Dashboard.html)** in a
> browser (download raw, or clone and open the file) — a real rendered dashboard
> with the equity curve, regime, signal attribution, sector breakdown, and the
> validation cards. One file, no dependencies.

## Architecture (60-second version)

```
yfinance → data_fetcher (Stooq fallback) → SQLite cache (off-disk)
   → screener/  HMM regime + 5 signals + 8 vetoes → per-sector ranking
   → auto_trader/  mock broker + 8 risk guards + Kelly sizing + append-only ledger
   → screener/{backtest,tournament,signal_lab,rigor}  on-demand validation
   → render/  Markdown notes + self-contained Dashboard.html
   ↑ cli/track.py  drives it all, doctor-gated
```

Full detail in **[ARCHITECTURE.md](ARCHITECTURE.md)**; the engineering story and
the honest edge analysis in **[docs/CASE_STUDY.md](docs/CASE_STUDY.md)**.

## Quick start

```bash
git clone <this-repo> quant-tracker && cd quant-tracker
python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env          # defaults are paper/mock; set VAULT_PATH to your Obsidian vault

# Render targets an Obsidian vault. Point VAULT_PATH at it (any folder works):
export VAULT_PATH="$HOME/Obsidian/Investment_AI"

./track doctor                # preflight (cache local, vault reachable)
./track seed --full           # first run: seed the 220-stock universe (~30 min, network)
./track screen                # regime-aware screen (weekly; ~minutes)
./track report                # render Markdown + Dashboard.html into the vault
./track tournament            # race ~20 strategy variants over history (~15-25 min)
./track signal-lab            # per-signal IC + Bonferroni significance
./track status                # quick terminal summary
```

No Obsidian? The engine still runs and `./track status` / the validation commands
print to the terminal; `Dashboard.html` opens standalone in any browser.

## Tests

```bash
./.venv/bin/python -m pytest -q                                  # engine + render (~180 tests)
TRADER_DB_PATH=store/test.db ./.venv/bin/python -m pytest auto_trader/tests -q
```

## Safety

Paper-only by default (`TRADING_MODE=paper`, `ALPACA_USE_MOCK=true`). The two hard
live-trading gates in `auto_trader/credentials.py` (minimum paper duration +
explicit `LIVE_TRADING_CONFIRMED`) are preserved unchanged. Data is Tier-3
yfinance (free, daily); forward-PEG / IV-surface / consensus features stay gated.
Nothing here is financial advice.

## License

[MIT](LICENSE).
