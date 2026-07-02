"""render/glossary.py — the single source of truth for plain-language explanations.

Every value, concept, and theory the dashboard shows has one entry here, so the
HTML term-component, tooltips, Learn-mode, worked examples, and the glossary modal
all read from ONE place (no scattered copies). Wording reuses the plain-language
already in `render/notes.py` so the HTML and Markdown surfaces agree.

Each entry:
  plain    — plain-English label that LEADS (e.g. "Prediction accuracy")
  term     — the real quant/stats term kept in parentheses (e.g. "IC")
  short    — one-line tooltip (≤ ~140 chars), no jargon
  long     — a fuller Learn-mode paragraph
  example  — a concrete worked example (shown on click)
  theory   — optional "why it works" / the idea behind it

This is first-party STATIC content (no user data), embedded once as JSON for the
client JS. `label()` builds "Plain (Term)"; `as_json()` serialises the registry.
"""
from __future__ import annotations

import json
import re

# key -> {plain, term, short, long, example, theory?}
GLOSSARY: dict[str, dict] = {
    # ── headline / portfolio ────────────────────────────────────────────────
    "paper_value": {
        "plain": "Pretend portfolio value", "term": "paper",
        "short": "What the make-believe $10,000 account is worth right now. No real money is ever used.",
        "long": "This tool trades on paper only — it starts with a pretend $10,000 and tracks what "
                "that account would be worth if it had really made these trades. It's a research "
                "sandbox, not a brokerage.",
        "example": "Start $10,000 → after some winning paper trades it might read $10,420.",
        "theory": "Paper trading lets you test an investing idea honestly without risking a cent.",
    },
    "total_pnl": {
        "plain": "Total profit or loss", "term": "P&L",
        "short": "Money made or lost so far — both from sold positions and ones still held.",
        "long": "P&L means 'profit and loss'. This adds up gains already locked in (from positions "
                "we sold) plus paper gains on positions we still hold.",
        "example": "Sold trades made +$200, current holdings are up +$150 → Total P&L +$350.",
    },
    "drawdown": {
        "plain": "Biggest drop from the high", "term": "drawdown",
        "short": "How far the account is below the best value it ever reached. Smaller is safer.",
        "long": "Drawdown measures pain: from the highest the portfolio ever got, how far has it "
                "since fallen? A big drawdown means a rough ride even if you end up ahead.",
        "example": "Peak $11,000, now $10,200 → drawdown −7.3%.",
        "theory": "Two strategies can earn the same total but one with smaller drawdowns is far "
                  "easier to actually stick with.",
    },
    "positions": {
        "plain": "Stocks currently held", "term": "open positions",
        "short": "How many different stocks the paper account owns right now.",
        "long": "Each position is one stock the paper account currently holds. The strategy spreads "
                "across sectors rather than piling into one name.",
        "example": "Holding AAPL, JNJ, CAT and 5 others → 8 open positions.",
    },
    "cash": {
        "plain": "Un-invested money", "term": "cash",
        "short": "Pretend dollars not yet put into any stock — ready for the next buy.",
        "long": "Cash is the part of the paper account sitting on the sidelines. The strategy keeps "
                "a small reserve and deploys the rest on its monthly buy day.",
        "example": "$10,000 account with $9,000 in stocks → $1,000 cash.",
    },
    "top_overall": {
        "plain": "This run's best ideas", "term": "top picks",
        "short": "The highest-scoring stocks across all sectors from the latest screen.",
        "long": "After scoring every stock, the engine surfaces the strongest few overall — its "
                "highest-conviction ideas this run.",
        "example": "Out of 220 stocks, the top 5 by score become the top picks.",
    },

    # ── regime ──────────────────────────────────────────────────────────────
    "regime": {
        "plain": "Market mood", "term": "regime",
        "short": "Whether the market is calm & rising (bull), choppy (sideways), or falling (bear).",
        "long": "A 'regime' is the overall weather of the market. The engine sorts it into three "
                "moods — bull (calm, rising), sideways (choppy, going nowhere), and bear (falling, "
                "fearful) — and gets more defensive as the mood sours.",
        "example": "Calm uptrend with low fear → 'bull'; sharp selloff with high fear → 'bear'.",
        "theory": "The same stock signal can mean different things in a calm vs a panicking market, "
                  "so the engine adjusts how aggressive it is to match the mood.",
    },
    "regime_confidence": {
        "plain": "How sure of the mood", "term": "confidence",
        "short": "The model's certainty (0–100%) that it has labelled the market's mood correctly.",
        "long": "The mood label comes from a statistical model, and this is how confident that "
                "model is. Low confidence means the market is in between moods.",
        "example": "85% confidence in 'bull' = fairly sure; 52% = borderline, treat with caution.",
    },
    "hmm": {
        "plain": "The mood-detector", "term": "Hidden Markov Model",
        "short": "A statistical model that infers the market's hidden mood from price, volatility and fear gauges.",
        "long": "A Hidden Markov Model watches signals you CAN see (returns, how jumpy prices are, "
                "the VIX 'fear index', how many stocks are rising) to infer the mood you CAN'T "
                "directly see. It's the same family of math used in speech recognition.",
        "example": "Rising prices + low volatility + low fear → the model infers a 'bull' state.",
    },

    # ── the signals ─────────────────────────────────────────────────────────
    "composite": {
        "plain": "Overall score", "term": "composite",
        "short": "A single 0–1 conviction score blending all the signals. Higher = stronger idea.",
        "long": "Each stock gets several individual signal scores; the composite blends them into "
                "one number between 0 and 1 using weights that depend on the market mood. It's the "
                "engine's bottom-line conviction.",
        "example": "Strong trend + good risk profile → composite 0.78 (high conviction).",
    },
    "weight_matrix": {
        "plain": "Signal recipe", "term": "weight matrix",
        "short": "How much each signal counts toward the overall score — and it changes with the market mood.",
        "long": "The weight matrix is the recipe: in a calm market it might lean on trend signals, "
                "in a fearful one it leans on risk signals. The weights for each mood always add to 100%.",
        "example": "Bull mood: trend 40%, risk 30%, efficiency 30%.",
    },
    "arima": {
        "plain": "Trend forecaster", "term": "ARIMA",
        "short": "Reads recent price history to guess the direction over the next few weeks.",
        "long": "ARIMA is a classic time-series model. It studies a stock's own recent price path "
                "and projects whether it's likelier to drift up or down over the next ~month.",
        "example": "A steady climb that ARIMA expects to continue → a high trend score.",
        "theory": "If prices have momentum or mean-revert in a learnable way, recent history carries "
                  "a (weak) clue about the near future.",
    },
    "kalman": {
        "plain": "Noise smoother", "term": "Kalman filter",
        "short": "Strips the day-to-day jitter out of a price to estimate its 'true' underlying level.",
        "long": "A Kalman filter is a smoothing algorithm (famously used in spacecraft navigation). "
                "Here it separates a stock's real trend from the random daily noise, which helps spot "
                "turns and mean-reversion.",
        "example": "A jagged price gets smoothed into a clean line the model tracks.",
    },
    "garch": {
        "plain": "Turbulence forecaster", "term": "GARCH",
        "short": "Predicts how wild a stock's swings will be. Calmer stocks score higher; too wild gets vetoed.",
        "long": "GARCH forecasts volatility — how much a stock is about to bounce around. The engine "
                "prefers calmer names and uses this mainly as a safety gate to exclude the most "
                "turbulent stocks.",
        "example": "A stock whose swings are widening → high predicted volatility → may be vetoed.",
        "theory": "Volatility clusters: turbulent days tend to follow turbulent days, so recent "
                  "calm or chaos forecasts the near future.",
    },
    "monte_carlo": {
        "plain": "Thousands of what-ifs", "term": "Monte Carlo",
        "short": "Simulates 10,000 random futures for a stock to estimate its chance of a big loss.",
        "long": "Monte Carlo simulation rolls the dice 10,000 times — generating thousands of "
                "possible price paths from the stock's own history — and measures how often it ends "
                "up badly down. Used as a risk gate.",
        "example": "If 35% of 10,000 simulated paths end down >10%, that's a high tail-risk flag.",
        "theory": "When the math is too messy to solve directly, simulate it many times and read "
                  "the odds off the results.",
    },
    "sharpe": {
        "plain": "Reward for the risk", "term": "Sharpe ratio",
        "short": "Annualized return divided by how much it bounces around. Higher = smoother gains.",
        "long": "The Sharpe ratio divides a stock's return by how much it bounced around to get it. "
                "A high Sharpe means the gains came smoothly; a low one means a wild ride for the "
                "same result.",
        "example": "Two stocks each up 10%; the calmer one has the higher Sharpe.",
        "theory": "Risk-adjusted return is fairer than raw return — it rewards getting there without "
                  "white-knuckle volatility.",
    },
    "momentum": {
        "plain": "Winners keep winning", "term": "12-1 momentum",
        "short": "How much a stock rose over the past year (skipping the last month). Strong past winners often keep going.",
        "long": "Momentum measures a stock's trailing ~12-month return, deliberately skipping the "
                "most recent month (which often briefly reverses). It's one of the most studied "
                "patterns in markets. Here it's measured-and-watched, not yet driving live trades.",
        "example": "Stock rose 30% over the year → high momentum score 0.78.",
        "theory": "Jegadeesh-Titman / Asness: past winners tend to keep outperforming for a while — "
                  "one of the few effects that shows up across decades and markets.",
    },

    # ── veto / risk gates ───────────────────────────────────────────────────
    "veto": {
        "plain": "Safety veto", "term": "veto",
        "short": "A pass/fail safety gate: a stock that fails any risk check is dropped before ranking.",
        "long": "Before a stock can be a pick it must clear safety checks — not too volatile, not "
                "too much crash risk, not right before earnings. Fail one and it's vetoed (excluded), "
                "no matter how good its score.",
        "example": "A high-scoring stock reporting earnings tomorrow → vetoed for safety.",
    },
    "veto_rate": {
        "plain": "Share screened out", "term": "veto rate",
        "short": "What percent of all stocks failed the safety checks this run.",
        "long": "The veto rate is how strict the gates were this run — the fraction of the universe "
                "that got filtered out for risk reasons.",
        "example": "220 stocks, 77 vetoed → 35% veto rate.",
    },
    "earnings_blackout": {
        "plain": "Earnings blackout", "term": "EARNINGS_BLACKOUT",
        "short": "Skip stocks within a few days of an earnings report — too unpredictable to trade safely.",
        "long": "Earnings announcements cause big, coin-flip price jumps. The engine refuses to buy "
                "anything within ~5 trading days of earnings — and this veto is never relaxed.",
        "example": "Stock reports earnings in 3 days → blacked out this run.",
    },
    "delisted_stale": {
        "plain": "Out-of-date data", "term": "delisted/stale",
        "short": "Stocks skipped because their price data is missing or too old to trust.",
        "long": "If a stock's cached data hasn't updated recently (delisted, halted, or a data gap), "
                "the engine skips it rather than score it on stale numbers.",
        "example": "No fresh price in 10+ days → skipped as stale.",
    },

    # ── screener / sectors ──────────────────────────────────────────────────
    "universe": {
        "plain": "All stocks considered", "term": "universe",
        "short": "The full list of stocks the engine evaluates each run (about 220 large companies).",
        "long": "The universe is the pool of candidates — roughly 220 large U.S. companies spread "
                "across every sector — that get scored every run.",
        "example": "220 stocks across 11 sectors form the universe.",
    },
    "sector": {
        "plain": "Industry group", "term": "sector",
        "short": "An industry bucket (Tech, Healthcare, Energy…). Picks are spread across them for balance.",
        "long": "Sectors group companies by industry. The engine takes the best from each sector "
                "rather than letting one hot industry dominate — built-in diversification.",
        "example": "Apple → Technology; Johnson & Johnson → Healthcare.",
    },
    "equity_curve": {
        "plain": "Value over time", "term": "equity curve",
        "short": "A line of the paper account's value through time, vs simply buying the S&P 500 (SPY).",
        "long": "The equity curve plots the strategy's value over time (green) against a plain "
                "buy-and-hold of the S&P 500 (the dashed SPY line). Both start at 100 so you compare "
                "shape, not dollars. Above the dashed line = beating the market.",
        "example": "Strategy line at 112 vs SPY at 108 → ahead of the market by 4 points.",
    },
    "spy": {
        "plain": "The market benchmark", "term": "SPY",
        "short": "An S&P 500 fund — the 'just buy the whole market' yardstick every strategy is measured against.",
        "long": "SPY tracks the S&P 500, the 500 biggest U.S. companies. It's the default 'do nothing "
                "clever' benchmark: if a strategy can't beat just holding SPY, it isn't adding value.",
        "example": "Strategy +18% vs SPY +12% → +6% of added value.",
    },

    # ── positions detail ────────────────────────────────────────────────────
    "shares": {
        "plain": "Units held", "term": "shares",
        "short": "How many units of the stock the paper account owns (fractions allowed).",
        "long": "Shares are the count of units held. Fractional shares are allowed, so a position can "
                "be 4.7 shares if that's what the dollar budget bought.",
        "example": "$950 budget at $200/share → 4.75 shares.",
    },
    "cost_basis": {
        "plain": "Average buy price", "term": "cost basis",
        "short": "The average price paid per share — the break-even line for the position.",
        "long": "Cost basis is what you paid on average per share. Compare it to the current price to "
                "see if the position is up or down.",
        "example": "Bought at $190 avg, now $205 → up $15/share.",
    },
    "market_value": {
        "plain": "Worth today", "term": "market value",
        "short": "What the position is worth right now: shares × current price.",
        "long": "Market value is today's worth of a holding — the number of shares times the current "
                "price.",
        "example": "5 shares × $205 = $1,025 market value.",
    },
    "unrealized_pnl": {
        "plain": "Paper gain (not sold)", "term": "unrealized P&L",
        "short": "Profit or loss on a position you still hold — only real once you sell.",
        "long": "Unrealized P&L is profit/loss on paper for a position still open. It moves every day "
                "with the price and only becomes 'realized' when the position is sold.",
        "example": "Up $75 on a held stock = +$75 unrealized (could still change).",
    },

    # ── sentiment ───────────────────────────────────────────────────────────
    "finbert": {
        "plain": "News mood-reader (AI)", "term": "FinBERT",
        "short": "An AI model that reads recent headlines and rates them positive, negative or neutral.",
        "long": "FinBERT is an AI language model trained on financial news. It reads a stock's recent "
                "headlines and scores the tone. It's an optional, off-by-default overlay — never auto-"
                "blocks a trade unless turned on.",
        "example": "5 upbeat headlines, 1 bad → overall positive tone.",
    },
    "sentiment_score": {
        "plain": "News tone score", "term": "sentiment score",
        "short": "Headline mood from −1 (very bad news) to +1 (very good news).",
        "long": "The sentiment score averages the tone of recent headlines onto a −1…+1 scale: "
                "negative means bad-news flow, positive means good-news flow.",
        "example": "Score +0.42 = mostly positive recent coverage.",
    },

    # ── scorecard / backtest ────────────────────────────────────────────────
    "scorecard": {
        "plain": "Report card", "term": "scorecard",
        "short": "Grades past picks against what prices actually did afterward. Honest, accumulating proof.",
        "long": "The scorecard waits and checks: did the picks actually beat the market after we "
                "named them? It needs a few weeks of forward data before it can say much — an honest, "
                "out-of-sample track record.",
        "example": "Of last month's picks, 6 of 10 beat SPY → 60% hit rate.",
    },
    "hit_rate": {
        "plain": "How often right", "term": "hit rate",
        "short": "The share of picks that beat the market over the measured window.",
        "long": "Hit rate is the batting average: out of all picks, what fraction outperformed SPY? "
                "Above 50% means more winners than losers vs the market.",
        "example": "6 of 10 picks beat SPY → 60% hit rate.",
    },
    "alpha": {
        "plain": "Edge over the market", "term": "alpha",
        "short": "How much a pick beat (or lagged) simply buying the market, in percentage points.",
        "long": "Alpha is the value added beyond the market: a pick's return minus what SPY did over "
                "the same window. Positive alpha = genuine edge; negative = you'd have done better "
                "just buying the index.",
        "example": "Pick +9%, SPY +6% → alpha +3 points.",
        "theory": "Beating the market is hard, so consistent positive alpha is the whole game.",
    },
    "horizon": {
        "plain": "How long we wait", "term": "horizon",
        "short": "The window after a pick before we grade it (e.g. 7, 28, 84 days).",
        "long": "A pick is judged over a horizon — a set number of days after it was named. Short "
                "horizons are noisy; longer ones are more telling but take time to fill in.",
        "example": "84-day horizon = grade the pick three months later.",
    },

    # ── signal lab ──────────────────────────────────────────────────────────
    "ic": {
        "plain": "Prediction accuracy", "term": "IC",
        "short": "Does ranking stocks by a signal actually predict which go up? Positive = yes, negative = backwards.",
        "long": "IC (Information Coefficient) checks whether sorting stocks by a signal lines up with "
                "how they actually performed next period. +1 is perfect, 0 is useless, negative means "
                "the signal predicts the opposite of what happens.",
        "example": "IC +0.06 = a small but real edge; IC −0.12 = it predicts backwards.",
        "theory": "A signal is only worth its weight if its ranking holds up out-of-sample — IC is "
                  "the honest test of that.",
    },
    "info_ratio": {
        "plain": "Signal consistency", "term": "info ratio",
        "short": "How reliable a signal's edge is across dates — its accuracy divided by its wobble.",
        "long": "The info ratio standardises a signal's accuracy by how much it varies date to date. "
                "A high ratio means a small edge that shows up dependably; a low one means a noisy, "
                "untrustworthy edge.",
        "example": "Steady small edge → high info ratio; same edge but erratic → low.",
    },
    "quintile_spread": {
        "plain": "Best-minus-worst gap", "term": "quintile spread",
        "short": "The return gap between a signal's top fifth of stocks and its bottom fifth.",
        "long": "Split stocks into fifths by a signal; the quintile spread is how much the top fifth "
                "outperformed the bottom fifth next period. A practical 'does this signal separate "
                "winners from losers?' measure.",
        "example": "Top fifth +5%, bottom fifth +2% → spread +3 points.",
    },
    "bonferroni": {
        "plain": "Luck filter", "term": "Bonferroni significance",
        "short": "A stricter bar when testing many signals at once, so a fluke doesn't look real.",
        "long": "Test 5 signals and one may look good by chance. The Bonferroni correction raises the "
                "bar accordingly — a ✓ means the edge clears the tougher, multiple-test threshold; a "
                "✗ means it's suggestive but not proven.",
        "example": "A signal needs a much higher score to earn a ✓ when 5 are tested together.",
        "theory": "The more things you test, the more flukes you'll see — so demand stronger evidence.",
    },
    "verdict": {
        "plain": "Keep or drop call", "term": "verdict",
        "short": "Plain-English judgement on a signal: keep it, it's weak, or drop it / it predicts backwards.",
        "long": "The verdict summarises each signal's evidence in words — from 'KEEP — real edge' down "
                "to 'DROP / FLIP — predicts backwards' — so you don't have to read the numbers.",
        "example": "IC −0.12 → 'DROP / FLIP — predicts backwards'.",
    },

    # ── fleet ───────────────────────────────────────────────────────────────
    "fleet": {
        "plain": "Strategy fleet", "term": "fleet",
        "short": "Several paper portfolios race in parallel, each following one strategy on the same weekly screen.",
        "long": "Instead of forward-testing one strategy at a time, the fleet runs a separate $10,000 "
                "paper portfolio per strategy — same screen, same safety vetoes, different signal "
                "weighting. The leaderboard shows who is actually ahead on real forward data, which "
                "is the evidence a backtest can't fake.",
        "example": "ARIMA-only, the default 5-signal blend, and a momentum mix each run their own "
                   "$10k book; SPY buy-hold rides along as the control.",
    },

    # ── tournament ──────────────────────────────────────────────────────────
    "tournament": {
        "plain": "Strategy bake-off", "term": "tournament",
        "short": "~20 strategy variations race over real history to see which would have won — a hypothesis, not proof.",
        "long": "The tournament pits ~20 strategy variations (different signal mixes, concentrations, "
                "sizing) against each other and against 'dumb' benchmarks over real historical prices. "
                "It generates hypotheses; it does not prove future results.",
        "example": "20 variants race over 3 years; the winner is examined, not blindly trusted.",
    },
    "control": {
        "plain": "Sanity-check benchmark", "term": "control",
        "short": "Dumb baselines (buy SPY, buy everything, buy 20 at random) the clever strategies must beat.",
        "long": "Controls are the null hypotheses: just holding SPY, holding the whole universe, or "
                "picking 20 stocks at random. If the clever variants can't beat random, the 'edge' is "
                "noise.",
        "example": "If 'random 20' beats your strategy, the strategy has no real skill.",
        "theory": "Always race your idea against luck — beating a thoughtful baseline is the real bar.",
    },
    "in_sample": {
        "plain": "Practice data", "term": "in-sample",
        "short": "The history used to PICK the winning strategy — easy to look good on data you trained on.",
        "long": "In-sample is the stretch of history used to choose the winner. Looking good here is "
                "cheap (you tuned to it), which is why the out-of-sample check matters.",
        "example": "Pick the best variant on 2021–2023 (in-sample)…",
    },
    "out_of_sample": {
        "plain": "Fresh-data test", "term": "out-of-sample (OOS)",
        "short": "Held-back history the winner never saw — the honest test of whether the edge is real.",
        "long": "Out-of-sample is the held-out stretch the winner was NOT chosen on. If a strategy "
                "only shines in-sample but flops out-of-sample, it was curve-fit, not skilful.",
        "example": "…then judge it on 2024 (out-of-sample) it never trained on.",
        "theory": "The only credible backtest result is one on data the strategy never peeked at.",
    },
    "excess": {
        "plain": "Beat-the-market amount", "term": "excess vs SPY",
        "short": "How much more (or less) a strategy returned than just buying the S&P 500.",
        "long": "Excess is return above the SPY benchmark over the same window. Positive means it beat "
                "the market; negative means the index would have been better.",
        "example": "Strategy +25%, SPY +18% → +7% excess.",
    },
    "max_drawdown": {
        "plain": "Worst peak-to-low fall", "term": "max drawdown",
        "short": "The deepest drop a strategy suffered from a high to a low. Smaller = gentler ride.",
        "long": "Max drawdown is the worst peak-to-trough fall over the whole test — the stomach-"
                "churn metric. A strategy with a smaller max drawdown is easier to stick with.",
        "example": "Fell from $12k to $10.5k at worst → −12.5% max drawdown.",
    },

    # ── rigor ───────────────────────────────────────────────────────────────
    "dsr": {
        "plain": "Real-skill probability", "term": "Deflated Sharpe Ratio",
        "short": "After trying ~20 strategies, the chance the winner's edge is real and not just the luckiest try.",
        "long": "Try enough strategies and one looks great by luck. The Deflated Sharpe Ratio "
                "discounts the winner's score by how lucky the BEST of 20 random tries would look, "
                "then gives the probability the edge is genuine. Near 1.0 = robust; under 0.5 = "
                "probably luck.",
        "example": "DSR 0.99 = very likely real even after accounting for 20 tries.",
        "theory": "Bailey & López de Prado: the more strategies you test, the higher the bar the "
                  "winner must clear to be believed.",
    },
    "cpcv": {
        "plain": "Many fresh-data tests", "term": "CPCV",
        "short": "Instead of one fresh-data test, run many overlapping ones to see if the edge holds across windows.",
        "long": "Combinatorial Purged Cross-Validation replaces a single held-out test with many — "
                "holding out different chunks of history in turn (with buffers to avoid leakage). You "
                "get a spread of results, not one lucky number. Most windows positive = robust.",
        "example": "Across 15 held-out windows, 73% were positive → reasonably robust.",
        "theory": "One test can flatter or fool you; a distribution of tests is far harder to game.",
    },
    "transaction_cost": {
        "plain": "Trading friction", "term": "transaction cost",
        "short": "Real-world fees, spread and slippage subtracted so the backtest isn't fantasy.",
        "long": "Every trade costs a little (commission, bid-ask spread, slippage). Frictionless "
                "backtests overstate returns, so the engine subtracts a realistic per-trade haircut — "
                "more for high-churn strategies.",
        "example": "Quarterly rebalancing at ~20bps per trade trims the headline return slightly.",
    },
    "turnover": {
        "plain": "How much it trades", "term": "turnover",
        "short": "The fraction of the portfolio swapped out each rebalance. High turnover = more fees.",
        "long": "Turnover measures churn — what share of holdings change each rebalance. Low-turnover "
                "strategies pay less friction; high-turnover ones need a bigger edge to overcome costs.",
        "example": "Replace 1 of 10 holdings → ~10% turnover that period.",
    },
    "rebalance": {
        "plain": "Refresh the holdings", "term": "rebalance",
        "short": "Periodically sell stale picks and buy the new best ones (here, roughly quarterly).",
        "long": "Rebalancing is the periodic refresh: re-run the screen, sell what no longer ranks, "
                "buy the new leaders. More frequent = more responsive but more costly.",
        "example": "Every quarter, swap out faded picks for fresh top-ranked ones.",
    },

    # ── ops / co-pilot ──────────────────────────────────────────────────────
    "automation_health": {
        "plain": "Is it running?", "term": "automation health",
        "short": "A green/amber/red check that the scheduled jobs actually ran on time.",
        "long": "The engine runs itself on a schedule. This beacon confirms the last run succeeded "
                "(green), failed (red), or is overdue (amber — e.g. the Mac was asleep).",
        "example": "Amber 'no run in 40h' → the laptop was off when the job was due.",
    },
    "reconciliation": {
        "plain": "Do the books balance?", "term": "reconciliation",
        "short": "An independent replay of every trade, checked against each P&L number shown.",
        "long": "After each daily run the engine replays the whole trade ledger from scratch — "
                "cash in/out, share counts, cost bases — and compares the result against the "
                "broker book, the positions table, and today's snapshot. Any mismatch over one "
                "cent is flagged here instead of silently propagating to the numbers you read.",
        "example": "A stray test once wrote a fake $800 position into the live book; this check "
                   "now catches that class of drift the same day.",
    },
    "copilot": {
        "plain": "AI second opinion", "term": "co-pilot",
        "short": "A short plain-English read of the situation from Claude. Advisory only — it never trades.",
        "long": "The co-pilot is an optional AI commentary layer (Claude) that reads the current "
                "picture and explains it in words. It's a second opinion for you, never an actor — it "
                "cannot place trades.",
        "example": "'The market looks defensive; the top picks skew toward stable names.'",
    },

    # ── company health ───────────────────────────────────────────────────────
    "health_score": {
        "plain": "Company health", "term": "health score",
        "short": "Is the company financially sound? Grades its profitability + balance sheet against the minimums for its sector.",
        "long": "Each company's quality metrics (return on equity, margins, debt, liquidity) are "
                "checked against the minimum 'floors' that are normal for its sector. The score is how "
                "many it passes: 🟢 STRONG (most), 🟡 FAIR, 🔴 WEAK, ⚪ data unavailable. It's a "
                "soundness check, separate from whether the stock is cheap.",
        "example": "Passes 4 of 4 floors → 🟢 STRONG; passes 1 of 4 → 🔴 WEAK.",
        "theory": "A cheap stock in a financially weak company can be a value trap — health screens "
                  "for that.",
    },
    "roe": {
        "plain": "Profit on shareholders' money", "term": "ROE",
        "short": "Return on equity — profit earned per dollar of shareholder capital. Higher = more efficient.",
        "long": "ROE divides net profit by shareholders' equity: how much profit the company makes on "
                "the money owners have put in. Consistently high ROE signals a quality business.",
        "example": "ROE 18% = 18¢ of profit a year per $1 of equity.",
    },
    "debt_to_equity": {
        "plain": "Borrowing vs own money", "term": "debt/equity",
        "short": "How much the company borrows relative to its own capital. Lower = less risky; 'normal' varies by sector.",
        "long": "Debt-to-equity compares borrowed money to shareholders' equity. High leverage amplifies "
                "both gains and risk; utilities/REITs normally run higher, tech lower — so it's graded "
                "against the sector.",
        "example": "D/E 0.6 = 60¢ of debt per $1 of equity (conservative for most sectors).",
    },
    "operating_margin": {
        "plain": "Profit per sales dollar", "term": "operating margin",
        "short": "What fraction of revenue is left as operating profit. Higher = more efficient/pricing power.",
        "long": "Operating margin is operating profit divided by revenue — how much of each sales dollar "
                "survives after the costs of running the business. Wider margins signal pricing power "
                "and efficiency.",
        "example": "12% operating margin = 12¢ operating profit per $1 of sales.",
    },
    "next_earnings": {
        "plain": "Next earnings date", "term": "earnings date",
        "short": "When the company next reports quarterly results — a known volatility event.",
        "long": "The scheduled date of the next quarterly earnings report. Results often move the stock "
                "sharply, so the engine also avoids buying right before it (the earnings blackout).",
        "example": "Earns Jul 23 — expect bigger-than-usual moves around then.",
    },
    "earnings_surprise": {
        "plain": "Beat or missed", "term": "earnings surprise",
        "short": "Did last quarter's actual profit (EPS) come in above (beat) or below (miss) what analysts expected?",
        "long": "Each quarter a company reports actual earnings per share (EPS) against the analyst estimate. "
                "The surprise is how far off the estimate it landed: above = a 'beat' (🟢), below = a 'miss' "
                "(🔴), roughly on = in-line. A run of beats signals momentum; misses, trouble.",
        "example": "EPS $3.66 vs $3.50 expected → +4.7% beat 🟢.",
        "theory": "Stocks often drift in the direction of an earnings surprise for weeks after the report.",
    },
}


