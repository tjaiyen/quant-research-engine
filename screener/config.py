"""screener/config.py — single source of truth for all screener constants.

Self-validates the WEIGHT_MATRIX at import time so a bad config fails fast
(before any model trains, before any data is fetched).

No magic numbers should appear anywhere else in the screener package — every
constant lives here. If you need a new tunable, add it here and import it.
"""
from __future__ import annotations

# ── Forecast & Model Parameters ──────────────────────────────────────────────
FORECAST_HORIZON_DAYS: int = 20
ARIMA_ORDER: tuple[int, int, int] = (2, 1, 2)   # fixed order when ARIMA_USE_AUTO=False
ARIMA_USE_LOG_PRICES: bool = True               # fit on log(close), not raw prices
ARIMA_USE_AUTO: bool = False                    # True = pmdarima stepwise (slow on 220 stocks)
ADF_PVALUE_THRESHOLD: float = 0.05
SIGNAL_TIMEOUT_SECONDS: int = 30

GARCH_P: int = 1
GARCH_Q: int = 1
GARCH_VOL_SCORE_CAP: float = 0.60               # annualized vol >= this → score 0.0
GARCH_COMPOSITE_MODE: str = "efficiency"        # "efficiency" | "veto_only"

MC_SIMULATIONS: int = 10_000
MC_DRIFT_LOOKBACK_DAYS: int = 63
MC_VOL_LOOKBACK_DAYS: int = 63
MC_RISK_FREE_RATE: float = 0.05                 # annualized
MC_SEED: int = 42
MC_SEED_VARIATIONS: int = 5                     # number of seeds to average
MC_REPORT_UNCERTAINTY: bool = True
MC_LOSS_THRESHOLD: float = 0.90                 # terminal < 0.90 × current = loss

KALMAN_TRANSITION_COV: float = 1e-5
KALMAN_OBSERVATION_COV: float = 1e-2

# ── Universe & Output ────────────────────────────────────────────────────────
STOCKS_PER_SECTOR: int = 20
TOP_N_OUTPUT: int = 5

# ── Minimum History Requirements (rows of daily OHLCV) ───────────────────────
MIN_HISTORY_ARIMA: int = 60
MIN_HISTORY_GARCH: int = 100
MIN_HISTORY_KALMAN: int = 30
MIN_HISTORY_MC: int = 60
MIN_HISTORY_SHARPE: int = 63

# ── HMM Regime Engine ────────────────────────────────────────────────────────
HMM_N_STATES: int = 3
HMM_N_ITER: int = 1000
HMM_LOOKBACK_YEARS: int = 3
HMM_RETRAIN_CADENCE_DAYS: int = 28
HMM_CONVERGENCE_THRESHOLD: float = 1e-4
HMM_MIN_REGIME_SEPARATION: float = 0.10
HMM_LABEL_VALIDATION: bool = True
HMM_BULL_MIN_RETURN: float = 0.0
HMM_BEAR_MAX_RETURN: float = 0.0
HMM_FEATURES: list[str] = [
    "log_return",
    "realized_vol_20d",
    "vix_normalized",
    "breadth_pct",
]
REGIME_HYSTERESIS_DAYS: int = 3                  # M2: days of consistent signal before flip
REGIME_CONFIDENCE_THRESHOLD: float = 0.55        # M2: min confidence for stable label

# ── Regime Labels ────────────────────────────────────────────────────────────
REGIME_LABELS: dict[int, str] = {0: "bull", 1: "sideways", 2: "bear"}

# ── Dynamic Weight Matrix ────────────────────────────────────────────────────
WEIGHT_MATRIX: dict[str, dict[str, float]] = {
    "bull":     {"arima": 0.40, "kalman": 0.20, "garch": 0.10,
                 "monte_carlo": 0.10, "sharpe": 0.20},
    "bear":     {"arima": 0.15, "kalman": 0.15, "garch": 0.35,
                 "monte_carlo": 0.25, "sharpe": 0.10},
    "sideways": {"arima": 0.20, "kalman": 0.30, "garch": 0.20,
                 "monte_carlo": 0.15, "sharpe": 0.15},
}

# ── Regime-Adjusted Veto Thresholds ──────────────────────────────────────────
VETO_THRESHOLDS: dict[str, dict[str, float]] = {
    "bull":     {"garch_vol": 0.045, "mc_loss_prob": 0.30},
    "sideways": {"garch_vol": 0.035, "mc_loss_prob": 0.25},
    "bear":     {"garch_vol": 0.025, "mc_loss_prob": 0.20},
}

# ── Veto Relaxation ──────────────────────────────────────────────────────────
BEAR_REGIME_VETO_RELAXATION: bool = True
VETO_RELAXATION_PASSES: int = 2                  # loosen 20% per pass, max N times
VETO_RELAXATION_FACTOR: float = 0.20             # per-pass loosening fraction

# ── Earnings-Blackout Guard (Upgrade U7) ─────────────────────────────────────
# Veto (and NEVER relax) any candidate within ±N days of its next earnings date.
# Categorical, not threshold-based — excluded from the relaxation loop above.
EARNINGS_BLACKOUT_ENABLED: bool = True
EARNINGS_BLACKOUT_DAYS: int = 5

