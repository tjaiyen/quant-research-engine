"""Central glossary — terms used across the cockpit.

Each entry has four fields:
  short    — 1-line precise definition (used in hover tooltips)
  full     — conversational expanded explanation (used in 'Explain this' panels)
  matters  — the decision question this term answers
  watch    — common pitfalls / when the metric lies

Tone is intentionally mixed: the `short` is precise/textbook so users
learn the formal language; the `full` is conversational so beginners can
follow without prior finance training.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    short: str
    full: str
    matters: str
    watch: str


# Public dict keyed by lowercase short tag.
GLOSSARY: dict[str, Term] = {

    # ---- Valuation -----------------------------------------------------------
    "dcf": Term(
        short="Discounted Cash Flow — present value of projected future free cash flows discounted at WACC.",
        full=(
            "DCF asks: 'if this company keeps making cash, what's all that future money worth "
            "in today's dollars?' We project free cash flow for ~5 years, then assume it grows "
            "at a small terminal rate forever, and discount everything back at a rate (WACC) "
            "that reflects how risky the business is. If the result per share is higher than "
            "today's price, the stock might be cheap. If it's lower, it might be expensive."
        ),
        matters="What's a fair price for this company based on its future cash?",
        watch=(
            "DCF is hyper-sensitive to assumptions. A 1% change in WACC or growth can swing "
            "intrinsic value by 30%+. Treat the number as a range, not a target. The cockpit "
            "shows a Bear/Base/Bull triplet for exactly this reason."
        ),
    ),
    "ddm": Term(
        short="Dividend Discount Model — present value of future dividends, assuming a constant growth rate.",
        full=(
            "DDM is DCF for income investors: instead of cash flows, you discount the dividend "
            "stream. Only meaningful for companies that actually pay dividends. The classic "
            "Gordon formula assumes dividends grow forever at a constant rate; if that rate "
            "ever exceeds the discount rate, the math blows up."
        ),
        matters="If I'm buying this for the dividend, what's the dividend stream worth today?",
        watch=(
            "Useless for non-dividend payers (most tech). Very sensitive to the growth-rate "
            "assumption. Doesn't capture share buybacks, which are economically equivalent "
            "to dividends for taxable shareholders."
        ),
    ),
    "pe": Term(
        short="Price-to-Earnings — share price divided by earnings per share.",
        full=(
            "P/E is the market's answer to: 'how many years of current profits would it take "
            "to pay back the price?' A P/E of 20 means you're paying $20 for every $1 of "
            "current annual earnings. Lower can mean cheaper — or that the market expects "
            "earnings to fall. Always compare P/E to the company's own history and to its sector."
        ),
        matters="Am I paying a lot or a little for each dollar of profit?",
        watch=(
            "Trailing P/E uses last 12 months of earnings (factual). Forward P/E uses analyst "
            "estimates (often optimistic). Negative earnings make P/E undefined. Sectors have "
            "wildly different normal ranges (utilities 15-20, tech 25-40)."
        ),
    ),
    "forward_pe": Term(
        short="Forward P/E — price divided by projected next-12-months earnings.",
        full=(
            "Same idea as P/E, but using analyst estimates of next year's earnings instead "
            "of last year's actual earnings. More forward-looking but less reliable — "
            "estimates miss often, especially in volatile sectors. At Tier 3 (yfinance) "
            "the cockpit shows forward P/E when available but flags the data confidence."
        ),
        matters="If analysts are right, how am I being priced relative to next year?",
        watch="Analyst estimates are systematically optimistic in good markets and slow to revise down in bad ones.",
    ),
    "peg": Term(
        short="P/E divided by earnings growth rate — a 'growth-adjusted' valuation multiple.",
        full=(
            "PEG normalizes P/E by the earnings growth rate. The classic Lynch rule: "
            "PEG of 1.0 is fair, below 1.0 is cheap-for-the-growth, above 2.0 is expensive. "
            "It tries to reconcile high-growth (high P/E) names with value names on a single "
            "scale. NVDA might look insane on P/E but reasonable on PEG if growth justifies it."
        ),
        matters="Is the high P/E justified by how fast this company is growing?",
        watch=(
            "PEG breaks for non-growth or shrinking companies (negative or zero growth = "
            "meaningless ratio). Growth rate often comes from analyst estimates, which are "
            "noisy. Forward PEG (uses forward earnings) is gated off at Tier 3."
        ),
    ),
    "ps": Term(
        short="Price-to-Sales — market cap divided by trailing 12-month revenue.",
        full=(
            "P/S is P/E's older sibling: it asks how much you're paying per dollar of "
            "revenue (not profit). Useful when earnings are negative or volatile (early-stage "
            "tech, cyclical industries). A bank trading at P/S 5 is alarming; a software "
            "company at P/S 20 is normal-ish. Always compare within sector."
        ),
        matters="What am I paying per dollar of sales? Useful when profits are unstable.",
        watch="Says nothing about whether revenue translates to profit. Two companies with the same P/S can have wildly different margins.",
    ),
    "pb": Term(
        short="Price-to-Book — market cap divided by shareholders' equity (book value).",
        full=(
            "P/B compares the stock price to the accounting value of the company's net assets. "
            "Below 1.0 means the market values the company at less than the sum of its assets "
            "minus liabilities — sometimes a value signal, sometimes a warning that the assets "
            "are worth less than the books say. Critical for banks; less useful for asset-light "
            "businesses like software."
        ),
        matters="Am I paying more or less than the company's net assets are worth on paper?",
        watch="Book value is meaningless for IP-heavy businesses (Apple, Google) and unreliable when assets are stale (legacy industrials).",
    ),
    "ev_ebitda": Term(
        short="Enterprise Value / EBITDA — total business value relative to operating earnings before interest, taxes, depreciation, amortization.",
        full=(
            "EV/EBITDA strips out capital structure (debt vs equity) and accounting choices "
            "(depreciation methods) so you can compare companies across financing setups. "
            "Lower is generally cheaper. Used heavily in M&A. Typical bands: under 8 = cheap, "
            "over 18 = expensive, but sectors vary widely."
        ),
        matters="If someone bought the whole business — debt and equity — how many years of cash earnings to pay back?",
        watch="EBITDA isn't real cash flow (ignores capex, working capital, taxes). Charlie Munger called it 'bullshit earnings' for a reason.",
    ),

    # ---- Risk / Quant --------------------------------------------------------
    "var": Term(
        short="Value at Risk — the loss that won't be exceeded over a horizon at a given confidence level.",
        full=(
            "VaR(95%, 1 month) of $2,000 means: 'in a normal month, I'd expect to lose less "
            "than $2,000. Only 1 month in 20 (the worst 5%) should exceed that.' VaR is a "
            "rough floor on bad days, not a worst case. The cockpit estimates it via Monte "
            "Carlo simulation of 5,000 random futures."
        ),
        matters="In a routine bad month, how much could I plausibly lose?",
        watch="VaR ignores the shape of the tail beyond the threshold. A '5% chance of losing $2K' could hide a 1% chance of losing $50K. Use CVaR for the full picture.",
    ),
    "cvar": Term(
        short="Conditional VaR (Expected Shortfall) — average loss in the worst α% of outcomes.",
        full=(
            "Where VaR draws a line at the worst 5% of outcomes, CVaR averages everything "
            "beyond that line. CVaR(95%) of $3,000 means: 'when things do go badly enough to "
            "exceed VaR, the average loss is $3,000.' It captures tail severity, not just "
            "frequency. Always larger than VaR."
        ),
        matters="When the bad day hits, how bad is it on average?",
        watch="Same Gaussian assumptions as VaR. Real-world tails are fatter — both VaR and CVaR underestimate truly catastrophic events.",
    ),
    "monte_carlo": Term(
        short="Monte Carlo simulation — projecting many random futures from estimated drift + covariance, then aggregating.",
        full=(
            "We take the historical mean return (drift) and covariance matrix (how assets "
            "move together) of your portfolio, then simulate 5,000 different 21-day futures "
            "by sampling correlated random shocks. Each simulation gives a final portfolio "
            "value; the distribution of those 5,000 outcomes lets us read off VaR, CVaR, and "
            "fan-chart percentile bands."
        ),
        matters="What range of outcomes is plausible for my portfolio over the next month?",
        watch="Garbage in, garbage out: if the lookback period was atypical (only bull market history) the simulation underestimates real-world risk. Stress regimes (Covid 2020, 2022) help compensate.",
    ),
    "beta": Term(
        short="The slope of a regression of an asset's returns on the benchmark's returns — sensitivity to market moves.",
        full=(
            "Beta of 1.0 means a stock moves in lockstep with the market. Beta 1.5 means it "
            "amplifies market moves by 50% (rises faster, falls faster). Beta 0.7 dampens. "
            "Negative beta is rare (gold sometimes). Beta is the workhorse risk decomposition: "
            "every position's return splits into a beta-driven 'market' component and an "
            "alpha-driven 'idiosyncratic' component."
        ),
        matters="When the market moves 1%, how much does this position move?",
        watch="Beta is unstable across regimes. A stock's beta in calm markets is often very different from its beta in a crash.",
    ),
    "alpha": Term(
        short="The intercept of an asset's regression on the benchmark — return after adjusting for beta.",
        full=(
            "Alpha is the part of a stock's return that ISN'T explained by market exposure. "
            "Positive alpha means the stock did better than its beta-adjusted expectation; "
            "negative alpha means it underperformed even after accounting for risk taken. "
            "Annualized alpha of +5% means the stock added 5% per year over what its beta "
            "alone would predict."
        ),
        matters="Did this position beat the market on a risk-adjusted basis?",
        watch="Alpha is noisy on short windows. A year of positive alpha can disappear in one bad quarter. Persistent alpha is rare and contested.",
    ),
    "sharpe": Term(
        short="Mean excess return divided by return standard deviation, then annualized.",
        full=(
            "Sharpe is the textbook 'return per unit of risk' metric. Higher = more efficient. "
            "A Sharpe of 1.0 is decent, 2.0 is excellent, above 3.0 in equities is suspicious. "
            "It penalizes both ups and downs equally (counts upside volatility as 'risk' which "
            "many find unintuitive). Sortino is the version that only penalizes downside volatility."
        ),
        matters="How much return did I get for each unit of bumpiness I sat through?",
        watch="Assumes returns are normally distributed (they aren't). High-Sharpe strategies often blow up — the Sharpe captures normal-time efficiency but misses tail risk.",
    ),
    "tracking_error": Term(
        short="Annualized standard deviation of the difference between portfolio and benchmark returns.",
        full=(
            "Tracking error tells you how far off-benchmark your returns swing. Index ETFs "
            "have tracking error near zero. Concentrated portfolios run 5-15%. A high TE "
            "isn't bad per se — it just tells you that you're making bets the market isn't."
        ),
        matters="How much do my returns wander away from the benchmark month-to-month?",
        watch="High TE without positive alpha is the worst combo: you're taking benchmark-divergent risk without earning extra return.",
    ),
    "info_ratio": Term(
        short="Active return divided by tracking error — risk-adjusted measure of how much your bets actually paid.",
        full=(
            "Information Ratio (IR) is Sharpe for active management: instead of total return "
            "over total volatility, it's excess return over tracking error. Above 0.5 is good, "
            "above 1.0 is rare and excellent. Below 0 means you're paying more in active risk "
            "than you're collecting in active return."
        ),
        matters="Are my benchmark-divergent bets earning their keep?",
        watch="IR over short windows is statistical noise. Need at least a few years of data to draw inference.",
    ),
    "drawdown": Term(
        short="Maximum peak-to-trough decline over a window.",
        full=(
            "Drawdown is the worst loss someone holding from a high water mark would have "
            "endured. A -30% 1-year drawdown means at some point during the year, the position "
            "was 30% below its prior peak. It's psychologically the most important risk number "
            "for retail investors — it correlates with capitulation."
        ),
        matters="If I'd been holding through the worst of this period, how deep did the hole get?",
        watch="Past drawdown ≠ future drawdown. The 2020 drawdown was much larger than what realized vol would have predicted.",
    ),
    "vol": Term(
        short="Annualized standard deviation of returns — a measure of how much prices wiggle.",
        full=(
            "Volatility (a.k.a. realized vol) measures how much returns scatter around their "
            "mean. Higher vol = bigger ups and downs. SPY's vol is roughly 13-18% annualized "
            "in normal times, 30%+ in crises. Implied vol (from options markets) is the "
            "market's forward-looking guess; we use realized vol because we're at Tier 3 "
            "(no options chain access)."
        ),
        matters="How bumpy is the ride likely to be?",
        watch="Treats up-days and down-days as equally bad. Sometimes high vol is upside skew (rare but real).",
    ),
    "hhi": Term(
        short="Herfindahl-Hirschman Index — sum of squared weights, measuring concentration.",
        full=(
            "HHI for a portfolio sums the squared weight of each position. A 1-stock portfolio "
            "has HHI = 1.0; an equally-weighted 10-stock portfolio has HHI = 0.10. Antitrust "
            "regulators use HHI to flag market concentration; we use it for portfolio "
            "concentration. Above 0.30 is concentrated; below 0.15 is well-diversified."
        ),
        matters="Am I betting too much of the portfolio on one or two names?",
        watch="HHI doesn't capture sector concentration. A 10-stock portfolio of all-tech has low HHI but high sector risk.",
    ),
    "component_cvar": Term(
        short="Per-position contribution to portfolio CVaR via tail-conditional expectation.",
        full=(
            "Component CVaR breaks down the question 'when bad things happen, who's "
            "responsible?' Each position gets a share of total tail loss based on its "
            "behavior in the worst 5% of simulated outcomes. The shares sum exactly to "
            "portfolio CVaR (Euler's theorem). The 'risk multiplier' = contribution % / "
            "weight %; values above 1.0 mean a position pulls more risk than its size "
            "would suggest."
        ),
        matters="When my portfolio bleeds, who's bleeding the worst?",
        watch="Same Gaussian assumptions as CVaR. Risk attribution can flip in real crises.",
    ),

    # ---- Technical -----------------------------------------------------------
    "sma": Term(
        short="Simple Moving Average — arithmetic mean of the last N closing prices.",
        full=(
            "SMA smooths price into a trend line. SMA50 vs SMA200 is the classic "
            "trend-following signal: when the 50-day crosses above the 200-day "
            "('golden cross'), it's bullish; the reverse ('death cross') is bearish. "
            "Lagging by construction — an SMA50 reflects the average of the last 50 days, "
            "not what's happening today."
        ),
        matters="Is the trend up, down, or sideways?",
        watch="Whipsaws in sideways markets — multiple false crossovers eat away at returns.",
    ),
    "rsi": Term(
        short="Relative Strength Index (Wilder, 14-period) — momentum oscillator on a 0-100 scale.",
        full=(
            "RSI compares the magnitude of recent gains vs recent losses. Above 70 is "
            "traditionally 'overbought' (mean reversion likely); below 30 is 'oversold'. "
            "But RSI can stay extreme for a long time in trending markets — overbought "
            "in 2020-21 tech was a feature, not a warning. Use as one input, not a rule."
        ),
        matters="Is this name overextended in either direction?",
        watch="In strong trends, RSI 70+ persists for weeks. Combine with trend filter (SMA50 vs SMA200) before acting.",
    ),
    "macd": Term(
        short="Moving Average Convergence Divergence — 12-EMA minus 26-EMA, with a 9-EMA signal line.",
        full=(
            "MACD plots two short-term moving averages' difference. When the MACD line "
            "crosses above the signal line, momentum is shifting up; below, shifting down. "
            "The histogram visualizes the gap between MACD and signal. Useful as a momentum "
            "confirmation signal, prone to false starts in choppy markets."
        ),
        matters="Is short-term momentum strengthening or weakening?",
        watch="Lagging — by the time MACD confirms a trend, a chunk of the move has already happened.",
    ),

    # ---- Sector rotation -----------------------------------------------------
    "rrg": Term(
        short="Relative Rotation Graph — 4-quadrant view of sectors via relative strength vs RS-momentum.",
        full=(
            "RRG plots each sector by (1) its relative performance vs the benchmark and "
            "(2) the rate of change of that relative performance. Four quadrants:\n"
            "  • Leading (top-right) — outperforming AND accelerating\n"
            "  • Weakening (bottom-right) — outperforming BUT decelerating\n"
            "  • Lagging (bottom-left) — underperforming AND decelerating\n"
            "  • Improving (top-left) — underperforming BUT accelerating\n"
            "Sectors typically rotate clockwise: Improving → Leading → Weakening → Lagging → Improving."
        ),
        matters="Which sectors are catching tailwinds vs running out of steam?",
        watch="A snapshot only — the rotation pattern matters more than the current quadrant. A sector deep in 'Leading' might be near a top.",
    ),
    "rel_strength": Term(
        short="Asset's return minus benchmark's return over the same window.",
        full=(
            "Relative strength is the simplest 'is this winning vs the market?' metric. "
            "If XLK returned +15% over 3 months and SPY returned +8%, XLK's 3M relative "
            "strength is +7%. Doesn't say if absolute returns are good — both could be "
            "down — just whether the asset out- or under-performed."
        ),
        matters="Is this leading or lagging the market?",
        watch="Relative strength can come from the asset rallying OR the benchmark falling — same number, very different conclusions.",
    ),

    # ---- Scoring -------------------------------------------------------------
    "ars": Term(
        short="Aggregate Risk-adjusted Score — 0-100 composite of Technical, Valuation, Risk, Quality, Growth, sector-tilted.",
        full=(
            "ARS is the cockpit's house score. It blends 5 component scores (each 0-100):\n"
            "  Technical — trend regime + RSI sweet-spot + 1M momentum\n"
            "  Valuation — DCF + multiples blend (peer-relative when possible)\n"
            "  Risk — inverse of vol + drawdown\n"
            "  Quality — drawdown stability + dividend + fundamental coverage\n"
            "  Growth — 3M / 6M / 12M total returns\n"
            "Sector tilts (industry_config.py) reweight components — banks weight valuation more, "
            "tech weights growth more. Bucket: ≥75 strong_buy · 60-74 buy · 45-59 hold · "
            "30-44 reduce · <30 avoid. Thresholds are user-editable in Settings."
        ),
        matters="On a single 0-100 scale, how attractive is this name right now?",
        watch="ARS aggregates noisy inputs. Treat the bucket as a starting point for research, not a verdict.",
    ),
    "composite_score": Term(
        short="Legacy 0-1 scoring used by the Overview tab — Technical 45% + Risk 30% + Valuation 25%.",
        full=(
            "Composite is the older, narrower scoring engine. It produces the score chips "
            "shown on each Overview ticker and feeds the trim/add suggestions. It's simpler "
            "than ARS (3 components vs 5) and runs on every Overview render. ARS is the "
            "richer score; composite is the fast one used for 'today's actions.'"
        ),
        matters="A quick read on whether to trim a current holding or review a watchlist name.",
        watch="Composite has no quality or growth dimension. ARS is more complete but slower to compute.",
    ),
    "data_tier": Term(
        short="Tier 1 (paid premium) > Tier 2 (enhanced free) > Tier 3 (yfinance) — gates feature availability.",
        full=(
            "The cockpit currently runs at Tier 3 — yfinance scraping. That's why the "
            "header shows the ⚠ REDUCED DATA CONFIDENCE badge. At Tier 3 these features "
            "are disabled or unreliable: forward PEG, implied volatility surface (so "
            "Black-Scholes uses historical vol), analyst consensus estimates, multi-year "
            "fundamentals history. Wiring a paid feed (FMP, Polygon, Refinitiv) flips "
            "the tier and unlocks them."
        ),
        matters="How much should you trust the numbers? Tier 3 = treat as directional, not precise.",
        watch="DCF intrinsic values are particularly affected — Tier 3 lacks reliable free cash flow data, so the cockpit uses a P/S × 10% margin proxy.",
    ),

    # ---- Stress / Regime -----------------------------------------------------
    "stress_regime": Term(
        short="Forward MC re-run with covariance from a historical stress window (Covid 2020, Rate shock 2022, Q4 2018).",
        full=(
            "Stress regimes ask: 'if the bad market of 2020/2022/2018 returned with my "
            "current portfolio, what would VaR look like?' We replay each historical "
            "regime's correlation structure on today's holdings. The 'vs Current' multiplier "
            "shows how much riskier each regime would be. The cockpit currently runs three "
            "stress regimes; the math is general and easy to add more."
        ),
        matters="My portfolio is fine in calm markets — but what would a real crisis cost me?",
        watch="Past regimes don't repeat exactly. Use these as floors, not predictions.",
    ),
    "hmm_regime": Term(
        short="Hidden Markov Model trained on log returns to classify market state into vol regimes.",
        full=(
            "HMM tries to identify which of N hidden 'regimes' the market is currently in, "
            "where each regime has its own mean return and volatility. The cockpit fits a "
            "3-state Gaussian HMM (low_vol / neutral / high_vol). When HMM fails to converge, "
            "we fall back to a simple rolling-vol bucketed regime detector. Both are "
            "imperfect, but classifying current conditions helps you adjust risk taking."
        ),
        matters="Are we in a calm or stressed market regime right now?",
        watch="HMM can be slow to detect regime shifts. Frequent retraining helps but adds noise.",
    ),

    # ---- Decision -----------------------------------------------------------
    "trim_signal": Term(
        short="Held position with low composite score AND high risk multiplier — a candidate to consider reducing.",
        full=(
            "The cockpit flags a position to consider trimming when (1) its composite score "
            "is mediocre or weak and (2) it pulls more risk than its weight justifies. "
            "Intensity = (1 - composite) × 0.5 + min(risk_multiplier / 2, 1) × 0.5; only "
            "shown when intensity ≥ 0.50. This is a research signal, not an order — the "
            "cockpit never knows your tax situation, time horizon, or other commitments."
        ),
        matters="Where might I be over-allocated to a position that isn't earning its risk?",
        watch="Suggestions are based purely on price + risk math. They ignore taxes, transaction costs, and your personal goals. Always think before acting.",
    ),
    "add_signal": Term(
        short="Unheld watchlist ticker with high composite + acceptable valuation — a candidate to consider reviewing.",
        full=(
            "An 'add' candidate is a watchlist ticker you don't yet own with a strong "
            "composite score AND a valuation that isn't extreme. Intensity = composite × 0.7 "
            "+ valuation_bonus × 0.3. Threshold-gated at 0.50. The signal flags candidates "
            "for further research — not a buy order. Always do your own due diligence on "
            "fit, sector exposure, and timing."
        ),
        matters="Which watchlist names look most worth a closer look right now?",
        watch="A 'consider reviewing' signal is upstream of a buy decision. The cockpit doesn't know your existing exposures or goals.",
    ),
    "concentration": Term(
        short="How tightly your portfolio is bunched into a small number of positions.",
        full=(
            "Three buckets, derived from HHI and largest-position weight:\n"
            "  • Diversified — top weight < 25% AND HHI < 0.20\n"
            "  • Moderate — top weight 25-40% OR HHI 0.20-0.30\n"
            "  • Concentrated — top weight ≥ 40% OR HHI ≥ 0.30\n"
            "Concentration isn't bad per se — Buffett-style portfolios are deliberately concentrated. "
            "But high concentration means single-name risk dominates, and any one bad pick can hurt badly."
        ),
        matters="How much of my portfolio's fate rests on a single position?",
        watch="HHI doesn't see sector or factor concentration. A 'diversified' equal-weight tech-only portfolio still has huge sector concentration.",
    ),

    # ---- Other / utility ----------------------------------------------------
    "wacc": Term(
        short="Weighted Average Cost of Capital — blended discount rate for DCF, weighted by debt and equity proportions.",
        full=(
            "WACC is what a company needs to earn just to break even with its capital "
            "providers. Equity holders demand a higher return (riskier); debt holders less. "
            "WACC blends them by their proportion of total capital. The cockpit uses sector "
            "midpoint WACCs from industry_config.py — Utilities ~6%, Tech ~10%, Energy ~10%."
        ),
        matters="What rate do I discount future cash flows at?",
        watch="WACC drifts as interest rates change. Last decade's WACCs were too low for many sectors.",
    ),
    "free_cash_flow": Term(
        short="Operating cash flow minus capital expenditures — cash the business throws off after maintaining itself.",
        full=(
            "FCF is the gold-standard cash measure: how much cash does the business actually "
            "produce that's free to return to shareholders or reinvest? Unlike earnings, it "
            "can't be smoothed by accounting choices. DCF uses FCF as its starting point. At "
            "Tier 3 the cockpit doesn't have reliable FCF history, so DCF uses a proxy "
            "(market_cap × P/S × 10% margin) — flagged in warnings."
        ),
        matters="How much real cash is this business generating that I could in principle have?",
        watch="One-time items (asset sales, working capital swings) can spike FCF in a single year. Look at multi-year averages.",
    ),

    # ---- Phase J: Regime-aware screener -------------------------------------
    "regime": Term(
        short="Hidden Markov Model classification of the market into bull / sideways / bear states from 4 features.",
        full=(
            "Markets rotate through persistent states with very different statistical "
            "properties: bull (positive drift, low vol, expanding breadth), bear (negative "
            "drift, high vol, contracting breadth), and sideways (chop). The screener fits "
            "a 4-feature Gaussian HMM on SPY log-returns, 20d realized vol, normalized VIX, "
            "and a breadth proxy, then assigns each state a label by ranking mean return "
            "and inverse vol. The blended weight across the 5 signals shifts with the "
            "model's posterior probabilities — preventing cliff-edge rescoring at boundaries."
        ),
        matters="Which scoring weights are right for THIS market environment?",
        watch="Regime labels can flip transiently. A 3-day hysteresis filter requires consistent + confident signal before flipping. Treat 'unstable' calls with extra skepticism.",
    ),
    "veto_gate": Term(
        short="Pre-composite filter: stocks with extreme GARCH vol or MC tail-loss probability are rejected.",
        full=(
            "Before computing any composite score, each stock is gated on two raw risk "
            "metrics: GARCH(1,1) daily volatility and Monte-Carlo loss probability over a "
            "20-day horizon. Thresholds tighten in bear regime (2.5% vol / 20% loss) and "
            "loosen in bull (4.5% / 30%). Vetoed stocks get composite_score=0. If a sector "
            "has zero passers, the screener relaxes thresholds by 20% (max 2 passes) so the "
            "tab is never empty."
        ),
        matters="Should this stock be considered at all, before we even rank it?",
        watch="A relaxed-veto candidate is flagged with ⚑ — its risk metrics exceeded the regime threshold and only survived the safety-valve loosening.",
    ),
    "composite_score_screener": Term(
        short="Weighted sum of 5 signal scores in [0,1]: ARIMA + Kalman + GARCH + Monte-Carlo + Sharpe.",
        full=(
            "The screener's per-stock score blends 5 underlying signals using regime-aware "
            "weights. ARIMA captures price-momentum forecasts; Kalman picks up smoothed "
            "trend slope; GARCH measures volatility efficiency; Monte-Carlo measures "
            "tail-loss risk; Sharpe rewards risk-adjusted return. In bull regime ARIMA "
            "carries 40% of the weight; in bear, GARCH and MC dominate at 60%. Stocks that "
            "fail the veto get composite_score=0 — they don't contribute to top-N rankings."
        ),
        matters="How does this stock rank against its sector peers in today's market environment?",
        watch="A high composite is the sum of MANY underlying assumptions (HMM regime detection, signal models, weight matrix). Treat it as a research input, not a buy order.",
    ),
    "arima": Term(
        short="Autoregressive Integrated Moving Average — short-horizon forecast of price direction.",
        full=(
            "ARIMA fits an AR(2)+I(1)+MA(2) model to log prices and forecasts the price 20 "
            "trading days ahead. The signal score is sigmoid(forecast_return / hist_vol) — "
            "values near 0.5 mean 'no edge'; >0.5 favors longs. ADF stationarity test "
            "selects the differencing order. The screener uses a fixed (2,d,2) by default; "
            "pmdarima auto-search is supported but disabled (slow on 220 stocks)."
        ),
        matters="Does the recent price path imply a short-term tailwind or headwind?",
        watch="ARIMA fits backward-looking patterns. A regime change (e.g., earnings shock) can render the forecast meaningless overnight.",
    ),
    "kalman": Term(
        short="Kalman filter slope — recent trend direction of a smoothed price series.",
        full=(
            "Kalman/EMA produces a noise-filtered estimate of the underlying price level. "
            "We then fit a linear slope to the last 20 filtered points, normalize by the "
            "current price, and squash through sigmoid. Score >0.5 = positive trend. The "
            "filter handles missing data better than a moving average and reacts faster to "
            "real signals while suppressing noise. Falls back to α=0.3 EMA if pykalman is "
            "unavailable."
        ),
        matters="Is the recent trend pointing up or down through the noise?",
        watch="Slope-based signals lag turning points by design. Kalman is best as a tiebreaker, not a primary signal.",
    ),
    "garch": Term(
        short="GARCH(1,1) volatility forecast — annualized vol used both as a score and as a veto input.",
        full=(
            "GARCH models persistence in volatility — high-vol days cluster together, calm "
            "days do too. We fit GARCH(1,1) on log returns and use the 20-day forecasted "
            "annualized vol two ways: (1) the SCORE is 1 - min(ann_vol/0.60, 1.0), so "
            "high-vol stocks score low; (2) the daily vol is also the input to the veto "
            "gate, which rejects stocks above the regime-specific threshold (2.5–4.5%/day)."
        ),
        matters="How risky has this stock been, and is that risk forecast getting worse?",
        watch="GARCH only captures realized return vol — it can't see imminent earnings, M&A news, or macro shocks. Use it as 'how messy was the recent past?', not 'how risky is the future?'",
    ),
    "mc_loss_prob": Term(
        short="Monte Carlo loss probability — fraction of simulated 20-day paths that end below 90% of today's price.",
        full=(
            "We simulate 10,000 GBM paths per stock using Ito-corrected drift (μ - 0.5σ²) "
            "and the 63-day rolling drift/vol. We do this with 5 different seeds to "
            "quantify uncertainty in the loss-probability estimate. The signal score is "
            "1 - loss_probability; the raw loss_probability is also the second input to "
            "the veto gate."
        ),
        matters="How likely is this stock to drop more than 10% over the next month?",
        watch="GBM assumes returns are i.i.d. normal — they're not. Tail probabilities are systematically understated when fat tails matter (i.e., precisely when you most care about them).",
    ),
    "signal_ic": Term(
        short="Information Coefficient — rank correlation between a signal score and realized forward return.",
        full=(
            "IC measures whether a signal's ranking actually predicts subsequent returns. "
            "We compute Spearman's rank correlation between the signal score (across all "
            "stocks) and the next-20d realized return. IC > 0.05 is considered meaningful "
            "in equity research. Per-regime ICs let us see if a signal works only in a "
            "specific environment — feeding into the WEIGHT_MATRIX design."
        ),
        matters="Does this signal actually predict returns, or is it just adding noise?",
        watch="IC is sample-size hungry. A single-quarter IC of 0.10 might be pure luck. Look for IC stability across years and regimes.",
    ),
    "walk_forward": Term(
        short="Walk-forward cross-validation — retrains the model on rolling history, evaluates on the next out-of-sample window.",
        full=(
            "Walk-forward is the time-series version of k-fold CV. We start with N years of "
            "history, train the regime HMM + score the universe, evaluate against actual "
            "next-20d returns, then slide the window forward and repeat. Unlike random k-fold, "
            "no future data ever leaks into training. The screener's backtest module reports "
            "precision@5, hit rate by regime, and per-signal IC across all walk-forward windows."
        ),
        matters="Would the screener have actually worked if I'd run it 6 months / 1 year / 2 years ago?",
        watch="Walk-forward over too short a window misses regime variation. The default 252-day train window catches a year of bull/bear cycling but misses generational shifts (2008, 2020).",
    ),
}


def get(term_key: str) -> Term | None:
    """Case-insensitive lookup. Returns None if term is unknown."""
    return GLOSSARY.get(term_key.lower())


def known_terms() -> list[str]:
    return sorted(GLOSSARY.keys())


__all__ = ["Term", "GLOSSARY", "get", "known_terms"]
