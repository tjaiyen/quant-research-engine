# INSIGHTS — Upgrade candidates mined from external repos

Mined 2026-06-19 from 13 external repos to find concrete upgrades for quant-tracker,
in the style of ruflo's B-series insight mining. Each insight: **Source → Maps-to →
Recommendation** (ADOPT / ADAPT / DECLINE · effort S/M/L · one-line why). Nothing here
is implemented yet — this is the menu, sequenced at the end.

**Repos surveyed.** QuantConnect [Lean](https://github.com/QuantConnect/Lean) ·
[lean-cli](https://github.com/QuantConnect/lean-cli) · [Research](https://github.com/QuantConnect/Research)
(the professional gold standard); [Ooples YahooFinanceAPI](https://github.com/Ooples-Finance-LLC/OoplesFinance.YahooFinanceAPI)
(Yahoo endpoint-coverage map); [transformers](https://github.com/huggingface/transformers)
(FinBERT) · [tensorflow](https://github.com/tensorflow/tensorflow); plus seven smaller
analysis/ML repos ([sardarosama](https://github.com/sardarosama/Stock-Market-Trend-Prediction-Using-Sentiment-Analysis),
[vishal815](https://github.com/vishal815/-Stock-market-Prediction-with-Machine-Learning-Django),
[Amey-Thakur k-means](https://github.com/Amey-Thakur/OPTIMIZING-STOCK-TRADING-STRATEGY-WITH-K-MEANS-CLUSTERING),
[pystocklib](https://github.com/mohabmes/pystocklib), [storieswithsiva](https://github.com/storieswithsiva/Stock-Market-Analysis),
[arshpreet hedge-fund](https://github.com/arshpreet/Hedge-Fund-stock-market-analysis)).

**Headline.** The real prizes are LEAN's *architectural abstractions* (a typed signal
object, separated risk layer, deterministic backtester) and *data-layer resilience*
(multi-provider fallback, earnings-awareness, delisting detection) — both lightweight and
directly de-tangling. The heavy ML (LSTM/TensorFlow, NN ensembles, price-level regression)
is declined: it overfits quant-tracker's small free-data regime and is dominated by a naïve
baseline. The one ML dependency worth its weight is **FinBERT news sentiment**, because it
fills a *stated* Tier-3 gap rather than duplicating the existing ARIMA/GARCH/Kalman stack.

---

## Theme A — Architecture (from LEAN): structural de-tangling

### U1 — Typed `Signal`/Insight object as the screener→trader contract ✅ IMPLEMENTED (surgical scope)
- **Source:** LEAN `Insight` (direction, magnitude, confidence, weight, **period/expiry**).
- **Maps to:** screener compositing → paper-trader hand-off; `signal_decay_monitor`.
- **Rec: ADOPT (M).** Formalize the 5-signal composite into a `Signal(symbol, direction, score, confidence, horizon/expiry, regime_at_emit)` dataclass. The **expiry field replaces ad-hoc decay monitoring** and gives a clean seam between `screener/` and `auto_trader/`. Highest clarity-per-effort borrow.
- **Status:** built at the **ingestion boundary only** (the low-risk slice TJ chose over the full 15-file refactor): `screener/signal.py:Signal` (`from_row` validates + type-coerces, `canonical()` merges back) is wired into `auto_trader/compat/screener_compat.normalize_screener_cache`, so a malformed/old-schema cache (e.g. a string `composite_score`) is coerced+logged at the seam instead of silently flowing into the trader's math. Downstream stays dict-based. **Not done:** full pipeline typing (signal_filter→sizer→target_builder→delta_engine) — deliberately skipped as low-marginal-value churn on a working, fully-tested system; no expiry field added (would be unused — `signal_decay_monitor` already handles decay by re-scoring).

### U2 — Split sizing from risk: a `PortfolioTarget` hand-off + separate risk layer
- **Source:** LEAN PortfolioTarget + the Risk-Management stage (risk *post-processes* targets).
- **Maps to:** `auto_trader/allocator/*` (sizing) + the 8-guard veto.
- **Rec: ADAPT (M).** Today sizing (score/vol) and the 8 guards are entangled. Restructure: compositor emits desired weights → a risk layer scales/cancels them → execution diffs against holdings. Keep the *content* of the 8 guards; adopt the *architecture* (post-process, don't veto inline).

### U3 — Composite guard chain (each guard a `(targets, portfolio) -> targets` unit)
- **Source:** LEAN `CompositeRiskManagementModel`.
- **Maps to:** the 8-guard veto pipeline (`auto_trader/risk/*`).
- **Rec: ADAPT (S).** Make each guard a small object with one method, run them as a list. Turns a monolithic veto into 8 independently testable, reorderable units. Pairs with U2.

### U4 — Deterministic event-replay backtester reusing the trader's decision functions ✅ IMPLEMENTED (portfolio-sim scope)
- **Source:** LEAN's "same code path across backtest/paper/live," deterministic replay.
- **Maps to:** `auto_trader/` + the SQLite ledger; the biggest genuine gap (today = forward paper only).
- **Rec: ADAPT (L).** Feed the *same* compositor + sizer + guards bar-by-bar from the SQLite price cache; swap only the clock + fill source. Lets you validate a strategy change before it touches the live paper ledger. High effort, highest discipline payoff.
- **Status:** built as a **strategy portfolio simulation** (not a bit-exact broker/guard replay): `screener/backtest/portfolio_backtest.py:run_portfolio_backtest` reuses the walk-forward machinery (`_regime_at`/`_slice_history_to`/`_ph_for` + `score_stock`) — quarterly rebalance, per-sector top-N equal-weight, mark-to-market vs SPY (equity curve + total/CAGR/maxDD/Sharpe). `render/notes.strategy_backtest_note`, `track sim`. Sampled (~10–15 min), on-demand. Pure metric helpers unit-tested; sim loop tested via injected scorer.

### U5 — Regime *selects which signals are active*, not just reweights them
- **Source:** LEAN Universe Selection + alpha-model swapping.
- **Maps to:** HMM regime → `WEIGHT_MATRIX` compositing.
- **Rec: ADAPT (M).** Beyond regime-weighting, let bull/sideways/bear *disable* unsuited signals (e.g., mean-reversion in a strong-trend regime). Uses existing HMM output; cleaner than weight-only.

### U6 — Explicit `MaximumSectorExposure` cap as a construction model ✅ ALREADY SATISFIED
- **Source:** LEAN SectorWeighting / MaximumSectorExposure.
- **Maps to:** the top-5-per-sector ranking across 11 sectors.
- **Rec: ADOPT (S).** Add a hard cap so no sector dominates the book. Natural fit for the 11-sector universe.
- **Status:** already enforced — `auto_trader/config.py:MAX_SECTOR_PCT=0.20` → `auto_trader/risk/exposure_guard.py:_guard_5_sector_exposure` drops over-budget buys in the monthly cycle (`run_all_guards`), covered by `test_risk_guards.py`. No work needed.

---

## Theme B — Data-layer resilience (fix the single fragile yfinance adapter)

### U7 — Earnings-date-aware veto guard
- **Source:** Ooples `calendarEvents`/earnings endpoints (reachable via yfinance `.get_earnings_dates()`).
- **Maps to:** the 8-guard veto + a new field in the SQLite `fundamentals` table.
- **Rec: ADOPT (S).** Veto/down-weight any entry within N days of earnings to dodge event-gap risk. Highest value-per-effort in this theme; one cached field.

### U8 — Multi-provider fallback (Stooq) behind `data_fetcher`
- **Source:** Ooples (shows how fragile single-source Yahoo is) + the yfinance→Stooq fallback pattern.
- **Maps to:** `data_fetcher` tiering + a new `data_providers/stooq_provider.py` sibling to the frozen yfinance adapter.
- **Rec: ADAPT (M).** Add **Stooq** (no key, decades of EOD, via `pandas-datareader`) as automatic fallback when yfinance returns empty/raises — a provider-fallback *within* Tier-3. Biggest resilience win; keeps the confidence tiers intact.

### U9 — Delisting / stale-data detection in the adapter
- **Source:** Ooples coverage exposes the failure modes; this is custom logic no library ships.
- **Maps to:** the yfinance adapter + `data_fetcher` confidence scoring (extends the existing Phase-L `audit_universe`).
- **Rec: ADOPT (S).** Empty/short price frames, NaN-only fundamentals, or last-trade older than the cache horizon → mark `delisted/stale`, drop confidence tier, exclude from top-5. Prevents trading a dead ticker; pairs with U8.

### U10 — Quarterly fundamentals **history** for richer valuation
- **Source:** Ooples — income/balance/cash-flow history, annual **and quarterly**.
- **Maps to:** the SQLite `fundamentals` table + the DCF/DDM module (`fundamental.py`).
- **Rec: ADAPT (M).** Cache time-indexed quarterly rows (`.quarterly_financials`) so DCF/DDM use TTM + growth trajectories instead of a single snapshot. No new deps.

---

## Theme C — New signals (opt-in, Tier-gated — fill the Tier-3 gaps)

### U11 — FinBERT news-sentiment ✅ IMPLEMENTED (opt-in veto, not a 6th signal)
- **Source:** transformers / `ProsusAI/finbert` (BERT-base, CPU-OK, trained on financial-news register).
- **Maps to:** a NEW 6th per-stock signal feeding the regime-weighted composite + ARS.
- **Rec: ADAPT (M).** The one ML dependency worth its weight — fills the explicit no-sentiment gap. **Only valid with a point-in-time, timestamped news feed** (else instant lookahead). Cache scores in SQLite keyed by (ticker, date) like everything else.
- **Status:** built as an **opt-in soft veto + overlay**, NOT a 6th composite signal (the validated `WEIGHT_MATRIX` stays untouched). `screener/sentiment/scorer.py` (yfinance `.news` → FinBERT, point-in-time for forward use, graceful UNAVAILABLE if deps/model absent), additive `news_sentiment` table, `track sentiment`, `Sentiment.md`, and a `SENTIMENT_VETO_ENABLED` (default **OFF**) categorical veto wired into `composite_scorer`/`industry_ranker` (mirrors the U7 earnings guard). Heavy deps are **optional** (`requirements-sentiment.txt`); without them sentiment is UNAVAILABLE and the veto is a no-op.

### U12 — Ship sentiment as a *veto/guard* first, weighted signal later
- **Source:** FinBERT + the 8-guard pattern.
- **Maps to:** add a 9th guard (strongly-negative-news veto) before trusting sentiment as a weighted input.
- **Rec: ADAPT (S).** De-risks U11: a binary safety gate is cheaper and safer than full compositing on day one.

### U13 — Analyst recommendation-**trend** signal (time series, not snapshot)
- **Source:** Ooples `recommendationTrend` / upgrade-downgrade history (yfinance `.recommendations`, `.upgrades_downgrades`).
- **Maps to:** a low-weight confirming signal or veto-softener in the signal layer.
- **Rec: ADAPT (M, default OFF).** Momentum-of-consensus. Keep Tier-gated (consensus is exactly what Tier-3 gates OFF) — opt-in only.

### U14 — Insider net-purchase as a tie-breaker
- **Source:** Ooples insider transactions / net share-purchase (yfinance `.insider_transactions`).
- **Maps to:** a tie-breaker within a sector's top-5, or a soft guard.
- **Rec: ADAPT (M, default OFF).** Cheap conviction signal but noisy — tie-breaker only, Tier-gated like U13.

### U15 — K-means universe clustering on (vol, return) for diversification ✅ IMPLEMENTED
- **Source:** Amey-Thakur (k-means on annualized vol + return; Silhouette/Elbow for k).
- **Maps to:** a universe-layer step — cluster the 220 stocks into risk/return cohorts; cap picks per cohort or add an "over-concentrated cohort" veto.
- **Rec: ADOPT (S).** scikit-learn already present (hmmlearn dep chain); **leakage-light** (clusters on realized stats, not future labels); serves the diversification goal directly. Use Silhouette to pick k (free rigor).
- **Status:** built — `screener/analysis/clustering.py` (pure `cluster_features` + `compute_clusters` w/ silhouette k-selection), `render/notes.py:clusters_note`, `track clusters` → `90 Tracker/Clusters.md`. On-demand (not in the daily report). A *descriptive* diversification lens, not a selection veto (that's a later option).

---

## Theme D — Workflow & ergonomics

### U16 — QuantBook-style research notebooks sharing the screener's signal primitives
- **Source:** QuantConnect/Research (research and production share one API surface).
- **Maps to:** `screener/signals/*` + the history loader.
- **Rec: ADOPT (S).** Expose the 5 signal functions + price loader so they're callable in a Jupyter notebook identically to production — prototype a 6th signal / tune GARCH against the *same* code. Obsidian-native, zero new infra.

### U17 — Timestamped run-artifact dirs `runs/<timestamp>/`
- **Source:** lean-cli `backtests/<timestamp>/`, `--output`.
- **Maps to:** render layer + ledger (extends the existing `screener/output/runs/`).
- **Rec: ADOPT (S).** Each run writes a timestamped folder (ranks, targets, fills, equity snapshot) → reproducible, diffable history alongside the Obsidian render.

### U18 — Cascading config + `track config get/set/list`
- **Source:** lean-cli flag → config-file → default resolution; config groups.
- **Maps to:** `cli/track.py`.
- **Rec: ADAPT (S).** Add a `config` subcommand and four-level option resolution. Cheap ergonomics win for a CLI-driven, monthly-cadence tool.

### U19 — Codify walk-forward / no-lookahead discipline as a rule
- **Source:** the LSTM/sentiment literature anti-patterns (predict *return* not level; shift predictions; align timestamps).
- **Maps to:** cross-cuts the screener + any new signal; a `.claude/rules/` or docs note.
- **Rec: ADOPT (S).** Process guard matching quant-tracker's allergy to lookahead/overfit — required before U11/U13 land.

---

## Theme F — Presentation (mined 2026-06-19 from obsidian-advanced-slides)

### U20 — Auto-generated investment-review slide deck ✅ IMPLEMENTED
- **Source:** [MSzturc/obsidian-advanced-slides](https://github.com/MSzturc/obsidian-advanced-slides) — Markdown → reveal.js decks (`---` slide breaks, frontmatter theme/transition, ```chart blocks, tables/fragments; exports standalone HTML/PDF).
- **Maps to:** the `render/` layer — emit one more note, `90 Tracker/Review.md`, a weekly-review deck from the same data dicts (regime, top picks, scorecard incl. coverage, equity-curve chart).
- **Rec: ADOPT (S–M).** Low-effort pure note-builder; high value as a **finance+AI job-hunt portfolio artifact** (exportable HTML deck: pipeline → model → picks → measured performance). **Implemented:** `render/slides.py:review_deck`, `track review`, and `build_all` writes `Review.md`.
- **Caveats baked in:** the original plugin is **discontinued → target the maintained fork "Slides Extended"**; advanced-slides conflicts with **Dataview**, so the deck is **fully static** (no queries — naturally how `render/` works); it degrades to readable Markdown without the plugin.

---

## Theme G — Obsidian ecosystem (mined 2026-06-19 from 4 plugins)

**Honest verdict: a mostly-decline round.** Three of the four are human-facing UX
plugins an auto-generation pipeline can't drive; Dataview (already used well) mostly
*validates keeping the current static design*. One small ADOPT, deferred.

### U21 — Dataview inline fields + FLATTEN for trade-level history — ADOPT (S), DEFERRED
- **Source:** [blacksmithgu/obsidian-dataview](https://github.com/blacksmithgu/obsidian-dataview) — inline fields (`key:: value`, incl. in list items) + `FLATTEN file.lists`.
- **Maps to:** `render/notes.py:position_note` — emit a `## Trade Log` with per-fill inline fields (`[entry_date:: …] [entry_price:: …] [shares:: …]`) from `auto_trader` `trade_history`, so a Dataview query shows transaction-level history inside one position note (no note-per-fill).
- **Rec: ADOPT (S) — but DEFER.** Genuinely additive and renderer-emittable. Low value *now* (the monthly `paper cycle` hasn't produced fills yet); revisit once positions exist.

### U22 — Dataview GROUP BY live sector aggregation — ADAPT (M), keep static
- **Source:** Dataview `GROUP BY sector` + `sum(rows.market_value)` field-swizzling.
- **Maps to:** could replace a Python-computed sector roll-up with a live view.
- **Rec: ADAPT (M) — not recommended.** quant-tracker's **static, Python-computed, deterministic** output is *better* for an auditable finance tool than live, vault-index-dependent aggregation. Recorded as possible; **keep static**.

### U23 — DataviewJS (computed columns / dv.view() / in-JS charting) — DECLINE
- **Source:** Dataview `dataviewjs` + the `dv` API.
- **Rec: DECLINE.** Adds vault-side JS, bundle weight, and split logic. The existing Python-computed tables + the ```chart equity block (U20 / `render/markdown.py:equity_chart`) already cover this with less complexity and more auditability.

### U24 — Advanced Tables / Iconic / Vault Statistics — DECLINE (all)
- **[advanced-tables](https://github.com/tgrosinger/advanced-tables-obsidian):** an interactive human table *editor* — irrelevant to read-only auto-generated tables.
- **[iconic](https://github.com/gfxholo/iconic):** icons are assigned via **manual UI rules**, **not** drivable from note frontmatter — the renderer can't automate them (cosmetic, one-time manual user setup at most).
- **[vault-statistics](https://github.com/bkyle/obsidian-vault-statistics-plugin):** generic vault word/file counts in the status bar — zero relevance to trading data.

> **Dataview health (for the ledger):** MIT, actively maintained (v0.5.68, Apr 2025).
> The advanced-slides × Dataview 5× slowdown is **already mitigated** — quant-tracker
> keeps `Review.md` (U20) fully static.

---

## Theme E — Explicit DECLINES (with why)

- **LSTM / TensorFlow price prediction** (tensorflow) — DECLINE (L). Overfits small free-data, seed-unstable, dominated by a naïve "tomorrow≈today" baseline, lookahead-prone. ARIMA/GARCH/Kalman already cover the linear-Gaussian job.
- **Multiple-linear-regression price-level forecast** (vishal815) — DECLINE (S). Predicts price *level*; strictly worse than the existing ARIMA.
- **Twitter / social retail sentiment** (sardarosama) — DECLINE (S). Noisy, manipulable (pump-and-dump); prefer curated news (U11).
- **MLP + boosting NN ensemble** (arshpreet) — DECLINE (M). Opaque; cuts against the dependency-light, explainable-signal posture.
- **EMD preprocessing** (pystocklib) — DECLINE (M). Unstable at series edges, extra `PyEMD` dep; Kalman/HMM already denoise.
- **Mean-variance / risk-parity optimizer sizing** (LEAN) — DECLINE (L). Needs a stable covariance estimate free yfinance won't reliably support; overkill for a $1k/mo single-investor paper book. VaR/CVaR already gives the risk read. Where LEAN is genuinely overkill.
- **ESG scores** (Ooples) — DECLINE as a signal (optional display-only ADAPT-S in the Obsidian card if wanted). Not a paper-trading input.

---

## Recommended sequencing

1. **Resilience core (do first — all S/M, fixes the fragile-adapter problem):** U7 earnings-aware veto · U9 delisting/stale detection · U8 Stooq fallback.
2. **Architecture de-tangle (compounding clarity):** U1 Signal object → U3 composite guards → U2 sizing/risk split. (U1 first; it's the contract everything else hangs on.)
3. **Cheap wins, anytime:** U6 sector cap · U15 k-means diversification · U16 research notebooks · U17 run dirs · U19 lookahead rule.
4. **Valuation depth:** U10 quarterly fundamentals history.
5. **The sentiment track (gated on U19 + a news feed):** U12 sentiment-as-veto → U11 FinBERT 6th signal; U13/U14 as opt-in Tier-gated extras.
6. **Largest, last:** U4 deterministic backtester · U5 regime-selects-signals · U18 config subcommand.

## Accepted limitations of this research
- The five small repos (sardarosama, vishal815, Amey-Thakur, pystocklib, storieswithsiva, arshpreet) were assessed at README + metadata level, not full source audits; their 6–18 commit histories confirm proof-of-concept maturity, so a deeper audit would not change the DECLINE calls.
- Ooples is C#; only its **data-coverage map** (Yahoo endpoints) was mined — every cited endpoint is reachable from Python `yfinance` / raw `query2`, so the adoptable ideas carry no .NET dependency.
- Options/IV stay out of scope — no surveyed repo offers a free options source, consistent with quant-tracker keeping IV gated OFF.
