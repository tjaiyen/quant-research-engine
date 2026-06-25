# Case Study — Quant Tracker

*A regime-aware equity screener that measures, honestly, whether its own picks
have an edge — and concludes they're suggestive but unproven.*

This document is the engineering narrative behind the code: the problem, the
design decisions, and the part most personal projects skip — **rigorously
testing whether the strategy actually works, and reporting the answer even when
it's "not yet."**

---

## 1. The problem

Most retail "stock screeners" rank tickers by some composite score and stop
there. The hard question — *do high-scored picks actually outperform, out of
sample, net of costs, after you account for having tried many variants?* — is
left unanswered, because answering it honestly usually means the edge evaporates.

The goal here was the opposite: build a screener whose **primary output is a
calibrated belief about its own skill**, not a list of "buys." The picks are a
by-product; the validation is the product.

## 2. The engine

**Universe.** 217 large-caps across the 11 GICS sectors (dual-class shares
de-duplicated so one company never occupies two slots).

**Regime.** A Hidden Markov Model labels the market bull / sideways / bear with a
confidence. Signal weights are blended by regime.

**Signals.** Five quantitative signals, each scored to `[0,1]` per ticker:
ARIMA (trend/mean-reversion), a Kalman filter (smoothed trend), GARCH (volatility
regime), Monte-Carlo (simulated forward distribution), and a Sharpe-style
risk-adjusted momentum. A 12-1 momentum signal is implemented and measured but
**held out of the live weights** pending forward evidence (see §4).

**Vetoes.** Eight guards can zero a candidate regardless of score — earnings
blackout, delisting/stale data, sector-exposure caps, drawdown circuit, etc. A
vetoed name is *never* resurrected by the score-relaxation loop.

**Paper execution.** Selected names flow to a mock broker with score- and
volatility-weighted (vol-parity) sizing and an append-only ledger (positions, fills, daily equity snapshots). The
live-trading path exists but is gated behind a minimum paper duration and an
explicit confirmation token — never weakened.

## 3. The validation layer (the actual point)

Each of these runs on-demand over a **causal panel** (history sliced to the
as-of date — no look-ahead), so they're cheap re-passes over cached signal
scores rather than re-screens:

- **Walk-forward & signal IC** — do high scores rank-predict forward returns?
- **A 20-variant tournament** — equal-weight vs Sharpe-only vs the validated
  matrix vs regime-blended vs sizing/concentration variants — *with controls*
  (SPY buy-hold, equal-weight universe, random-20). In-sample pick, out-of-sample
  report. If nothing beats random, the report says so.
- **Transaction-cost haircut** — turnover × round-trip bps, applied uniformly so
  the leaderboard is net of cost.
- **Deflated Sharpe Ratio** (Bailey & López de Prado) — discounts the best
  observed Sharpe by the *number of variants tried* and their variance.
- **Combinatorial Purged Cross-Validation** (De Prado) — replaces a single
  in-sample→OOS split with many purged + embargoed folds, yielding a
  *distribution* of out-of-sample excess return, not one lucky number.
- **Bonferroni-corrected signal significance** — each signal's IC info-ratio is
  tested against a multiple-comparison-corrected bar before it's trusted.

## 4. The honest result

Re-validating the live ARIMA+Sharpe weighting through the full rigor cluster on
~3 years of daily data:

| Test | Result | Reading |
|---|---|---|
| Transaction cost | 73.0% → 69.8% at 40bps | **Not** cost-fragile (low quarterly turnover) |
| Deflated Sharpe Ratio | **DSR ≈ 0.989** (SR 1.25 vs 0.55 expected-max under null, 20 trials) | Portfolio-level edge **survives** multiple-comparison correction |
| Combinatorial Purged CV | 15 folds, mean excess **+3.8%**, **73% folds positive** (std 6.9%) | Positive but **wide** — small-sample noise |
| Bonferroni signal significance | ARIMA IR **+2.13** (< 2.576 bar); no signal clears it | The underlying signal is **suggestive, not significant** |

**Verdict the tool reaches about itself:** *real enough to keep live at moderate
confidence, but not proven.* The portfolio edge passes a return-distribution test
(DSR), yet no single signal is statistically significant once you correct for
having tried five of them, and the OOS spread is wide on ten quarterly
observations. Two more caveats I won't bury: the candidate beats a *random*
control out-of-sample by only **+0.8pp** (and random itself beat SPY in this
mostly-up window), and a **simpler** equal-ish weighting actually posts a higher
OOS Sharpe (4.27 vs the candidate's 3.47). Re-weighting five weak signals cannot
manufacture an edge — the honest next step is **new signals**, and live forward
paper data is the real arbiter.

A worked example of the discipline: adding the 12-1 momentum signal *improved*
the in-sample OOS numbers (Sharpe 3.47 → 4.66, CPCV positive-folds 73% → 87%) —
but momentum was **+0.86 correlated** with the existing Sharpe signal (so it
partly re-expressed information already present) and the gain held on the same
favourable sample where even a *random* strategy scored DSR 0.96. Decision:
**hold momentum out of the live weights** until forward data justifies it, rather
than bank an overfit improvement.

## 5. Engineering decisions worth noting

- **Frozen provider boundary** — the yfinance adapter is never edited; every new
  data need (sentiment, company health, earnings history) adds its own fetcher
  behind the same contract, with a Stooq fallback so one bad provider day doesn't
  blank a ticker.
- **Additive-only schema** — `schema.sql` is `CREATE TABLE IF NOT EXISTS` only;
  `init_db()` is idempotent across versions. The SQLite cache is rebuildable from
  source and never treated as the source of truth.
- **A deterministic preflight** — `doctor.py` enforces an off-disk-cache
  invariant (compute/cache local, only Markdown in the cloud-synced vault) and
  runs before every command that touches the DB or vault.
- **Self-contained presentation** — the dashboard is one HTML file with inline
  CSS/JS and hand-rolled SVG charts: no CDN, no build step, opens offline. An
  educational layer (tooltips + learn-mode + a glossary) is enforced by a test
  that fails the build if any metric ships without a definition.
- **Honesty as a habit** — a verify ritual (pre-register the expected probe
  result, then run it; a contradicted prediction is the finding, not something to
  rationalize) is how the cost-fragility prediction and the momentum-redundancy
  finding were caught.

## 6. What I'd do next

New, genuinely orthogonal signals (quality, value, post-earnings drift) rather
than re-tuning the existing five; an LLM that *proposes* candidate signal
formulas which the signal-lab IC-gates before inclusion; and — the real arbiter —
accumulating live forward paper-trading results, since every backtest number
above is in-sample-aware by construction.

---

*Stack: Python · pandas/NumPy · statsmodels · arch · hmmlearn · pykalman ·
SQLite. ~180 tests. No web framework, no hosting. Rendered into Obsidian +
a self-contained HTML dashboard.*
