"""render/notes.py — PURE note builders (engine objects → Markdown strings).

No file IO, no DB, no network — every function takes plain dicts/lists and
returns a string. ``render.build`` does the IO and calls these. This keeps the
surface fully unit-testable without a vault or a database.
"""
from __future__ import annotations

from render.markdown import document, equity_chart, money, num, pct, table

# Dataview folder anchors (paths are relative to the vault root).
_POS_FROM = '"90 Tracker/Positions"'
_SCREENER_FROM = '"90 Tracker/Screener"'


def _dataview(query: str) -> str:
    return "```dataview\n" + query.strip() + "\n```"


# ── Regime ───────────────────────────────────────────────────────────────────

def regime_note(regime: dict, generated_at: str) -> str:
    label = str(regime.get("label", "unknown"))
    conf = float(regime.get("confidence", 0.0) or 0.0)
    probs = regime.get("probabilities") or {}
    weights = regime.get("blended_weights") or {}

    fm = {
        "title": "Regime",
        "type": "tracker-regime",
        "regime": label,
        "regime_confidence": round(conf, 4),
        "regime_stable": bool(regime.get("stable", False)),
        "as_of": generated_at,
    }
    prob_tbl = table(
        ["Regime", "Probability"],
        [[k, pct(v)] for k, v in sorted(probs.items(), key=lambda kv: -float(kv[1] or 0))],
    )
    weight_tbl = table(
        ["Signal", "Blended weight"],
        [[k, num(v, 2)] for k, v in weights.items()],
    )
    body = (
        f"# Market Regime — **{label.upper()}**\n\n"
        f"- Confidence: **{pct(conf)}**\n"
        f"- Stable: **{regime.get('stable', False)}**\n\n"
        f"## Regime probabilities\n\n{prob_tbl}\n\n"
        f"## Blended signal weights (this regime)\n\n{weight_tbl}\n"
    )
    return document(fm, body)


# ── Screener run ─────────────────────────────────────────────────────────────

def screener_run_note(results: dict) -> str:
    """Build a per-run screener note from a ``format_results`` dict."""
    regime = results.get("regime", {})
    summary = results.get("summary", {})
    generated = str(results.get("generated_at", ""))
    run_date = generated[:10]

    fm = {
        "title": f"Screener Run {run_date}",
        "type": "tracker-screener-run",
        "run_date": run_date,
        "generated_at": generated,
        "regime": regime.get("label"),
        "regime_confidence": round(float(regime.get("confidence", 0) or 0), 4),
        "total_screened": summary.get("total_screened"),
        "total_passed_veto": summary.get("total_passed_veto"),
        "veto_rate_pct": summary.get("veto_rate_pct"),
    }

    top = summary.get("top_overall", [])
    top_tbl = table(
        ["Rank", "Ticker", "Sector", "Score"],
        [[s.get("rank"), s.get("ticker"), s.get("sector"), num(s.get("composite_score"))]
         for s in top],
    )

    sector_blocks = []
    for sector, stocks in (results.get("sectors") or {}).items():
        if not stocks:
            sector_blocks.append(f"### {sector}\n\n_No stocks passed veto._")
            continue
        rows = []
        for s in stocks:
            flag = " ⚠️relaxed" if s.get("veto_relaxed") else ""
            rows.append([
                s.get("rank"),
                s.get("ticker"),
                num(s.get("composite_score")),
                "✓" if s.get("passed_veto") else "✗",
                (s.get("veto_reason") or "") + flag,
            ])
        sector_blocks.append(
            f"### {sector}\n\n"
            + table(["Rank", "Ticker", "Score", "Veto", "Notes"], rows)
        )

    body = (
        f"# Screener Run — {run_date}\n\n"
        f"Regime: **{str(regime.get('label', 'unknown')).upper()}** "
        f"({pct(float(regime.get('confidence', 0) or 0))} confidence) · "
        f"screened **{summary.get('total_screened', 0)}**, "
        f"passed veto **{summary.get('total_passed_veto', 0)}** "
        f"(veto rate {summary.get('veto_rate_pct', 0)}%)\n\n"
        f"## Top picks overall\n\n{top_tbl}\n\n"
        f"## Per-sector top {len(top) and ''}candidates\n\n"
        + "\n\n".join(sector_blocks)
        + "\n"
    )
    return document(fm, body)


# ── Paper position ───────────────────────────────────────────────────────────

def position_note(pos: dict, trades: list[dict] | None = None) -> str:
    """One open paper position. Frontmatter is the Dataview source of truth."""
    shares = float(pos.get("shares", 0) or 0)
    total_cost = float(pos.get("total_cost", 0) or 0)
    price = pos.get("current_price")
    market_value = shares * float(price) if price is not None else None
    unreal = (market_value - total_cost) if market_value is not None else None
    unreal_pct = (unreal / total_cost) if (unreal is not None and total_cost) else None

    ticker = str(pos.get("ticker", "?"))
    fm = {
        "title": f"{ticker} (paper)",
        "type": "tracker-position",
        "ticker": ticker,
        "sector": pos.get("sector"),
        "status": pos.get("status", "ACTIVE"),
        "shares": round(shares, 4),
        "cost_basis": round(float(pos.get("cost_basis", 0) or 0), 4),
        "total_cost": round(total_cost, 2),
        "current_price": None if price is None else round(float(price), 4),
        "market_value": None if market_value is None else round(market_value, 2),
        "unrealized_pnl": None if unreal is None else round(unreal, 2),
        "unrealized_pct": None if unreal_pct is None else round(unreal_pct, 4),
        "entry_date": pos.get("entry_date"),
        "entry_score": pos.get("entry_score"),
        "last_score": pos.get("last_score"),
        "stop_loss_price": pos.get("stop_loss_price"),
        "regime_at_entry": pos.get("regime_at_entry"),
    }
    body = (
        f"# {ticker} — paper position\n\n"
        f"- Sector: **{pos.get('sector', '—')}** · Status: **{pos.get('status', 'ACTIVE')}**\n"
        f"- Shares: **{num(shares, 4)}** @ cost **{money(pos.get('cost_basis'))}** "
        f"(total {money(total_cost)})\n"
        f"- Current: **{money(price)}** · Market value: **{money(market_value)}**\n"
        f"- Unrealized P&L: **{money(unreal)}** ({pct(unreal_pct)})\n"
        f"- Entry: {pos.get('entry_date', '—')} @ score {num(pos.get('entry_score'), 3)} "
        f"(regime {pos.get('regime_at_entry', '—')}) · "
        f"last score {num(pos.get('last_score'), 3)}\n"
        f"- Stop-loss: **{money(pos.get('stop_loss_price'))}**\n"
    )
    # U21: per-fill trade log as Dataview inline fields (transaction-level history
    # in one note). Renders nothing until the paper cycle produces fills.
    if trades:
        lines = []
        for t in sorted(trades, key=lambda x: str(x.get("executed_at", ""))):
            d = str(t.get("executed_at", ""))[:10]
            lines.append(
                f"- [date:: {d}] [action:: {t.get('action', '?')}] "
                f"[shares:: {num(t.get('shares'), 4)}] "
                f"[price:: {num(t.get('price'), 2)}] "
                f"[value:: {num(t.get('total_value'), 2)}]"
            )
        body += "\n## Trade Log\n\n" + "\n".join(lines) + "\n"
    return document(fm, body)


