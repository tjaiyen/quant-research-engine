"""render/notes.py — PURE note builders (engine objects → Markdown strings).

No file IO, no DB, no network — every function takes plain dicts/lists and
returns a string. ``render.build`` does the IO and calls these. This keeps the
surface fully unit-testable without a vault or a database.
"""
from __future__ import annotations

from render.markdown import document, money, num, pct, table

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

def position_note(pos: dict) -> str:
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
                   generated_at: str) -> str:
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
        f"See also: [[Regime]] · [[Performance]]\n\n"
        f"## Latest top picks\n\n{picks_tbl}\n\n"
        f"## Open paper positions\n\n{positions_view}\n\n"
        f"## Recent screener runs\n\n{runs_view}\n"
    )
    return document(fm, body)
