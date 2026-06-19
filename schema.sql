-- Quant cockpit: Week 1 schema. Daily adjusted prices + ticker registry.

CREATE TABLE IF NOT EXISTS tickers (
    symbol           TEXT PRIMARY KEY,
    name             TEXT,
    last_refreshed   TEXT,          -- ISO-8601 UTC timestamp of last successful fetch
    last_status      TEXT,          -- 'ok' | 'error:<reason>'
    added_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prices (
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,      -- ISO date 'YYYY-MM-DD'
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    adj_close   REAL,
    volume      INTEGER,
    PRIMARY KEY (ticker, date),
    FOREIGN KEY (ticker) REFERENCES tickers(symbol) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON prices(ticker, date DESC);

-- Fundamentals snapshot. One row per (ticker, as_of_date). v1 pulls from yfinance.info.
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker         TEXT NOT NULL,
    as_of          TEXT NOT NULL,     -- ISO date the snapshot was taken
    pe             REAL,              -- trailing P/E
    forward_pe     REAL,
    ps             REAL,              -- price / sales
    pb             REAL,              -- price / book
    ev_ebitda      REAL,
    peg            REAL,
    div_yield      REAL,              -- as decimal (0.015 = 1.5%)
    market_cap     REAL,
    sector         TEXT,
    industry       TEXT,
    PRIMARY KEY (ticker, as_of),
    FOREIGN KEY (ticker) REFERENCES tickers(symbol) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker ON fundamentals(ticker, as_of DESC);

-- User holdings. One row per open position (ticker, lot).
-- Multiple lots for the same ticker collapse into a single row via avg cost basis.
CREATE TABLE IF NOT EXISTS holdings (
    ticker         TEXT PRIMARY KEY,
    shares         REAL NOT NULL,
    cost_basis     REAL NOT NULL,      -- average cost per share
    opened_on      TEXT,               -- ISO date, optional
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (ticker) REFERENCES tickers(symbol) ON DELETE CASCADE
);

-- =========================================================================
-- Phase A additions (Week 16): cache tables for MC, valuation, sector rotation.
-- All additive. Never drop. Writers use timeout=30 + WAL (see utils/db.py).
-- =========================================================================

-- Monte Carlo results cache. MC is cache-only per the current policy —
-- written by tasks (tasks/*), read by Dash callbacks. Keyed by a deterministic
-- hash of (holdings snapshot + sim parameters + stress label).
CREATE TABLE IF NOT EXISTS mc_results (
    scenario_key       TEXT PRIMARY KEY,   -- sha256(holdings_hash|horizon|n_sims|lookback|stress_label)
    run_at             TEXT NOT NULL,       -- ISO-8601 UTC timestamp
    stress_label       TEXT,                -- 'Current' | 'Covid 2020' | 'Rate shock 2022' | 'Q4 2018 bear' | NULL
    horizon_days       INTEGER NOT NULL,
    n_sims             INTEGER NOT NULL,
    lookback_days      INTEGER NOT NULL,
    current_value      REAL NOT NULL,
    var_95             REAL,
    var_99             REAL,
    cvar_95            REAL,
    var_pct_95         REAL,
    cvar_pct_95        REAL,
    median_terminal    REAL,
    best_5pct_gain     REAL,
    portfolio_vol_ann  REAL,
    percentiles_json   TEXT,                -- {"5": [...], "25": [...], "50": [...], "75": [...], "95": [...]} for fan chart
    components_json    TEXT,                -- [{"ticker":..., "component_cvar":..., "risk_multiplier":..., ...}]
    asset_names_json   TEXT,                -- ["AAPL", "MSFT", ...]
    status             TEXT NOT NULL DEFAULT 'ok',   -- 'ok' | 'error:<reason>' | 'insufficient_data'
    ttl_hours          INTEGER NOT NULL DEFAULT 24
);

CREATE INDEX IF NOT EXISTS idx_mc_run_at ON mc_results(run_at DESC);

-- Valuation cache per ticker. Daily snapshots; newer row per (ticker, as_of).
-- Extends what live scoring computes + adds DCF/DDM/EV-EBITDA outputs from fundamental.py.
CREATE TABLE IF NOT EXISTS valuation_cache (
    ticker             TEXT NOT NULL,
    as_of              TEXT NOT NULL,        -- ISO date
    data_tier          INTEGER NOT NULL,     -- 1 | 2 | 3  (see data_fetcher.detect_data_tier)
    absolute_score     REAL,
    peer_score         REAL,
    final_score        REAL,
    bucket             TEXT,                  -- 'attractive' | 'fair' | 'expensive' | 'no_data'
    peer_type          TEXT,                  -- 'sector' | 'watchlist' | 'absolute'
    peer_group_size    INTEGER,
    -- fundamental.py methodology outputs
    dcf_value          REAL,                  -- per-share intrinsic from DCF
    dcf_upside_pct     REAL,                  -- (dcf_value / price - 1)
    ddm_value          REAL,                  -- dividend discount model (dividend payers only)
    ev_ebitda          REAL,
    pe_relative        REAL,                  -- P/E vs sector median ratio
    peg                REAL,
    pb                 REAL,
    confidence         REAL,                  -- 0..1 based on coverage + tier
    notes              TEXT,                  -- JSON: assumptions, warnings
    PRIMARY KEY (ticker, as_of),
    FOREIGN KEY (ticker) REFERENCES tickers(symbol) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_valuation_cache_ticker ON valuation_cache(ticker, as_of DESC);

-- Sector rotation cache. One row per (etf_ticker, as_of) for daily sector performance
-- and rotation signals used by Tab 5.
CREATE TABLE IF NOT EXISTS sector_perf (
    etf_ticker         TEXT NOT NULL,         -- 'XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLI', 'XLU', 'XLB', 'XLRE', 'XLC'
    sector_name        TEXT NOT NULL,         -- 'Technology', 'Financials', ...
    as_of              TEXT NOT NULL,         -- ISO date
    ret_1m             REAL,
    ret_3m             REAL,
    ret_6m             REAL,
    ret_1y             REAL,
    rel_strength_1m    REAL,                  -- etf_return - spy_return (1m)
    rel_strength_3m    REAL,
    rel_strength_6m    REAL,
    rotation_score     REAL,                  -- 0..1 composite of momentum + rel strength
    rotation_signal    TEXT,                  -- 'leading' | 'improving' | 'lagging' | 'weakening'
    PRIMARY KEY (etf_ticker, as_of)
);

CREATE INDEX IF NOT EXISTS idx_sector_perf_as_of ON sector_perf(as_of DESC);

-- =========================================================================
-- Phase H additions: user-editable settings (watchlist, ARS thresholds).
-- Single key/value table — values are JSON-serialized for flexibility.
-- =========================================================================
CREATE TABLE IF NOT EXISTS user_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,                       -- JSON-encoded
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- =========================================================================
-- Phase J additions: regime-aware screener (SCREENER_BUILD_v3).
-- One row per (run, ticker). Cockpit's Screener tab queries the most-recent
-- run_at and groups by sector for the per-sector top-5 tables.
-- Additive only — never drop. Writers use timeout=30 + WAL (utils/db.py).
-- =========================================================================
CREATE TABLE IF NOT EXISTS screener_results (
    run_at                  TEXT NOT NULL,           -- ISO-8601 UTC of the orchestrator run
    ticker                  TEXT NOT NULL,
    sector                  TEXT NOT NULL,           -- one of SECTOR_ETFS keys
    rank                    INTEGER NOT NULL,        -- 1..TOP_N_OUTPUT within sector
    composite_score         REAL NOT NULL,
    regime                  TEXT NOT NULL,           -- 'bull' | 'sideways' | 'bear'
    regime_confidence       REAL NOT NULL,
    passed_veto             INTEGER NOT NULL,        -- 0 | 1
    veto_reason             TEXT,                    -- NULL if passed
    veto_relaxed            INTEGER NOT NULL DEFAULT 0,
    relaxation_passes       INTEGER NOT NULL DEFAULT 0,
    signal_scores_json      TEXT NOT NULL,           -- {"arima":..., "kalman":..., ...}
    signal_contributions_json TEXT NOT NULL,         -- weighted contributions
    top_overall_rank        INTEGER,                 -- 1..TOP_N_OUTPUT or NULL
    PRIMARY KEY (run_at, ticker)
);

CREATE INDEX IF NOT EXISTS idx_screener_results_run_at
    ON screener_results(run_at DESC);
CREATE INDEX IF NOT EXISTS idx_screener_results_sector
    ON screener_results(sector, run_at DESC);

-- Run-level summary; one row per run_at (separate from per-ticker rows).
CREATE TABLE IF NOT EXISTS screener_runs (
    run_at              TEXT PRIMARY KEY,
    regime_label        TEXT NOT NULL,
    regime_confidence   REAL NOT NULL,
    regime_stable       INTEGER NOT NULL,
    total_sectors       INTEGER NOT NULL,
    total_screened      INTEGER NOT NULL,
    total_passed_veto   INTEGER NOT NULL,
    total_skipped       INTEGER NOT NULL,
    total_failed        INTEGER NOT NULL,
    veto_rate_pct       REAL NOT NULL,
    elapsed_seconds     REAL NOT NULL,
    output_path         TEXT,
    payload_json        TEXT NOT NULL                -- full format_results() blob
);

CREATE INDEX IF NOT EXISTS idx_screener_runs_run_at
    ON screener_runs(run_at DESC);