# ── Daily journal ────────────────────────────────────────────────────────────

def journal_note(date: str, trades: list[dict], events: list[dict] | None = None) -> str:
    fm = {
        "title": f"Journal {date}",
        "type": "tracker-journal",
        "journal_date": date,
        "n_trades": len(trades),
    }
    trade_tbl = table(
        ["Time", "Action", "Ticker", "Shares", "Price", "Value", "Reason"],
        [[
            str(t.get("executed_at", ""))[11:19] or str(t.get("executed_at", "")),
            t.get("action"),
            t.get("ticker"),
            num(t.get("shares"), 4),
            money(t.get("price")),
            money(t.get("total_value")),
            t.get("trigger_reason") or "",
        ] for t in trades],
    )
    body = f"# Paper-trading journal — {date}\n\n## Fills\n\n{trade_tbl}\n"
    if events:
        ev_tbl = table(
            ["Time", "Event", "Description"],
            [[str(e.get("event_time", ""))[11:19], e.get("event_type"), e.get("description")]
             for e in events],
        )
        body += f"\n## System events\n\n{ev_tbl}\n"
    return document(fm, body)


# ── Performance ──────────────────────────────────────────────────────────────

def performance_note(snapshots: list[dict]) -> str:
    latest = snapshots[-1] if snapshots else {}
    fm = {
        "title": "Performance",
        "type": "tracker-performance",
        "as_of": latest.get("snapshot_date"),
        "total_value": latest.get("total_value"),
        "unrealized_pnl": latest.get("unrealized_pnl"),
        "realized_pnl_ytd": latest.get("realized_pnl_ytd"),
        "drawdown_from_peak": latest.get("drawdown_from_peak"),
        "n_snapshots": len(snapshots),
    }
    curve = table(
        ["Date", "Total value", "Cash", "Invested", "Unrealized", "Realized YTD",
         "Positions", "Regime", "Drawdown"],
        [[
            s.get("snapshot_date"),
            money(s.get("total_value")),
            money(s.get("cash")),
            money(s.get("invested_value")),
            money(s.get("unrealized_pnl")),
            money(s.get("realized_pnl_ytd")),
            s.get("n_positions"),
            s.get("regime"),
            pct(s.get("drawdown_from_peak")),
        ] for s in snapshots[-60:]],  # last 60 snapshots; older rows stay in the DB
    )
    note = ""
    if len(snapshots) > 60:
        note = f"\n_Showing the most recent 60 of {len(snapshots)} snapshots._\n"
    body = (
        "# Paper portfolio performance\n\n"
        f"- Total value: **{money(latest.get('total_value'))}**\n"
        f"- Unrealized P&L: **{money(latest.get('unrealized_pnl'))}**\n"
        f"- Realized P&L (YTD): **{money(latest.get('realized_pnl_ytd'))}**\n"
        f"- Drawdown from peak: **{pct(latest.get('drawdown_from_peak'))}**\n\n"
        f"## Equity curve\n\n{curve}\n{note}"
    )
    return document(fm, body)


# ── Dashboard ────────────────────────────────────────────────────────────────

def dashboard_note(regime: dict, latest_snapshot: dict, top_picks: list[dict],
                   generated_at: str, last_move: str | None = None) -> str:
    fm = {
        "title": "Dashboard",
        "type": "tracker-dashboard",
        "regime": regime.get("label"),
        "regime_confidence": round(float(regime.get("confidence", 0) or 0), 4),
        "total_value": latest_snapshot.get("total_value"),
        "unrealized_pnl": latest_snapshot.get("unrealized_pnl"),
        "as_of": generated_at,
    }
    picks_tbl = table(
        ["Rank", "Ticker", "Sector", "Score"],
        [[s.get("rank"), s.get("ticker"), s.get("sector"), num(s.get("composite_score"))]
         for s in top_picks],
    )
    positions_view = _dataview(
        "TABLE sector, status, shares, current_price, market_value, "
        "unrealized_pnl, unrealized_pct\n"
        f"FROM {_POS_FROM}\n"
        "WHERE type = \"tracker-position\"\n"
        "SORT unrealized_pnl DESC"
    )
    runs_view = _dataview(
        "TABLE regime, total_passed_veto, veto_rate_pct\n"
        f"FROM {_SCREENER_FROM}\n"
        "WHERE type = \"tracker-screener-run\"\n"
        "SORT run_date DESC\n"
        "LIMIT 8"
    )
    body = (
        "# Quant Tracker — Dashboard\n\n"
        f"**Regime:** {str(regime.get('label', 'unknown')).upper()} "
        f"({pct(float(regime.get('confidence', 0) or 0))}) · "
        f"**Paper value:** {money(latest_snapshot.get('total_value'))} · "
        f"**Unrealized:** {money(latest_snapshot.get('unrealized_pnl'))}\n\n"
        + (f"🧠 **Last move:** {last_move}\n\n" if last_move else "")
        + "See also: [[Decisions]] · [[Regime]] · [[Performance]]\n\n"
        f"## Latest top picks\n\n{picks_tbl}\n\n"
        f"## Open paper positions\n\n{positions_view}\n\n"
        f"## Recent screener runs\n\n{runs_view}\n"
    )
    return document(fm, body)