# ── Delisting / Stale-Ticker Skip (Upgrade U9) ───────────────────────────────
# Skip-scoring a ticker whose last fetch errored, or whose cached data is older
# than this many calendar days (fail-open when status/refresh time is unknown).
SCREENER_SKIP_STALE_ENABLED: bool = True
SCREENER_SKIP_STALE_DAYS: int = 10

# ── News-Sentiment Overlay (Upgrade U11) ─────────────────────────────────────
# Opt-in soft veto on strongly-negative recent news (FinBERT over yfinance news).
# Default OFF — when off, the screener behaves exactly as before. Fail-open:
# unknown/unavailable sentiment never vetoes.
SENTIMENT_VETO_ENABLED: bool = False
SENTIMENT_VETO_THRESHOLD: float = -0.60     # veto if sentiment_score <= this (-1..1)
SENTIMENT_NEWS_LOOKBACK_DAYS: int = 14      # only score headlines from the last N days
SENTIMENT_MAX_ARTICLES: int = 10            # cap headlines scored per ticker

# ── AI Co-pilot (Claude reasoning overlay) ───────────────────────────────────
# Opt-in. When enabled AND an ANTHROPIC_API_KEY is present, Claude reads each
# cycle and writes a first-person "here's my thinking" take (conviction +
# concerns). ADVISORY ONLY — it never places trades; the deterministic quant
# engine + 8 risk guards remain the sole trade path. Default OFF; degrades
# gracefully (no SDK / no key / API error → an informational "off" note).
COPILOT_ENABLED: bool = False
COPILOT_MODEL: str = "claude-opus-4-8"
COPILOT_MAX_TOKENS: int = 2048
COPILOT_EFFORT: str = "low"          # narration task — keep it cheap

# ── Sector ETF Map ───────────────────────────────────────────────────────────
# Keys MUST exactly match the keys used in screener/data/holdings.json (Gate 10).
# Values mirror cockpit's tasks/refresh_sectors.SECTOR_ETFS.
SECTOR_ETFS: dict[str, str] = {
    "Technology":             "XLK",
    "Healthcare":             "XLV",
    "Financials":             "XLF",
    "Energy":                 "XLE",
    "Industrials":            "XLI",
    "Consumer_Discretionary": "XLY",
    "Consumer_Staples":       "XLP",
    "Materials":              "XLB",
    "Utilities":              "XLU",
    "Real_Estate":            "XLRE",
    "Communications":         "XLC",
}

# ── Data Fetching ────────────────────────────────────────────────────────────
YFIN_RETRY_ATTEMPTS: int = 3
YFIN_RETRY_DELAY_SECONDS: int = 5
YFIN_MIN_ROWS_REQUIRED: int = 252
YFIN_INTERSECTOR_DELAY_SEC: int = 2
YFIN_DATA_STALE_DAYS: int = 5                    # M3: warn if last data > N trading days old
VIX_TICKER: str = "^VIX"
VIX_NORMALIZE_DIVISOR: float = 20.0
VIX_FALLBACK_VALUE: float = 20.0
VIX_MAX_NAN_PCT: float = 0.10                    # M1: fallback if >10% NaN
VIX_FFILL_LIMIT: int = 2                         # M1: max consecutive forward-fill days

# ── Feature Cache ────────────────────────────────────────────────────────────
FEATURE_CACHE_TTL_HOURS: int = 12                # A2: in-memory cache window

# ── Paths ────────────────────────────────────────────────────────────────────
MODEL_PATH: str = "screener/models/hmm_model.pkl"
HOLDINGS_PATH: str = "screener/data/holdings.json"
OUTPUT_DIR: str = "screener/output/runs/"
AUDIT_RETAIN_DAYS: int = 30                      # A4: compressed audit files retained N days
LOG_LEVEL: str = "INFO"

# ── Internal Keys ────────────────────────────────────────────────────────────
EXPECTED_SIGNAL_KEYS: set[str] = {"arima", "kalman", "garch", "monte_carlo", "sharpe"}


# ── Self-Validation (runs at import time — fails fast on config error) ──────
def _validate_weight_matrix() -> None:
    for regime, weights in WEIGHT_MATRIX.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-9, (
            f"WEIGHT_MATRIX['{regime}'] sums to {total:.10f}, expected 1.0. "
            "Fix config.py before running."
        )
        assert set(weights.keys()) == EXPECTED_SIGNAL_KEYS, (
            f"WEIGHT_MATRIX['{regime}'] has wrong signal keys: "
            f"{set(weights.keys())}, expected {EXPECTED_SIGNAL_KEYS}"
        )


def _validate_veto_thresholds() -> None:
    for regime in WEIGHT_MATRIX:
        assert regime in VETO_THRESHOLDS, (
            f"VETO_THRESHOLDS missing entry for regime '{regime}'"
        )
        for key in ("garch_vol", "mc_loss_prob"):
            assert key in VETO_THRESHOLDS[regime], (
                f"VETO_THRESHOLDS['{regime}'] missing '{key}'"
            )


def _validate_regime_labels() -> None:
    labels = set(REGIME_LABELS.values())
    expected = set(WEIGHT_MATRIX.keys())
    assert labels == expected, (
        f"REGIME_LABELS values {labels} must match WEIGHT_MATRIX keys {expected}"
    )


_validate_weight_matrix()
_validate_veto_thresholds()
_validate_regime_labels()