def label(key: str) -> str:
    """'Plain (Term)' for a key — or the key itself if unknown."""
    e = GLOSSARY.get(key)
    if not e:
        return key
    term = e.get("term")
    return f"{e['plain']} ({term})" if term and term != e["plain"] else e["plain"]


def short(key: str) -> str:
    return (GLOSSARY.get(key) or {}).get("short", "")


def has(key: str) -> bool:
    return key in GLOSSARY


def as_json() -> str:
    """Compact JSON of the registry for embedding in the page's client JS."""
    return json.dumps(GLOSSARY, ensure_ascii=False, separators=(",", ":"))


# ── tournament strategies ────────────────────────────────────────────────────
# Each strategy variant raced in the tournament gets a plain-language definition
# + a concrete worked example, surfaced as a "?" beside its leaderboard row. Keyed
# by the variant's exact label (from screener/tournament/variants.py); a test
# enforces that every live variant has an entry, so this never silently drifts.
def strategy_key(label_: str) -> str:
    """Stable glossary key for a tournament strategy label."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(label_).lower()).strip("_")
    return f"strat_{slug}"


_STRATEGIES: dict[str, dict] = {
    # controls (null-hypothesis benchmarks)
    "SPY buy-hold": {
        "plain": "Just buy the market",
        "short": "Buy the whole S&P 500 and hold it — the 'do nothing clever' benchmark every strategy must beat.",
        "example": "Put all $10k in an S&P 500 fund on day one and never trade again."},
    "Equal-weight universe": {
        "plain": "Buy a bit of everything",
        "short": "Hold a little of every stock in the list, in equal amounts — maximum spread, no stock-picking.",
        "example": "$10k split evenly across all ~220 stocks."},
    "Random 20 (seed)": {
        "plain": "Pick 20 at random",
        "short": "Hold 20 randomly chosen stocks — the luck benchmark. If the clever strategies can't beat random, they have no real skill.",
        "example": "Throw darts at the list, hold the 20 you hit."},
    # signal weighting
    "Regime-blended (default)": {
        "plain": "Smart blend (the live engine)",
        "short": "The live strategy: blends all 5 signals, shifting the weights with the market's mood (calm vs fearful).",
        "example": "In a calm market it leans on trend signals; in a fearful one, on risk signals."},
    "Equal 5 signals": {
        "plain": "All signals equal",
        "short": "Weight all 5 signals the same, whatever the market mood.",
        "example": "Each signal counts 20% toward a stock's score."},
    "Pure Sharpe": {
        "plain": "Reward-for-risk only",
        "short": "Rank stocks only by reward-for-risk (Sharpe); ignore the other four signals.",
        "example": "Pick the calmest high-return names, nothing else considered."},
    "Pure Monte-Carlo": {
        "plain": "Crash-risk signal only",
        "short": "Rank only by the 10,000-path crash-risk simulation; ignore the rest.",
        "example": "Buy whatever the simulation rates as least likely to crash."},
    "Pure ARIMA": {
        "plain": "Trend forecast only",
        "short": "Rank only by the trend-forecast signal; ignore risk and efficiency.",
        "example": "Buy the stocks ARIMA expects to rise the most."},
    "Pure Kalman": {
        "plain": "Smoothed-trend only",
        "short": "Rank only by the noise-smoothed trend signal.",
        "example": "Buy stocks whose underlying (de-noised) price is turning up."},
    "Pure GARCH": {
        "plain": "Calmest stocks only",
        "short": "Rank only by the volatility forecast — favour the calmest names.",
        "example": "Buy the lowest predicted-volatility stocks."},
    "Trend (ARIMA+Kalman)": {
        "plain": "Trend signals only",
        "short": "Blend just the two trend signals (ARIMA + Kalman); ignore the risk signals.",
        "example": "Lean into direction/momentum, ignore how risky a name is."},
    "Risk (Sharpe+MC+GARCH)": {
        "plain": "Risk signals only",
        "short": "Blend just the three risk/efficiency signals; ignore raw trend.",
        "example": "Favour safe, efficient names even if their trend is flat."},
    # concentration
    "Top-1 per sector": {
        "plain": "Best 1 per sector",
        "short": "Buy only the single best stock from each sector — concentrated.",
        "example": "11 sectors → 11 holdings, the top pick in each."},
    "Top-3 per sector": {
        "plain": "Best 3 per sector",
        "short": "Buy the top 3 from each sector — more spread than top-1.",
        "example": "11 sectors → up to 33 holdings."},
    "Top-5 per sector": {
        "plain": "Best 5 per sector",
        "short": "Buy the top 5 from each sector — the most diversified mix.",
        "example": "11 sectors → up to 55 holdings."},
    # sizing
    "Score-weighted sizing": {
        "plain": "Bet more on conviction",
        "short": "Put more money into higher-scoring picks instead of equal amounts.",
        "example": "A 0.80-score pick gets more dollars than a 0.60-score one."},
    # veto policy
    "Guards off": {
        "plain": "Safety filters off",
        "short": "Skip the risk vetoes — buy top scores even if too volatile or near earnings. A test of whether the guards help.",
        "example": "Ignore the safety filters and just buy the highest scores."},
    # diagnostics
    "Worst-ranked (inverse)": {
        "plain": "Buy the worst (sanity check)",
        "short": "Deliberately buy the LOWEST-ranked stocks. A control: if these don't lag the best, the ranking has no signal.",
        "example": "Buy the bottom-2 per sector instead of the top-2."},
    "High conviction (top-1, score)": {
        "plain": "All-in on the best",
        "short": "Only the single best pick per sector, sized by conviction — the most aggressive bet on the engine being right.",
        "example": "One holding per sector, biggest bets on the highest scores."},
    # the live candidate (added to the race in the re-validation)
    "CANDIDATE ARIMA+Sharpe": {
        "plain": "The live candidate",
        "short": "The current live weighting: ARIMA trend + Sharpe efficiency only, dropping the 3 signals that predicted backwards.",
        "example": "Score = 62% ARIMA + 38% Sharpe; the other 3 signals get 0%."},
}

for _label, _d in _STRATEGIES.items():
    GLOSSARY[strategy_key(_label)] = {
        "plain": _d["plain"], "term": _d["plain"],   # term==plain → no parenthetical
        "short": _d["short"], "long": _d.get("long", _d["short"]),
        "example": _d["example"],
    }


KEYS = frozenset(GLOSSARY)

__all__ = ["GLOSSARY", "KEYS", "label", "short", "has", "as_json", "strategy_key"]