# ── Scorecard (is it working?) ───────────────────────────────────────────────

_MIN_PICKS_FOR_VERDICT = 5


def _scorecard_verdict(horizons: dict) -> tuple[str, str]:
    """Return (one-line verdict, headline_key). Prefers the longest elapsed horizon
    with enough graded picks; falls back to 'too early'."""
    for key in ("84d", "28d", "7d"):  # longest (most reliable) first
        s = horizons.get(key, {})
        if s.get("n", 0) >= _MIN_PICKS_FOR_VERDICT:
            days = key[:-1]
            beat = s["hit_rate"]
            alpha = s["avg_alpha"]
            if alpha > 0:
                v = (f"✅ **The picks are beating the market.** Over {days} days, "
                     f"**{pct(beat,0)}** of {s['n']} picks beat SPY, with an average "
                     f"edge of **{pct(alpha)}** (picks {pct(s['avg_return'])} vs market).")
            else:
                v = (f"⚠️ **The picks are lagging the market so far.** Over {days} days, "
                     f"only **{pct(beat,0)}** of {s['n']} picks beat SPY; average edge "
                     f"**{pct(alpha)}**. Early and noisy — keep watching.")
            return v, key
    return (
        "⏳ **Too early to tell.** The picks need a few weeks of price history before "
        "they can be graded against the market. Check back after ~1–4 weekly runs. "
        "(For an immediate read of whether the engine has skill, see **[[Backtest]]**.)",
        "",
    )


def scorecard_note(data: dict, snapshots: list[dict] | None = None) -> str:
    horizons = data.get("horizons", {})
    verdict, _ = _scorecard_verdict(horizons)

    def _row(label, s):
        cov = s.get("coverage")
        cov_str = "—" if cov is None else f"{pct(cov,0)} ({s.get('n',0)}/{s.get('attempted',0)})"
        return [label, s.get("n", 0), pct(s.get("up_rate")), pct(s.get("hit_rate")),
                pct(s.get("avg_return")), pct(s.get("avg_alpha")), cov_str]

    order = [("7 days", "7d"), ("28 days", "28d"), ("84 days", "84d"),
             ("to date", "to_date")]
    metrics = table(
        ["Horizon", "Picks", "% went up", "% beat SPY", "Avg return",
         "Avg edge vs SPY", "Coverage"],
        [_row(lbl, horizons.get(k, {})) for lbl, k in order],
    )

    paper = data.get("paper", {})
    if paper.get("status") == "ok":
        paper_line = (f"Paper portfolio **{pct(paper['port_return'])}** vs SPY "
                      f"**{pct(paper['spy_return'])}** → edge **{pct(paper['excess'])}** "
                      f"over {paper.get('n_days')} days.")
    elif paper.get("status") == "cash_only":
        paper_line = "_Holding cash only so far — comparison starts after the first monthly buy._"
    else:
        paper_line = "_No paper history yet — starts after the first monthly buy._"

    best = next((horizons.get(k, {}) for k in ("28d", "7d", "84d")
                 if horizons.get(k, {}).get("n", 0) >= _MIN_PICKS_FOR_VERDICT), {})
    fm = {
        "title": "Scorecard",
        "type": "tracker-scorecard",
        "as_of": data.get("as_of"),
        "runs_graded": data.get("n_graded_runs", 0),
        "headline_hit_rate": round(best["hit_rate"], 4) if best.get("hit_rate") is not None else None,
        "headline_avg_alpha": round(best["avg_alpha"], 4) if best.get("avg_alpha") is not None else None,
    }
    body = (
        "# Are the predictions working?\n\n"
        f"{verdict}\n\n"
        "_\"Beat SPY\" means the pick rose more than the overall market over that window. "
        "\"Edge\" (alpha) is the pick's return minus the market's._\n\n"
        f"## All passed picks, by horizon\n\n{metrics}\n\n"
        f"_Graded {data.get('n_graded_runs', 0)} of {data.get('n_runs', 0)} recorded runs._\n\n"
        f"## Paper portfolio vs the market\n\n{paper_line}\n\n"
        f"{equity_chart(snapshots or [])}\n"
    )
    return document(fm, body)


# ── Diversification clusters (U15) ───────────────────────────────────────────

def clusters_note(data: dict) -> str:
    clusters = data.get("clusters", [])
    k = data.get("k", 0)
    sil = data.get("silhouette")
    fm = {
        "title": "Clusters",
        "type": "tracker-clusters",
        "as_of": data.get("as_of"),
        "k": k,
        "silhouette": None if sil is None else round(sil, 3),
        "n_tickers": data.get("n_tickers", 0),
    }
    if not clusters:
        body = (
            "# Diversification clusters\n\n"
            "_Not enough cached price history yet — run `track seed` first, then "
            "`track clusters`._\n"
        )
        return document(fm, body)

    summary = table(
        ["Cluster", "Members", "Avg volatility", "Avg return", "Risk/return profile"],
        [[f"#{c['id']}", c["n"], pct(c["mean_vol"]), pct(c["mean_return"]), c["label"]]
         for c in clusters],
    )
    blocks = []
    for c in clusters:
        members = ", ".join(c["members"])
        blocks.append(f"### Cluster #{c['id']} — {c['label']} ({c['n']})\n\n{members}")

    sil_str = "n/a" if sil is None else f"{sil:.3f}"
    body = (
        "# Diversification clusters\n\n"
        "_Groups the universe by how **risky vs rewarding** each stock has actually "
        "been (annualized volatility + return over the lookback window). Spreading "
        "picks across clusters avoids piling into one risk profile — a different lens "
        "than the screener's per-sector diversification._\n\n"
        f"**{k} clusters** over **{data.get('n_tickers', 0)}** stocks "
        f"(silhouette {sil_str}; {data.get('n_skipped', 0)} skipped for thin history).\n\n"
        f"## Cluster summary\n\n{summary}\n\n"
        f"## Members\n\n" + "\n\n".join(blocks) + "\n"
    )
    return document(fm, body)


# ── Agent decision log (autonomous, first-person) ────────────────────────────

# trade_history trigger_reason → plain language
_TRADE_VERB = {
    "NEW_BUY": "opened", "REBALANCE_BUY": "added to",
    "STOP_LOSS": "stopped out of", "SIGNAL_EXIT": "exited (signal faded)",
    "REBALANCE_SELL": "trimmed", "UNKNOWN": "traded",
}


def _decision_text(d: dict) -> str:
    """Compose one first-person decision entry from a typed decision dict."""
    when = str(d.get("when", ""))[:10]
    kind = d.get("kind")
    if kind == "screen":
        top = ", ".join(s.get("ticker", "?") for s in (d.get("top") or [])[:5])
        return (
            f"🔭 **{when}** — I screened the market. Regime reads "
            f"**{str(d.get('regime', 'unknown')).upper()}** "
            f"({pct(d.get('regime_conf'))}). Of **{d.get('total_screened', 0)}** stocks, "
            f"**{d.get('total_passed', 0)}** cleared my risk veto "
            f"(veto rate {d.get('veto_rate', 0)}%). "
            + (f"Strongest conviction: **{top}**." if top else "No clear standouts.")
        )
    if kind == "trades":
        opened = [t for t in d.get("trades", []) if str(t.get("action")) == "BUY"]
        closed = [t for t in d.get("trades", []) if str(t.get("action")) == "SELL"]
        parts = []
        if opened:
            parts.append("opened/added: " + ", ".join(
                f"{_TRADE_VERB.get(t.get('trigger_reason'), 'bought')} **{t.get('ticker')}**"
                for t in opened[:8]))
        if closed:
            parts.append("sold: " + ", ".join(
                f"{_TRADE_VERB.get(t.get('trigger_reason'), 'sold')} **{t.get('ticker')}**"
                for t in closed[:8]))
        return f"💰 **{when}** — I traded. " + (" · ".join(parts) if parts else "No fills.")
    if kind == "daily":
        det = d.get("details", {}) or {}
        return (
            f"🩺 **{when}** — Daily check. "
            f"**{det.get('stop_hits', 0)}** stop-loss hit(s), "
            f"**{det.get('decay_alerts', 0)}** signal(s) faded"
            + (f", value **{money(d.get('total_value'))}**" if d.get("total_value") else "")
            + "."
        )
    return f"• **{when}** — {d.get('description', d.get('kind', 'event'))}"


def agent_log_note(decisions: list[dict]) -> str:
    fm = {"title": "Decisions", "type": "tracker-decisions",
          "as_of": decisions[0].get("when") if decisions else None,
          "n_entries": len(decisions)}
    if not decisions:
        body = ("# 🧠 What I'm doing\n\n"
                "_No decisions logged yet. Once I run a screen or a buy cycle, "
                "I'll narrate each move here._\n")
        return document(fm, body)
    entries = "\n\n".join(_decision_text(d) for d in decisions)
    body = (
        "# 🧠 What I'm doing — my decision log\n\n"
        "_I run on my own and explain every move here, newest first. This is "
        "pretend money — I'm showing you my reasoning, not giving advice._\n\n"
        f"{entries}\n"
    )
    return document(fm, body)


# ── Signal Lab ───────────────────────────────────────────────────────────────

def signal_lab_note(data: dict) -> str:
    sigs = data.get("signals", {}) or {}
    fm = {"title": "SignalLab", "type": "tracker-signal-lab",
          "as_of": data.get("as_of"), "n_dates": data.get("n_dates")}
    if not sigs:
        return document(fm, "# 🔬 Signal Lab\n\n_No analysis yet — `track signal-lab` "
                        "(needs a tournament panel: `track tournament` first)._\n")

    rows = []
    for s, d in sorted(sigs.items(), key=lambda kv: -(kv[1].get("ic") or -9)):
        ic = d.get("ic")
        sig_flag = d.get("ic_significant")
        sig_mark = "—" if sig_flag is None else ("✓" if sig_flag else "✗")
        rows.append([
            s, pct(ic) if ic is not None else "—",
            num(d.get("ic_ir"), 1) if d.get("ic_ir") is not None else "—",
            sig_mark,
            pct(d.get("quintile_spread")) if d.get("quintile_spread") is not None else "—",
            d.get("verdict", ""),
        ])
    _thr = data.get("ir_threshold")
    ic_tbl = table(["Signal", "IC", "Info ratio",
                    "Sig.", "Quintile spread", "Verdict"], rows)
    if _thr is not None:
        ic_tbl += (f"\n\n_Sig. = info ratio clears the Bonferroni-corrected "
                   f"|IR|≥{_thr:.2f} bar (α=0.05 across {len(sigs)} signals). A ✗ "
                   f"means the IC is **not** significant after the multiple-testing "
                   f"correction — suggestive, not proven (U28)._")

    cand = data.get("candidate_weights", {}) or {}
    kept = ", ".join(f"{k} {pct(v)}" for k, v in cand.items() if v and v > 0.001)
    val = data.get("validation", {}) or {}
    val_tbl = ""
    if val:
        val_tbl = table(["Out-of-sample", "Return"], [
            ["Candidate (kept signals)", pct(val.get("candidate_oos"))],
            ["Current engine default", pct(val.get("default_oos"))],
            ["SPY buy-hold", pct(val.get("spy_oos"))],
        ])

    best = max(sigs.items(), key=lambda kv: (kv[1].get("ic") or -9))
    worst = min(sigs.items(), key=lambda kv: (kv[1].get("ic") if kv[1].get("ic") is not None else 9))
    beats = val.get("candidate_oos") is not None and val.get("spy_oos") is not None \
        and val["candidate_oos"] > val["spy_oos"]

    body = (
        "# 🔬 Signal Lab — does each signal actually predict?\n\n"
        f"> [!{'success' if beats else 'warning'}] Finding\n"
        f"> **{best[0]}** is the only signal with real edge (IC {pct(best[1].get('ic'))}); "
        f"**{worst[0]}** predicts *backwards* (IC {pct(worst[1].get('ic'))}). Keeping only the "
        f"positive-IC signals ({kept or 'none'}) "
        + (f"**beat buy-and-hold SPY out-of-sample** ({pct(val.get('candidate_oos'))} vs "
           f"{pct(val.get('spy_oos'))}, weights derived in-sample)."
           if beats else "did not clearly beat the controls out-of-sample.") + "\n\n"
        "_Information Coefficient (IC) = cross-sectional rank correlation between a signal and "
        "the next quarter's return, per rebalance date then averaged. Positive = predictive; "
        "negative = predicts backwards. Quintile spread = top-fifth minus bottom-fifth forward "
        f"return. Over {data.get('n_dates','?')} rebalances._\n\n"
        f"## Per-signal predictive power\n\n{ic_tbl}\n\n"
        + (f"## Candidate re-weighting (drop the duds)\n\n"
           f"Keep **{kept}**, drop the rest. **Out-of-sample** (weights derived from in-sample "
           f"dates only, judged on held-out dates):\n\n{val_tbl}\n\n" if val_tbl else "")
        + ("_**Live now:** the candidate weighting is the active default "
           "(`WEIGHT_MATRIX_MODE=candidate`) — running it in paper IS the forward validation; "
           "revert to `current` if the post-July evidence doesn't hold._\n\n"
           if data.get("mode") == "candidate" else
           "_The live weights are unchanged; the candidate is opt-in via `WEIGHT_MATRIX_MODE`._\n\n")
        + "_Honest caveats: one ~3-year price path, few out-of-sample quarters — the IC "
        "diagnosis is robust (consistent across windows) but the OOS edge needs forward "
        "paper-validation before trusting it with conviction. Re-weighting only redistributes "
        "among 5 signals — if they're weak, the real fix is new signals (momentum / quality / "
        "value)._\n"
    )
    return document(fm, body)


# ── Strategy tournament ──────────────────────────────────────────────────────

def tournament_note(data: dict) -> str:
    ranked = data.get("ranked", []) or []
    attr = data.get("attribution", {}) or {}
    fm = {"title": "Tournament", "type": "tracker-tournament",
          "as_of": data.get("as_of"), "n_variants": len(ranked),
          "winner": attr.get("winner")}
    if not ranked:
        return document(fm, "# 🏆 Strategy tournament\n\n"
                        "_No tournament run yet — `track tournament`._\n")

    def _g(m, k):
        v = (m or {}).get(k)
        return v
    rows = []
    for r in ranked:
        f, o = r.get("full", {}), r.get("out_sample", {})
        tag = " ·control" if r.get("group") == "control" else ""
        rows.append([
            r.get("rank"), r.get("label") + tag,
            pct(_g(f, "total_return")), pct(_g(f, "cagr")),
            num(_g(f, "sharpe"), 2) if _g(f, "sharpe") is not None else "—",
            pct(_g(f, "max_drawdown")), pct(_g(f, "excess")),
            pct(_g(o, "total_return")),
        ])
    board = table(["#", "Strategy", "Total", "CAGR", "Sharpe", "MaxDD",
                   "vs SPY", "OOS total"], rows)

    # attribution blocks
    ic = attr.get("signal_ic", {}) or {}
    ic_tbl = table(["Signal", "IC (predicts return?)"],
                   [[k, num(v, 3) if v is not None else "—"] for k, v in ic.items()])
    tilt = attr.get("sector_tilt", []) or []
    tilt_tbl = table(["Sector", "Winner weight"],
                     [[t.get("sector"), pct(t.get("pct"))] for t in tilt]) if tilt else ""
    reg = attr.get("regime_conditional", []) or []
    reg_tbl = table(["Regime", "Winner avg/rebalance", "Rebalances"],
                    [[x.get("regime"), pct(x.get("avg_return")), x.get("n")] for x in reg]) if reg else ""
    direction = attr.get("direction", {}) or {}
    dir_rows = []
    for lbl, k in [("Up quarters", "up"), ("Down quarters", "down")]:
        b = direction.get(k)
        if b and b.get("n"):
            dir_rows.append([lbl, b["n"], pct(b["engine"]), pct(b["spy"]), pct(b["excess"])])
    dir_tbl = table(["Market", "Quarters", "Engine /q", "SPY /q", "Excess /q"],
                    dir_rows) if dir_rows else ""

    body = (
        "# 🏆 Strategy tournament\n\n"
        f"> [!{'success' if attr.get('oos_holds') and attr.get('beat_spy',0)>0 else 'warning'}] Verdict\n"
        f"> {attr.get('verdict','')}\n\n"
        + ("> [!danger] Engine ranking health\n"
           "> ⚠ The engine's own composite ranking showed **no edge** this window — the "
           "*worst*-ranked picks beat the best-ranked, and the signal ICs are ~0. Either "
           "the signal weighting needs re-examination, or this strong-index window simply "
           "favored buy-and-hold (the index beat most active stock-picking). Investigate, "
           "don't deploy.\n\n"
           if attr.get("ranking_has_signal") is False else "")
        + (f"> [!tip] Strategy character\n> The default strategy is **{attr.get('character')}**\n\n"
           if attr.get("character") else "")
        + "_~20 strategy variants raced over real historical prices. A **hypothesis-"
        "generator**, not proof: the winner is picked in-sample and re-checked "
        "out-of-sample, and three 'dumb' controls (SPY, whole universe, random-20) "
        "are the honesty bar._\n\n"
        f"- Beat SPY by **{pct(attr.get('beat_spy'))}** · beat random by "
        f"**{pct(attr.get('beat_random'))}** · out-of-sample rank "
        f"**{attr.get('oos_rank','—')}/{len(ranked)}** · field spread "
        f"{pct(attr.get('field_spread'))}\n\n"
        f"## Leaderboard ({data.get('n_segments','?')} rebalances, "
        f"{data.get('n_in_sample','?')} in-sample"
        + (f", net of {data['cost_bps']:.0f}bps round-trip cost"
           if data.get("cost_bps") else "")
        + f")\n\n{board}\n\n"
        f"## Why the winner won\n\n"
        f"**Which signals actually predicted returns** (Spearman IC over the window):\n\n{ic_tbl}\n\n"
        + (f"## The default strategy vs market direction\n\n"
           f"_The most useful cut: how the engine's own (regime-blended) strategy did "
           f"vs SPY in up vs down quarters._\n\n{dir_tbl}\n\n" if dir_tbl else "")
        + (f"**Winner's sector tilt:**\n\n{tilt_tbl}\n\n" if tilt_tbl else "")
        + (f"**Winner by regime:**\n\n{reg_tbl}\n\n" if reg_tbl else "")
        + (f"_Turnover: {pct(attr.get('turnover'))} of holdings change each rebalance._\n\n"
           if attr.get("turnover") is not None else "")
        + "_In-sample-aware: with ~20 variants on one price path, the leader is partly "
        "luck (data-snooping). The controls + out-of-sample split + this verdict are "
        "the guards. Forward-test a credible winner in paper before trusting it._\n"
    )
    return document(fm, body)


# ── AI co-pilot (Claude reasoning overlay) ───────────────────────────────────

def copilot_note(review: dict, context: dict | None = None) -> str:
    context = context or {}
    available = bool(review.get("available"))
    fm = {
        "title": "Copilot",
        "type": "tracker-copilot",
        "as_of": context.get("as_of"),
        "available": available,
        "model": review.get("model"),
    }
    if not available:
        reason = review.get("reason", "the co-pilot is off")
        body = (
            "# 🤖 AI co-pilot — off\n\n"
            f"_The co-pilot isn't running: **{reason}**._\n\n"
            "It's an **optional** layer. To turn it on:\n\n"
            "1. `./.venv/bin/python -m pip install -r requirements-copilot.txt`\n"
            "2. set an `ANTHROPIC_API_KEY` in your environment\n"
            "3. set `COPILOT_ENABLED = True` in `screener/config.py` (or just run `track copilot`)\n\n"
            "When on, Claude reads each cycle and writes its take here. It is "
            "**advisory only** — it never places trades; the quant engine + 8 "
            "risk guards make every actual (paper) trade.\n"
        )
        return document(fm, body)
    regime = context.get("regime") or {}
    body = (
        "# 🤖 My take\n\n"
        "_Claude, reading the latest cycle as your portfolio-manager co-pilot. "
        "**Advisory only** — I don't place trades; the quant engine + 8 risk "
        "guards do. Paper money, research, not financial advice._\n\n"
        + (f"**Regime:** {regime.get('label', '?')} · "
           f"**model:** {review.get('model', '?')}\n\n" if regime else "")
        + review.get("commentary", "").strip() + "\n"
    )
    return document(fm, body)


# ── News sentiment (U11) ─────────────────────────────────────────────────────

_HEALTH_EMOJI = {"STRONG": "🟢", "FAIR": "🟡", "WEAK": "🔴", "UNAVAILABLE": "⚪"}


def company_health_note(data: dict) -> str:
    """Per-company health snapshot: quality grade + valuation + next earnings.
    `data['rows']` are pre-joined dicts (health + fundamentals + earnings)."""
    rows = data.get("rows", []) or []
    fm = {"title": "CompanyHealth", "type": "tracker-company-health",
          "as_of": data.get("as_of"), "n_companies": len(rows)}
    if not rows:
        return document(fm, "# 🩺 Company health\n\n_No health data yet — "
                        "`track health` (scores your holdings' fundamentals)._\n")

    def _last_earn(r):
        e = (r.get("earnings") or [{}])[0]
        s = e.get("surprise_pct")
        if s is None:
            return "—"
        return f"{'+' if s >= 0 else ''}{s:.1f}% {e.get('verdict', '')}"

    def _row(r):
        lbl = r.get("health_label") or "UNAVAILABLE"
        return [
            f"{_HEALTH_EMOJI.get(lbl, '⚪')} **{r.get('ticker')}**"
            + (f" · {r.get('name')}" if r.get("name") else ""),
            lbl + (f" ({r['floors_passed']}/{r['floors_total']})"
                   if r.get("floors_total") else ""),
            pct(r.get("roe")) if r.get("roe") is not None else "—",
            pct(r.get("operating_margin")) if r.get("operating_margin") is not None else "—",
            num(r.get("debt_to_equity"), 2) if r.get("debt_to_equity") is not None else "—",
            num(r.get("pe"), 1) if r.get("pe") is not None else "—",
            _last_earn(r),
            r.get("next_earnings") or "—",
        ]

    tbl = table(["Company", "Health", "ROE", "Op margin", "Debt/Eq", "P/E",
                 "Last earnings", "Next earnings"], [_row(r) for r in rows])
    # Per-company recent-earnings breakdown (last few quarters).
    detail = ""
    with_hist = [r for r in rows if r.get("earnings")]
    if with_hist:
        blocks = []
        for r in with_hist:
            lines = [f"- **{q.get('report_date')}** — actual "
                     f"{num(q.get('eps_actual'), 2)} vs est {num(q.get('eps_estimate'), 2)} · "
                     f"{('+' if (q.get('surprise_pct') or 0) >= 0 else '')}"
                     f"{num(q.get('surprise_pct'), 1)}% {q.get('verdict', '')}"
                     for q in (r.get("earnings") or [])[:4]]
            blocks.append(f"**{r.get('ticker')}**\n" + "\n".join(lines))
        detail = "\n\n## Recent earnings (EPS actual vs estimate)\n\n" + "\n\n".join(blocks) + "\n"
    strong = sum(1 for r in rows if r.get("health_label") == "STRONG")
    weak = sum(1 for r in rows if r.get("health_label") == "WEAK")
    body = (
        "# 🩺 Company health — is each holding financially sound?\n\n"
        f"> [!{'success' if weak == 0 else 'warning'}] At a glance\n"
        f"> Of {len(rows)} companies, **{strong} strong** · **{weak} weak** on the "
        f"quality floors (ROE, margins, leverage, liquidity for their sector).\n\n"
        "_Health grades each company's profitability + balance-sheet metrics against "
        "the minimum floors for its sector (🟢 STRONG ≥¾ passed · 🟡 FAIR · 🔴 WEAK · "
        "⚪ data n/a). Valuation is the trailing P/E; **Last earnings** is the most "
        "recent quarter's beat/miss vs the analyst estimate; **Next earnings** is the "
        "upcoming report date. Free-feed snapshots — a monitoring aid, not advice._\n\n"
        f"{tbl}\n{detail}"
    )
    return document(fm, body)


def sentiment_note(data: dict) -> str:
    rows = data.get("rows", []) or []
    counts = {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0, "UNAVAILABLE": 0}
    for r in rows:
        counts[r.get("label", "UNAVAILABLE")] = counts.get(r.get("label", "UNAVAILABLE"), 0) + 1
    fm = {
        "title": "Sentiment",
        "type": "tracker-sentiment",
        "as_of": data.get("as_of"),
        "n_tickers": len(rows),
        "veto_enabled": bool(data.get("veto_enabled")),
    }
    if not rows:
        return document(fm, "# News sentiment\n\n"
                        "_No sentiment cached yet — run `track sentiment`._\n")

    scored = [r for r in rows if r.get("label") != "UNAVAILABLE"]
    most_neg = sorted(scored, key=lambda r: (r.get("sentiment_score") if r.get("sentiment_score")
                                             is not None else 0))[:15]
    tbl = table(
        ["Ticker", "Score", "Label", "Headlines", "Confidence"],
        [[r.get("ticker"), num(r.get("sentiment_score"), 3), r.get("label"),
          r.get("n_headlines"), num(r.get("confidence"), 2)] for r in most_neg],
    )
    veto_line = (f"**Sentiment veto:** {'ON' if data.get('veto_enabled') else 'OFF (opt-in)'} "
                 f"· threshold ≤ {data.get('threshold')}")
    body = (
        "# News sentiment\n\n"
        "_FinBERT over recent yfinance headlines. An informational overlay; the "
        "sentiment veto is opt-in and default OFF. News is noisy — treat as a "
        "'pause & review' flag, not a hard rule._\n\n"
        f"{veto_line}\n\n"
        f"- Positive: **{counts['POSITIVE']}** · Neutral: **{counts['NEUTRAL']}** · "
        f"Negative: **{counts['NEGATIVE']}** · Unavailable: **{counts['UNAVAILABLE']}**\n\n"
        f"## Most negative ({len(most_neg)})\n\n{tbl}\n"
    )
    return document(fm, body)


# ── Backtest (retrospective skill) ───────────────────────────────────────────

def backtest_note(data: dict) -> str:
    wf = data.get("walk_forward") or {}
    ic = data.get("ic") or {}
    rg = data.get("regime") or {}

    # 1) Walk-forward: do top picks beat the average stock?
    wf_n = wf.get("n_windows", 0)
    if wf_n:
        lift, win = wf.get("mean_lift", 0.0), wf.get("win_rate", 0.0)
        wf_verdict = (
            f"{'✅' if lift > 0 else '⚠️'} Across **{wf_n}** historical test windows, the "
            f"top picks beat the average stock in **{pct(win,0)}** of them, by an average of "
            f"**{pct(lift)}** per window."
        )
    else:
        wf_verdict = "_Walk-forward did not produce results (need more price history)._"

    # 2) Information Coefficient: do the signals predict returns?
    agg = ic.get("aggregate", {})
    ic_rows = [[k, num(v, 3), "predictive" if (v or 0) > 0.02 else
               ("weak/none" if (v or 0) >= -0.02 else "backwards")]
              for k, v in agg.items()]
    ic_tbl = table(["Signal", "IC (skill)", "Read"], ic_rows) if ic_rows else "_n/a_"
    good = [k for k, v in agg.items() if (v or 0) > 0.02]
    ic_verdict = (
        f"Signals that actually predict returns (positive skill): "
        f"**{', '.join(good) if good else 'none clearly'}**. "
        f"IC is a correlation between a signal's ranking and the next-{ic.get('horizon_days','?')}-day "
        f"return — above ~0.02 is a usable edge."
    )

    # 3) Regime: is the bull/bear call real?
    mono = rg.get("monotone_bull_gt_bear")
    rg_rows = [[r.get("regime"), pct(r.get("mean_forward_logret")), r.get("n_observations")]
               for r in rg.get("regimes", [])]
    rg_tbl = table(["Regime", "Avg forward return", "Days observed"], rg_rows) if rg_rows else "_n/a_"
    rg_verdict = (
        "✅ The regime call is real — bull periods averaged higher forward returns than bear periods."
        if mono else
        "⚠️ The regime ordering wasn't clean in this sample (bull not clearly > bear)."
    )

    fm = {
        "title": "Backtest",
        "type": "tracker-backtest",
        "as_of": data.get("as_of"),
        "wf_mean_lift": round(wf.get("mean_lift"), 4) if wf.get("mean_lift") is not None else None,
        "wf_win_rate": round(wf.get("win_rate"), 4) if wf.get("win_rate") is not None else None,
    }
    body = (
        "# Does the engine have skill? (retrospective)\n\n"
        "_This re-runs the strategy over the past year of prices for an immediate read. "
        "It's evidence of skill, not a promise of profit, and it's measured on history the "
        "models partly saw — treat the live **[[Scorecard]]** as the real test._\n\n"
        f"## 1. Do the top picks beat the average stock?\n\n{wf_verdict}\n\n"
        + (f"Win rate by regime: " + ", ".join(
            f"{k} {pct(v.get('win_rate'),0)}" for k, v in (wf.get('by_regime') or {}).items())
           + "\n\n" if wf.get("by_regime") else "")
        + f"## 2. Do the signals predict returns?\n\n{ic_verdict}\n\n{ic_tbl}\n\n"
        f"## 3. Is the market-regime call real?\n\n{rg_verdict}\n\n{rg_tbl}\n"
    )
    return document(fm, body)


# ── Strategy portfolio backtest (U4) ─────────────────────────────────────────

def strategy_backtest_note(data: dict) -> str:
    m = data.get("metrics", {}) or {}
    curve = data.get("equity_curve", []) or []
    fm = {
        "title": "Strategy Backtest",
        "type": "tracker-strategy-backtest",
        "as_of": data.get("as_of"),
        "years": data.get("years"),
        "rebalance": data.get("rebalance"),
        "total_return": None if m.get("total_return") is None else round(m["total_return"], 4),
        "cagr": None if m.get("cagr") is None else round(m["cagr"], 4),
        "sharpe": None if m.get("sharpe") is None else round(m["sharpe"], 3),
        "max_drawdown": None if m.get("max_drawdown") is None else round(m["max_drawdown"], 4),
    }
    if not curve:
        return document(fm, "# Strategy backtest\n\n"
                        "_Not enough cached history to simulate — run `track seed` first._\n")

    excess = m.get("excess")
    verdict = (
        f"{'✅' if (excess or 0) > 0 else '⚠️'} Over **{data.get('years')} years** "
        f"(rebalanced {data.get('rebalance')}ly), the strategy returned "
        f"**{pct(m.get('total_return'))}** vs SPY **{pct(m.get('spy_total_return'))}** "
        f"→ edge **{pct(excess)}**."
    )
    metrics_tbl = table(
        ["Metric", "Strategy", "SPY"],
        [
            ["Total return", pct(m.get("total_return")), pct(m.get("spy_total_return"))],
            ["CAGR", pct(m.get("cagr")), "—"],
            ["Max drawdown", pct(m.get("max_drawdown")), "—"],
            ["Sharpe (per-rebalance)", num(m.get("sharpe"), 2), "—"],
            ["Win rate (segments)", pct(m.get("win_rate"), 0), "—"],
        ],
    )
    snaps = [{"snapshot_date": c["date"], "total_value": c["strategy"],
              "benchmark_value": c["spy"]} for c in curve]
    body = (
        "# Strategy backtest (portfolio simulation)\n\n"
        f"{verdict}\n\n"
        "_A portfolio simulation: per-sector top picks, equal-weight, rebalanced; "
        "**not** a bit-exact replay of the paper trader. Sampled over history the "
        "models partly saw — evidence, not a profit promise._\n\n"
        f"## Metrics\n\n{metrics_tbl}\n\n"
        f"_{data.get('n_rebalances', 0)} rebalances · ~{data.get('avg_picks', 0):.0f} "
        "picks held each period._\n\n"
        f"## Equity curve (rebased to 100)\n\n{equity_chart(snaps)}\n"
    )
    return document(fm, body)


def fleet_note(data: dict) -> str:
    """Strategy-fleet leaderboard: N parallel paper books, one per strategy.

    ``data['rows']`` come from ``render.build.fleet_reads`` — sorted by return,
    pending members (no book yet) last with value None.
    """
    rows = data.get("rows", []) or []
    live = [r for r in rows if r.get("value") is not None]
    fm = {"title": "Fleet", "type": "tracker-fleet", "as_of": data.get("as_of"),
          "n_members": len(rows), "n_live": len(live)}
    intro = ("Several paper portfolios race in parallel, each following ONE "
             "strategy on the same weekly screen — the live forward test the "
             "Signal Lab said is the real arbiter. Same information, different "
             "weighting; the leaderboard is who's actually ahead.")
    if not live:
        return document(fm, f"# 🏁 Strategy fleet\n\n{intro}\n\n_No member books "
                        "yet — the fleet seeds at the next monthly buy window "
                        "(1st–5th)._\n")

    def _row(r):
        badge = (" **LIVE**" if r.get("kind") == "flagship"
                 else " *(control)*"
                 if (r.get("kind") == "hold" or r.get("group") == "control")
                 else " *(tourney)*" if r.get("group") == "tournament" else "")
        ret = r.get("ret_pct")
        exc = r.get("excess_pct")
        return [
            f"**{r.get('label')}**{badge}",
            money(r["value"]) if r.get("value") is not None else "—",
            (f"{'+' if (r.get('pnl') or 0) >= 0 else ''}{money(r.get('pnl'))}"
             if r.get("pnl") is not None else "—"),
            f"{ret:+.1f}%" if ret is not None else "—",
            f"{exc:+.1f}% vs SPY" if exc is not None else "—",
            str(r.get("n_positions") if r.get("n_positions") is not None else "—"),
        ]

    tbl = table(["Strategy", "Value", "P&L", "Return", "Excess", "Holdings"],
                [_row(r) for r in rows])

    # Drill-down: every live book's positions, one section per member.
    def _holdings_tbl(holdings):
        return table(
            ["Ticker", "Shares", "Price", "Value", "P&L $", "P&L %"],
            [[f"**{h.get('t')}**", f"{h.get('shares', 0):.2f}",
              money(h["price"]) if h.get("price") is not None else "—",
              money(h["value"]) if h.get("value") is not None else "—",
              money(h["pnl"]) if h.get("pnl") is not None else "—",
              f"{h['pnl_pct']:+.1f}%" if h.get("pnl_pct") is not None else "—"]
             for h in holdings])

    sections = "".join(
        f"\n## {r.get('label')}\n\n"
        + (_holdings_tbl(r["holdings"]) if r.get("holdings")
           else "_No holdings recorded yet._")
        + "\n"
        for r in rows if r.get("value") is not None)
    return document(fm, (
        f"# 🏁 Strategy fleet\n\n{intro}\n\n{tbl}\n\n"
        "_Every book is paper money on the same $10k start. Short histories are "
        "noise — let the race run before crowning anyone._\n"
        f"{sections}"
    ))
