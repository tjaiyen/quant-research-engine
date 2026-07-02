"""render/html.py — PURE builder for a comprehensive, self-contained dashboard.

Emits a single ``Dashboard.html`` (inline CSS + inline SVG charts, NO external
libraries, CDNs, or web fonts) so it opens in any browser, offline, and syncs
via Drive. Like the Markdown notes it is a regenerated render artifact — never
hand-edited. ``render.build`` does the IO and calls ``dashboard_html``.

This is the implementation of the ``Dashboard.dc.html`` redesign handoff
(Claude Design). Ported server-side (the handoff's "Approach B"): Python renders
the final markup from the real run data, the client JS only decorates it
(tooltips / Learn mode / theme / glossary / client-side table sort / scroll-spy).
The handoff's fetch(run.json) model is intentionally NOT used — a Drive-synced
file:// page can't fetch a sibling, and the whole point is one offline artifact.

Every value that originates from data (tickers, regime, prose) is HTML-escaped
before it reaches the page (Insight B13 — content is data, never markup).
"""
from __future__ import annotations

import html as _html
import re

from render import glossary as _gloss
from render.markdown import money, num, pct

# The signals the engine actually emits per pick (== EXPECTED_SIGNAL_KEYS).
# Momentum is measured-only (held), so it is NOT a per-pick bar — its explainer
# lives in the Signal-Lab IC table, where it IS measured (see _signal_lab_section).
_SIGNALS = ("arima", "kalman", "garch", "monte_carlo", "sharpe")


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _bold(s: str) -> str:
    """Escape, then turn markdown **bold** into <strong> (decisions use it)."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _esc(s))


# ── educational layer: every label leads in plain language, real term kept, and
# carries an info button JS turns into a tooltip (hover/focus) + worked-example
# popover (click). One source of truth: render/glossary.py. ───────────────────
def _ibtn(key: str) -> str:
    """A tiny '?' button JS wires to the glossary entry for `key` (or nothing).

    The `aria-label` carries the plain definition so screen-reader users hear it
    directly, without needing the visual tooltip.
    """
    e = _gloss.GLOSSARY.get(key)
    if not e:
        return ""
    aria = f'{e["plain"]}: {e.get("short", "")}'.strip().rstrip(":")
    return (f'<button class="i" type="button" data-term="{_esc(key)}" '
            f'aria-label="{_esc(aria)}">?</button>')


def _dterm(key: str, text: str) -> str:
    """An inline dotted-underline term: hover/click opens the glossary entry."""
    if key not in _gloss.GLOSSARY:
        return _esc(text)
    return f'<span class="term" data-term="{_esc(key)}">{_esc(text)}</span>'


def _th(key: str, text: str | None = None, sort: str = "", align: str = "left") -> str:
    """A table header cell. `sort` makes it client-sortable (data-sort key)."""
    label = text if text is not None else _gloss.GLOSSARY.get(key, {}).get("plain", key)
    inner = f'{_dterm(key, str(label))}{_ibtn(key)}' if key in _gloss.GLOSSARY else _esc(label)
    attrs = f' class="{align}"'
    if sort:
        attrs += f' data-sort="{_esc(sort)}"'
    return f'<th{attrs}>{inner}</th>'


def _asof(iso) -> str:
    if not iso:
        return ""
    return f'<span class="asof">as of {_esc(str(iso)[:16].replace("T", " "))}</span>'


# GICS classifies aerospace & defense as a sub-industry of Industrials, so there's
# no "Defense" sector. For the dashboard view we break these names out into their
# own group — DISPLAY ONLY; the engine's stored sector + diversification stay GICS.
DEFENSE_TICKERS = frozenset({
    "GD", "RTX", "LMT", "NOC", "BA", "GE", "LHX", "HII", "TDG", "AXON", "LDOS", "HWM",
})


def _display_sector(ticker, sector) -> str:
    if ticker and str(ticker).upper() in DEFENSE_TICKERS:
        return "Defense"
    return sector or "Other"


def _ticker(sym, names: dict | None = None) -> str:
    nm = (names or {}).get(str(sym).upper()) if sym else None
    co = f' <span class="coname">{_esc(nm)}</span>' if nm else ""
    return f'<strong class="mono">{_esc(sym)}</strong>{co}'


def _signal_lookup(sectors: dict) -> dict:
    out: dict = {}
    for stocks in (sectors or {}).values():
        for s in (stocks or []):
            out[s.get("ticker")] = s
    return out


def _arrow(v) -> str:
    """Colour-blind cue: ▲ for a non-negative value, ▼ for negative."""
    return "▲" if (v or 0) >= 0 else "▼"


# ── equity: strategy vs SPY, indexed to 100 (band + endpoint labels) ─────────
def _equity_points(snaps: list[dict]) -> list[tuple]:
    return [(float(s["total_value"]),
             float(s["benchmark_value"]) if s.get("benchmark_value") else None)
            for s in (snaps or []) if s.get("total_value")]


def _equity_summary(snaps: list[dict]) -> dict:
    pts = _equity_points(snaps)
    if len(pts) < 2:
        return {"n": len(pts)}
    v0, vN = pts[0][0] or 1.0, pts[-1][0]
    strat = (vN / v0 - 1.0) * 100.0
    spy = None
    if pts[0][1] and pts[-1][1]:
        spy = (pts[-1][1] / pts[0][1] - 1.0) * 100.0
    return {"n": len(pts), "strat": strat, "spy": spy,
            "excess": (strat - spy) if spy is not None else None}


def _svg_equity(snaps: list[dict]) -> str:
    pts = _equity_points(snaps)
    if len(pts) < 2:
        return ('<div class="empty">The equity curve builds after the first monthly '
                'buy and a few daily snapshots accumulate.</div>')
    v0 = pts[0][0] or 1.0
    b0 = pts[0][1]
    strat = [v / v0 * 100.0 for v, _ in pts]
    bench = ([b / b0 * 100.0 if b else None for _, b in pts] if b0 else [None] * len(pts))
    allv = strat + [b for b in bench if b is not None]
    lo, hi = min(allv), max(allv)
    pad = max((hi - lo) * 0.18, 0.3)
    lo, hi = lo - pad, hi + pad
    X0, X1, Y0, Y1 = 52, 660, 24, 232
    n = len(pts)

    def xf(i):
        return X0 if n <= 1 else X0 + i * (X1 - X0) / (n - 1)

    def yf(v):
        return Y0 + (hi - v) / (hi - lo) * (Y1 - Y0)

    strat_pts = " ".join(f"{xf(i):.1f},{yf(v):.1f}" for i, v in enumerate(strat))
    have_bench = any(b is not None for b in bench)
    spy_pts = (" ".join(f"{xf(i):.1f},{yf(b):.1f}" for i, b in enumerate(bench) if b is not None)
               if have_bench else "")
    grid = []
    tv = int(hi)
    while tv >= int(lo) + 1:
        gy = yf(tv)
        stroke = "var(--border)" if tv == 100 else "var(--border-soft)"
        grid.append(f'<line x1="52" x2="660" y1="{gy:.1f}" y2="{gy:.1f}" stroke="{stroke}" '
                    f'stroke-width="1"/><text x="44" y="{gy + 3.5:.1f}" text-anchor="end" '
                    f'fill="var(--muted2)" font-size="10">{tv}</text>')
        tv -= 1
    band = ""
    if have_bench:
        top = [f"{xf(i):.1f},{yf(v):.1f}" for i, v in enumerate(strat)]
        bot = [f"{xf(j):.1f},{yf(bench[j]):.1f}" for j in range(n - 1, -1, -1)
               if bench[j] is not None]
        band = f'<polygon points="{" ".join(top + bot)}" fill="var(--neg)" opacity="0.07"/>'
    ys, yp = yf(strat[-1]), (yf(bench[-1]) if bench[-1] is not None else yf(strat[-1]))
    sm = _equity_summary(snaps)

    def fp(v):
        return f"{'+' if v >= 0 else '−'}{abs(v):.1f}%"

    strat_lbl = f"{strat[-1]:.1f} · {fp(sm.get('strat', 0))}"
    # F4 (stress-test fix): the endpoint label must tolerate a TRAILING None
    # benchmark (last snapshot missing benchmark_value while earlier ones have
    # it) — the old and/or nested f-string formatted bench[-1] unconditionally
    # and crashed the render.
    spy_end = ""
    if have_bench:
        if bench[-1] is not None and sm.get("spy") is not None:
            spy_lbl = f"{bench[-1]:.1f} · {fp(sm['spy'])}"
        else:
            spy_lbl = ""
        spy_end = (f'<circle cx="660" cy="{yp:.1f}" r="3.5" fill="var(--muted)"/>'
                   f'<text x="672" y="{yp - 3:.1f}" fill="var(--muted)" font-size="11" '
                   f'font-weight="600">SPY</text>'
                   f'<text x="672" y="{yp + 10:.1f}" fill="var(--muted)" '
                   f'font-size="10">{spy_lbl}</text>')
    spy_line = (f'<polyline points="{spy_pts}" fill="none" stroke="var(--muted)" '
                f'stroke-width="2" stroke-dasharray="5 4" opacity="0.8"/>' if spy_pts else "")
    return (f'<svg viewBox="0 0 760 260" preserveAspectRatio="xMidYMid meet" class="chart" '
            f'role="img" aria-label="Equity curve — strategy vs SPY, indexed to 100 at start.">'
            f'{"".join(grid)}{band}{spy_line}'
            f'<polyline points="{strat_pts}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>'
            f'<circle cx="660" cy="{ys:.1f}" r="4" fill="var(--accent)"/>'
            f'<text x="672" y="{ys - 3:.1f}" fill="var(--accent)" font-size="11" font-weight="600">Strategy</text>'
            f'<text x="672" y="{ys + 10:.1f}" fill="var(--muted)" font-size="10">{strat_lbl}</text>'
            f'{spy_end}</svg>')


# ── hand-rolled bars / donut (no libraries) ──────────────────────────────────
def _signal_bars(scores: dict) -> str:
    if not scores:
        return ""
    rows = []
    for sig in _SIGNALS:
        v = scores.get(sig)
        if v is None:
            continue
        wpct = max(0.0, min(1.0, float(v))) * 100.0
        col = ("var(--pos)" if wpct >= 66 else "var(--accent)" if wpct >= 45 else "var(--muted2)")
        plain = _gloss.GLOSSARY.get(sig, {}).get("plain", sig.replace("_", " "))
        rows.append(
            f'<div class="sigrow"><span class="siglbl" data-term="{sig}">{_esc(plain)}</span>'
            f'<span class="sigbar"><i style="width:{wpct:.0f}%;background:{col}"></i></span>'
            f'<span class="sigval mono">{num(v, 2)}</span></div>')
    return f'<div class="sigs">{"".join(rows)}</div>'


def _diverging_bars(rows: list[tuple]) -> str:
    """Zero-centred bars: negative grows left (red), positive right (green).
    rows = [(label_key, label_text, value, right_html)]."""
    rows = [r for r in rows if r[2] is not None]
    if not rows:
        return ""
    mx = max(abs(v) for *_, v, _ in rows) or 1.0
    out = []
    for key, text, v, right in rows:
        w = min(50.0, abs(v) / mx * 50.0)
        pos = v >= 0
        fill = (f'<i class="db-fill" style="left:50%;width:{w:.1f}%;background:var(--pos)"></i>'
                if pos else
                f'<i class="db-fill" style="right:50%;width:{w:.1f}%;background:var(--neg)"></i>')
        lbl = _dterm(key, text) if key else _esc(text)
        out.append(f'<div class="db-row"><span class="db-lbl">{lbl}</span>'
                   f'<div><div class="db-track"><i class="db-zero"></i>{fill}</div>'
                   f'<div class="db-val mono">{right}</div></div></div>')
    return f'<div class="dbars">{"".join(out)}</div>'


_DONUT_HUES = ["#5b9dff", "#3fc17d", "#e2b23f", "#f0595c", "#a78bfa",
               "#38bdc9", "#ec7a54", "#d9679f", "#8a93a6", "#e6c04a"]


def _svg_donut(parts: list[tuple], center_num=None, center_label="") -> str:
    parts = [(l, float(v)) for l, v in parts if v and float(v) > 0]
    if not parts:
        return ""
    tot = sum(v for _, v in parts)
    off, segs, legend = 0.0, [], []
    for i, (l, v) in enumerate(parts):
        frac = v / tot * 100.0
        c = _DONUT_HUES[i % len(_DONUT_HUES)]
        segs.append(
            f'<circle cx="75" cy="75" r="62" fill="none" stroke="{c}" stroke-width="24" '
            f'pathLength="100" stroke-dasharray="{frac:.2f} 100" stroke-dashoffset="{-off:.2f}" '
            f'transform="rotate(-90 75 75)"/>')
        legend.append(f'<span class="lg"><i style="background:{c}"></i>'
                      f'{_esc(str(l).replace("_", " "))} <b class="mono">{frac:.0f}%</b></span>')
        off += frac
    cnum = (f'<text x="75" y="72" text-anchor="middle" fill="var(--text)" font-size="17" '
            f'font-weight="600" class="mono">{_esc(center_num)}</text>'
            f'<text x="75" y="88" text-anchor="middle" fill="var(--muted2)" font-size="9">'
            f'{_esc(center_label)}</text>' if center_num is not None else "")
    return (f'<div class="donut-wrap"><svg viewBox="0 0 150 150" width="132" height="132" '
            f'class="donut" role="img" aria-label="Allocation by sector">{"".join(segs)}'
            f'<circle cx="75" cy="75" r="42" fill="var(--surface)"/>{cnum}</svg>'
            f'<div class="donut-lg">{"".join(legend)}</div></div>')


# ── sections ─────────────────────────────────────────────────────────────────
def _regime_pill(regime: dict) -> str:
    label = regime.get("label", "unknown")
    conf = regime.get("confidence")
    tone = {"bull": "pos", "bear": "neg"}.get(str(label).lower(), "warn")
    conf_s = f" · {pct(conf)} conf" if conf is not None else ""
    return (f'<span class="pill {tone}" data-term="regime">'
            f'<span class="dot"></span>{_esc(str(label).title())} market{conf_s}</span>')


def _verdict_section(data: dict) -> str:
    """§Today's read — an honest narrative reconciling the conflicting evidence."""
    regime = data.get("regime") or {}
    snap = data.get("latest_snapshot") or {}
    pnl = ((snap.get("unrealized_pnl") or 0) + (snap.get("realized_pnl_ytd") or 0)) if snap else None
    total = snap.get("total_value")
    base = (total - pnl) if (total and pnl is not None) else 10000
    pnl_pct = (pnl / base * 100.0) if (pnl is not None and base) else None
    sm = _equity_summary(data.get("snapshots") or [])
    sl = (data.get("signal_lab") or {}).get("signals") or {}
    healthy = sum(1 for d in sl.values() if (d.get("ic") or 0) > 0)
    total_sig = len(sl)
    rlabel = str(regime.get("label", "the market")).title()

    # Headline + body compose from whatever is present, honestly.
    if pnl is not None and pnl_pct is not None:
        pnl_txt = (f'<b class="{"pos" if pnl >= 0 else "neg"} mono">'
                   f'{_arrow(pnl)} {money(pnl)} ({"+" if pnl_pct >= 0 else "−"}'
                   f'{abs(pnl_pct):.1f}%)</b>')
    else:
        pnl_txt = '<b class="mono">$0.00</b>'

    if sm.get("excess") is not None:
        excess = sm["excess"]
        ahead = excess >= 0
        head = (f"Ahead of the market and up on paper." if ahead and (pnl or 0) >= 0 else
                f"Up on paper, but trailing the market — and it's still early." if (pnl or 0) >= 0 else
                f"Behind on paper and trailing the market — still early.")
        spy_clause = (
            f' — yet simply buying the {_dterm("spy", "S&P 500")} returned '
            f'<b class="mono">{"+" if (sm.get("spy") or 0) >= 0 else "−"}{abs(sm.get("spy") or 0):.1f}%</b> '
            f'over the same {sm["n"]} snapshots, so the strategy is '
            f'<b class="{"pos" if ahead else "neg"}">{"ahead" if ahead else "behind"} by '
            f'{abs(excess):.1f}%</b>.')
    else:
        head = f"Early days — not enough history to judge yet."
        spy_clause = ""

    sig_clause = ""
    if total_sig:
        sig_clause = (f' Of the {total_sig} {_dterm("ic", "signals")} measured, '
                      f'<b class="{"pos" if healthy > total_sig / 2 else "warn"}">{healthy} '
                      f'predict forwards</b> and the rest predict backwards.')
    snaps_clause = (f' With only <b>{sm.get("n", 0)} snapshots</b> logged, treat everything '
                    f'below as early and unproven.' if sm.get("n", 99) < 8 else "")

    body = (f'The paper book is {pnl_txt}{spy_clause}{sig_clause}{snaps_clause}')

    # Chips
    chips = []
    if sm.get("excess") is not None:
        ex = sm["excess"]
        chips.append(("Momentum", "warn" if ex < 0 else "pos",
                      "Behind market" if ex < 0 else "Ahead of market",
                      f'{"+" if ex >= 0 else "−"}{abs(ex):.1f}% vs SPY, {sm["n"]} snapshots'))
    if total_sig:
        chips.append(("Signals", "warn" if healthy <= total_sig / 2 else "pos",
                      f"{healthy} of {total_sig} healthy",
                      "over half predicting backwards" if healthy <= total_sig / 2
                      else "majority predicting forwards"))
    chips.append(("Track record", "muted" if sm.get("n", 0) < 8 else "pos",
                  "Too early" if sm.get("n", 0) < 8 else "Accumulating",
                  f'{sm.get("n", 0)} snapshots logged'))
    chip_html = "".join(
        f'<div class="vchip"><div class="vchip-hd"><span class="dot {c}"></span>{_esc(lbl)}</div>'
        f'<div class="vchip-val">{_esc(val)}</div><div class="vchip-note">{_esc(note)}</div></div>'
        for lbl, c, val, note in chips)

    return (f'<section id="verdict" class="verdict">'
            f'<div class="verdict-bar"></div>'
            f'<div class="eyebrow">Today\'s read <span class="rule"></span></div>'
            f'<h2 class="verdict-h">{_esc(head)}</h2>'
            f'<p class="verdict-body">{body}</p>'
            f'<div class="vchips">{chip_html}</div></section>')


def _auto_banner(lr: dict) -> str:
    """Automation-health beacon: green=OK, red=failed, amber=stale."""
    if not lr:
        return ""
    job = _esc(lr.get("job"))
    ended = _esc(str(lr.get("ended")).replace("T", " ")[:16])
    if lr.get("status") == "fail":
        return (f'<div class="autobar neg"><b>⚠ Automation issue</b>'
                f'<span>— last {_dterm("automation_health", "scheduled run")} '
                f'(<b>{job}</b>) FAILED at {ended} — check <code>logs/</code>.</span></div>')
    if lr.get("stale"):
        return (f'<div class="autobar warn"><b>⚠ Automation stale</b>'
                f'<span>— no {_dterm("automation_health", "scheduled run")} recently; '
                f'last <b>{job}</b> at {ended}.</span></div>')
    return (f'<div class="autobar pos"><b>✓ Automation healthy</b>'
            f'<span>— last {_dterm("automation_health", "scheduled run")} '
            f'(<b>{job}</b>) completed {ended} UTC.</span></div>')


def _kpis(data: dict) -> str:
    snap = data.get("latest_snapshot") or {}
    picks = data.get("top_picks") or []
    total = snap.get("total_value")
    pnl = ((snap.get("unrealized_pnl") or 0) + (snap.get("realized_pnl_ytd") or 0)) if snap else None
    base = (total - pnl) if (total and pnl is not None) else 10000
    pnl_pct = (pnl / base * 100.0) if (pnl is not None and base) else 0.0
    dd = snap.get("drawdown_from_peak")
    n_pos = snap.get("n_positions", len(data.get("positions") or []))
    cash = snap.get("cash")
    cards = [
        ("paper_value", money(total) if total else "$10,000", "paper money", "text",
         "What the make-believe $10,000 account is worth right now. No real money is used."),
        ("total_pnl",
         (f'{_arrow(pnl)} {money(pnl)}' if pnl is not None else "$0.00"),
         (f'realized + unrealized · {"+" if pnl_pct >= 0 else "−"}{abs(pnl_pct):.1f}%'),
         ("pos" if (pnl or 0) >= 0 else "neg"),
         "Money made or lost so far, from both sold and still-held positions."),
        ("drawdown", pct(dd) if dd is not None else "0.0%", "from peak", "text",
         "How far the account is below the best value it ever reached. Smaller is safer."),
        ("positions", str(n_pos), "open positions", "text",
         "How many different stocks the paper account owns right now."),
        ("cash", money(cash) if cash is not None else "$10,000", "available", "text",
         "Pretend dollars not yet invested — ready for the next buy."),
    ]
    out = []
    for key, val, sub, tone, explain in cards:
        e = _gloss.GLOSSARY.get(key, {})
        label = e.get("plain", key)
        out.append(
            f'<div class="kpi"><div class="kpi-label"><span class="term" data-term="{key}">'
            f'{_esc(label)}</span></div>'
            f'<div class="kpi-val mono {tone}">{_esc(val)}</div>'
            f'<div class="kpi-sub">{_esc(sub)}</div>'
            f'<div class="explain">{_esc(explain)}</div></div>')
    return f'<div class="kpis">{"".join(out)}</div>'


def _positions_section(positions: list[dict], names: dict | None = None) -> str:
    if not positions:
        return ('<section id="positions" class="card"><h3>Positions</h3>'
                '<div class="empty">No open positions yet — the first paper buys land '
                'in the monthly 1st–5th window.</div></section>')
    rows_data = []
    for p in positions:
        sh = float(p.get("shares", p.get("quantity")) or 0.0)
        cost = float(p.get("avg_cost", p.get("cost_basis", p.get("entry_price"))) or 0.0)
        price = p.get("current_price")
        mv = p.get("market_value")
        if mv is None and price is not None:
            mv = sh * float(price)
        upnl = p.get("unrealized_pnl")
        if upnl is None and price is not None:
            upnl = (float(price) - cost) * sh
        pnl_pct = (upnl / (cost * sh) * 100.0) if (upnl is not None and cost and sh) else None
        rows_data.append({
            "t": p.get("ticker"), "sec": _display_sector(p.get("ticker"), p.get("sector")),
            "price": price, "val": mv, "pnl": upnl, "pnlpct": pnl_pct})
    total_mv = sum(r["val"] or 0 for r in rows_data)
    for r in rows_data:
        r["port"] = (r["val"] / total_mv * 100.0) if (r["val"] and total_mv) else None
    max_abs = max((abs(r["pnlpct"]) for r in rows_data if r["pnlpct"] is not None), default=1.0) or 1.0

    trs = []
    for r in sorted(rows_data, key=lambda x: (x["sec"] or "", -(x["val"] or 0))):
        pos = (r["pnl"] or 0) >= 0
        tone = "pos" if pos else "neg"
        barw = min(50.0, abs(r["pnlpct"] or 0) / max_abs * 50.0)
        bar_side = "left" if pos else "right"
        bar = (f'<span class="pbar"><i style="{bar_side}:50%;width:{barw:.1f}%;'
               f'background:var(--{tone})"></i><i class="pbar-mid"></i></span>')
        # F3 (stress-test fix): port is None when a position lacks price/value
        # (e.g. fresh book, uncached ticker) — formatting None crashed the
        # whole dashboard render. Every cell now degrades to '—'.
        port_s = f'{r["port"]:.1f}%' if r["port"] is not None else "—"
        trs.append(
            f'<tr data-t="{_esc(r["t"])}" data-sec="{_esc(r["sec"])}" '
            f'data-price="{r["price"] or 0}" data-val="{r["val"] or 0}" '
            f'data-pnl="{r["pnl"] or 0}" data-pnlpct="{r["pnlpct"] or 0}" '
            f'data-port="{r["port"] or 0}">'
            f'<td>{_ticker(r["t"], names)}</td>'
            f'<td class="muted">{_esc((r["sec"] or "").replace("_", " "))}</td>'
            f'<td class="right mono t2">{money(r["price"]) if r["price"] is not None else "—"}</td>'
            f'<td class="right mono">{money(r["val"]) if r["val"] is not None else "—"}</td>'
            f'<td class="right mono {tone}">{_arrow(r["pnl"])} {money(r["pnl"]) if r["pnl"] is not None else "—"}</td>'
            f'<td class="right"><div class="pnlpct">{bar}'
            f'<span class="mono {tone}">{_arrow(r["pnlpct"])} '
            f'{abs(r["pnlpct"]):.1f}%</span></div></td>'
            f'<td class="right mono muted">{port_s}</td></tr>' if r["pnlpct"] is not None else
            f'<tr data-t="{_esc(r["t"])}" data-sec="{_esc(r["sec"])}" data-val="{r["val"] or 0}">'
            f'<td>{_ticker(r["t"], names)}</td>'
            f'<td class="muted">{_esc((r["sec"] or "").replace("_", " "))}</td>'
            f'<td class="right mono t2">{money(r["price"]) if r["price"] is not None else "—"}</td>'
            f'<td class="right mono">{money(r["val"]) if r["val"] is not None else "—"}</td>'
            f'<td class="right mono">—</td><td class="right mono">—</td>'
            f'<td class="right mono muted">{port_s}</td></tr>')
    g_pnl = sum(r["pnl"] or 0 for r in rows_data)
    gt = "pos" if g_pnl >= 0 else "neg"
    g_basis = sum((r["val"] or 0) - (r["pnl"] or 0) for r in rows_data) or 1.0
    total_row = (
        f'<tr class="total"><td colspan="3">Total</td>'
        f'<td class="right mono">{money(total_mv)}</td>'
        f'<td class="right mono {gt}">{_arrow(g_pnl)} {money(g_pnl)}</td>'
        f'<td class="right mono {gt}">{_arrow(g_pnl)} {abs(g_pnl / g_basis * 100.0):.1f}%</td>'
        f'<td class="right mono muted">100%</td></tr>')
    head = (f'<tr>{_th("positions", "Ticker", sort="t")}<th data-sort="sec">Sector</th>'
            f'{_th("", "Price", sort="price", align="right")}'
            f'<th class="right" data-sort="val">Value</th>'
            f'{_th("unrealized_pnl", "P&L $", sort="pnl", align="right")}'
            f'<th class="right" data-sort="pnlpct" style="min-width:150px">P&amp;L %</th>'
            f'<th class="right" data-sort="port">% Port</th></tr>')
    return (f'<section id="positions" class="card scroll-x"><div class="card-hd">'
            f'<h3><span class="term" data-term="positions">Positions</span> '
            f'<span class="muted thin">· {len(positions)} holdings</span></h3>'
            f'<span class="hint">Click a column to sort</span></div>'
            f'<table class="tbl sortable" data-sort-key="sec" data-sort-dir="1">'
            f'<thead>{head}</thead><tbody>{"".join(trs)}</tbody>'
            f'<tfoot>{total_row}</tfoot></table></section>')


def _screener_stats(summary: dict) -> str:
    if not summary:
        return ""
    stats = [
        (summary.get("total_screened"), "Stocks looked at", "universe", "text"),
        (summary.get("total_passed_veto"), "Passed safety", "veto", "pos"),
        (f'{summary.get("veto_rate_pct", 0)}%', "Screened out", "veto_rate", "muted"),
        (summary.get("total_skipped"), "Stale data", "delisted_stale", "muted"),
        (summary.get("total_failed"), "Failed checks", "veto", "text"),
        (summary.get("total_sectors"), "Sectors", "sector", "text"),
    ]
    cells = "".join(
        f'<div class="stat"><div class="stat-v mono {tone}">{_esc(v)}</div>'
        f'<div class="stat-l"><span class="term" data-term="{key}">{_esc(lbl)}</span></div></div>'
        for v, lbl, key, tone in stats if v is not None)
    return (f'<section id="screen-card" class="card"><h3>Screener</h3>'
            f'<p class="muted">The full {_dterm("universe", "universe")} the engine '
            f'evaluated this run.</p><div class="stats">{cells}</div></section>')


def _picks_section(picks: list[dict], sectors: dict, names: dict | None = None) -> str:
    if not picks:
        return ('<section class="card"><h3>Top picks</h3>'
                '<div class="empty">No screener run yet.</div></section>')
    look = _signal_lookup(sectors)
    rows = []
    for p in picks[:6]:
        s = look.get(p.get("ticker"), {})
        score = p.get("composite_score", p.get("score"))
        rows.append(
            f'<div class="pick"><div class="pick-hd">{_ticker(p.get("ticker"), names)}'
            f'<span class="muted thin">{_esc(p.get("sector", ""))}</span>'
            f'<span class="pick-score mono">{num(score, 3)}</span></div>'
            f'{_signal_bars(s.get("signal_scores", {}))}</div>')
    return (f'<section class="card"><h3>Top picks</h3>'
            f'<p class="muted">A single {_dterm("composite", "conviction score")} '
            f'(0–1) blending all signals.</p>{"".join(rows)}</section>')


_DEC_TAG = {"screen": ("Screen", "pos"), "trade": ("Trade", "warn"), "check": ("Check", "muted")}


def _decisions_section(decisions: list[str]) -> str:
    if not decisions:
        return ('<section class="card"><h3>Recent decisions</h3>'
                '<div class="empty">No decisions logged yet.</div></section>')
    items = []
    for d in decisions[:10]:
        low = str(d).lower()
        kind = ("screen" if "screen" in low else "trade" if "trade" in low
                or "opened" in low or "bought" in low else "check")
        tag, tone = _DEC_TAG[kind]
        items.append(f'<li><span class="tag {tone}">{tag}</span>'
                     f'<div class="dec-text">{_bold(d)}</div></li>')
    return (f'<section class="card"><h3>Recent decisions</h3>'
            f'<ul class="feed">{"".join(items)}</ul></section>')


def _scorecard_section(sc: dict | None) -> str:
    if not sc:
        return ""
    horizons = sc.get("horizons", {}) or {}
    rows = []
    for key, m in horizons.items():
        m = m or {}
        hit, alpha = m.get("hit_rate"), m.get("avg_alpha")
        htone = "muted2" if hit is None else ("pos" if (alpha or 0) >= 0 else "neg")
        rows.append(
            f'<tr><td class="mono">{_esc(key)}</td>'
            f'<td class="right mono muted">{_esc(m.get("n", 0))}</td>'
            f'<td class="right mono {htone}">{pct(hit) if hit is not None else "—"}</td>'
            f'<td class="right mono {htone}">{pct(alpha) if alpha is not None else "—"}</td></tr>')
    graded = any((m or {}).get("n") for m in horizons.values())
    intro = ("Too early to judge — picks need a few weeks of forward data." if not graded
             else "Past picks graded vs. what prices did (alpha = pick − SPY).")
    table = (f'<table class="tbl"><thead><tr>{_th("horizon", "Horizon")}<th class="right">Picks</th>'
             f'{_th("hit_rate", "Hit rate", align="right")}'
             f'{_th("alpha", "Avg alpha", align="right")}</tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table>' if rows else "")
    paper = sc.get("paper", {}) or {}
    note = ""
    if paper.get("status") in ("ok", "cash_only"):
        note = (f'<p class="callout">Paper vs SPY: {pct(paper.get("port_return"))} vs '
                f'{pct(paper.get("spy_return"))} '
                f'(<b>{pct(paper.get("excess"))}</b> excess, {_esc(paper.get("n_days"))} days).</p>')
    return (f'<section id="scorecard-card" class="card">'
            f'<h3><span class="term" data-term="scorecard">Scorecard</span></h3>'
            f'<p class="muted">{_esc(intro)}</p>{table}{note}</section>')


def _signal_lab_section(sl: dict) -> str:
    sigs = sl.get("signals") or {}
    if not sigs:
        return ""
    val = sl.get("validation") or {}
    strip = ""
    if val.get("candidate_oos") is not None:
        strip = (f'<p class="muted">{_dterm("out_of_sample", "Fresh-data test")}: candidate '
                 f'<b class="t2">{pct(val.get("candidate_oos"))}</b> · default '
                 f'{pct(val.get("default_oos"))} · SPY {pct(val.get("spy_oos"))}.</p>')
    bar_rows = []
    for s, d in sorted(sigs.items(), key=lambda kv: -(kv[1].get("ic") or -9)):
        ic = d.get("ic")
        plain = _gloss.GLOSSARY.get(s, {}).get("plain", s)
        verdict = _esc(d.get("verdict", "")[:18])
        col = "var(--pos)" if (ic or 0) >= 0 else "var(--neg)"
        right = (f'{pct(ic)} · <span style="color:{col};font-family:var(--sans)">{verdict}</span>')
        bar_rows.append((s, plain, ic, right))
    return (f'<section class="card"><h3><span class="term" data-term="ic">Signal Lab</span> '
            f'{_asof(sl.get("as_of"))}</h3>{strip}'
            f'<p class="muted small">Bars left of centre predict <b class="neg">backwards</b>; '
            f'right predict <b class="pos">forwards</b>.</p>{_diverging_bars(bar_rows)}</section>')


def _fleet_section(rows: list[dict]) -> str:
    """Strategy-fleet leaderboard: one paper book per strategy, ranked live."""
    if not rows:
        return ""
    live = [r for r in rows if r.get("value") is not None]
    intro = (f'<p class="muted">Parallel paper books, one per strategy, on the '
             f'same weekly screen — the {_dterm("fleet", "live forward test")}. '
             f'Same information, different weighting.</p>')
    if not live:
        return (f'<section class="card"><h3><span class="term" data-term="fleet">'
                f'Strategy fleet</span></h3>{intro}'
                f'<div class="empty">No member books yet — the fleet seeds at the '
                f'next monthly buy window (1st–5th).</div></section>')
    trs = []
    for i, r in enumerate(rows, start=1):
        badge = ('<span class="tag pos">LIVE</span>' if r.get("kind") == "flagship"
                 else '<span class="tag muted">CONTROL</span>'
                 if (r.get("kind") == "hold" or r.get("group") == "control")
                 else '<span class="tag warn">TOURNEY</span>'
                 if r.get("group") == "tournament" else "")
        val, pnl = r.get("value"), r.get("pnl")
        ret, exc = r.get("ret_pct"), r.get("excess_pct")
        tone = "pos" if (pnl or 0) >= 0 else "neg"
        etone = "pos" if (exc or 0) >= 0 else "neg"
        since = (f' <span class="coname">since {_esc(r["since"])}</span>'
                 if r.get("since") else "")
        trs.append(
            f'<tr><td class="mono muted">{i if val is not None else "—"}</td>'
            f'<td><strong>{_esc(r.get("label"))}</strong> {badge}{since}</td>'
            f'<td class="right mono">{money(val) if val is not None else "—"}</td>'
            f'<td class="right mono {tone if pnl is not None else ""}">'
            f'{f"{_arrow(pnl)} {money(pnl)}" if pnl is not None else "—"}</td>'
            f'<td class="right mono {tone if ret is not None else ""}">'
            f'{f"{ret:+.1f}%" if ret is not None else "pending"}</td>'
            f'<td class="right mono {etone if exc is not None else ""}">'
            f'{f"{exc:+.1f}%" if exc is not None else "—"}</td></tr>')
    tbl = (f'<table class="tbl"><thead><tr><th>#</th><th>Strategy</th>'
           f'<th class="right">Value</th><th class="right">P&amp;L</th>'
           f'<th class="right">Return</th><th class="right">vs SPY</th></tr>'
           f'</thead><tbody>{"".join(trs)}</tbody></table>')
    return (f'<section class="card scroll-x"><h3><span class="term" data-term="fleet">'
            f'Strategy fleet</span> <span class="muted thin">· {len(live)} of '
            f'{len(rows)} racing</span></h3>{intro}{tbl}'
            f'<p class="callout">Every book starts at $10,000 paper. Short histories '
            f'are noise — let the race run before crowning anyone.</p></section>')


def _sector_donut(sectors: dict) -> str:
    if not sectors:
        return ""
    remapped: dict = {}
    for name, stocks in sectors.items():
        for s in (stocks or []):
            remapped.setdefault(_display_sector(s.get("ticker"), name), []).append(s)
    parts, total_passed = [], 0
    for name, stocks in remapped.items():
        passed = sum(1 for s in (stocks or []) if s.get("passed_veto"))
        if passed:
            parts.append((name, passed))
            total_passed += passed
    if not parts:
        return ""
    donut = _svg_donut(parts, center_num=total_passed, center_label="passed")
    return (f'<section class="card"><h3>Candidates by '
            f'<span class="term" data-term="sector">sector</span></h3>'
            f'<p class="muted">Stocks that passed safety checks, spread for balance.</p>'
            f'{donut}</section>')


def _veto_key(reason: str) -> str:
    r = str(reason).upper()
    if "EARNINGS" in r:
        return "earnings_blackout"
    if "SENTIMENT" in r:
        return "sentiment_score"
    if "VOL" in r:
        return "garch"
    if "TAIL" in r or "MC" in r:
        return "monte_carlo"
    return "veto"


def _vetoes_section(sectors: dict, summary: dict, names: dict | None = None) -> str:
    vetoed = [(s.get("ticker"), s.get("veto_reason"))
              for stocks in (sectors or {}).values() for s in (stocks or [])
              if not s.get("passed_veto") and s.get("veto_reason")]
    skip = (summary or {}).get("total_skipped")
    if not vetoed and not skip:
        return ""
    counts: dict = {}
    for _, reason in vetoed:
        counts[reason] = counts.get(reason, 0) + 1
    stat_cells = "".join(
        f'<div class="stat"><div class="stat-v mono neg">{n}</div>'
        f'<div class="stat-l"><span class="term" data-term="{_veto_key(r)}">{_esc(r)}</span></div></div>'
        for r, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    if skip:
        stat_cells += (f'<div class="stat"><div class="stat-v mono muted">{skip}</div>'
                       f'<div class="stat-l"><span class="term" data-term="delisted_stale">'
                       f'Stale / skipped</span></div></div>')
    sample = ", ".join(_esc(t) for t, _ in vetoed[:12])
    sample_html = f'<p class="muted small">Vetoed this run: <b class="mono t2">{sample}</b></p>' if sample else ""
    # best pick per sector
    best_rows = ""
    if sectors:
        remapped: dict = {}
        for name, stocks in sectors.items():
            for s in (stocks or []):
                remapped.setdefault(_display_sector(s.get("ticker"), name), []).append(s)
        lines = []
        for name, stocks in remapped.items():
            stocks = stocks or []
            top = next((s for s in stocks if s.get("rank") == 1), stocks[0] if stocks else {})
            passed = sum(1 for s in stocks if s.get("passed_veto"))
            score = top.get("composite_score")
            col = "var(--text)" if (score or 0) > 0 else "var(--muted2)"
            lines.append(
                f'<div class="sp-row"><span class="sp-sec">{_esc(name.replace("_", " "))}</span>'
                f'<b class="mono">{_esc(top.get("ticker", "—"))}</b>'
                f'<span class="mono" style="margin-left:auto;color:{col}">'
                f'{num(score, 3) if score is not None else "—"}</span>'
                f'<span class="mono muted2 sp-passed">{passed}/{len(stocks)}</span></div>')
        best_rows = (f'<div class="sp-hd">Best pick per sector</div>'
                     f'<div class="sp-list">{"".join(lines)}</div>')
    return (f'<section class="card"><h3><span class="term" data-term="veto">Vetoes</span> & skips</h3>'
            f'<p class="muted">Safety gate — a stock failing any risk check is dropped before '
            f'ranking.</p><div class="stats two">{stat_cells}</div>{sample_html}'
            f'<div class="sp-block">{best_rows}</div></section>')


_HEALTH_TONE = {"STRONG": "pos", "WEAK": "neg"}
_HEALTH_RANK = {"STRONG": 3, "FAIR": 2, "WEAK": 1}


def _company_health_section(rows: list[dict], names: dict | None = None) -> str:
    rows = [r for r in (rows or []) if r.get("health_label")]
    if not rows:
        return ""
    trs = []
    for r in rows:
        lbl = r.get("health_label")
        tone = _HEALTH_TONE.get(lbl, "warn")
        floors = f" ({r['floors_passed']}/{r['floors_total']})" if r.get("floors_total") else ""
        surp = r.get("last_surprise_pct")
        roe, de, pe = r.get("roe"), r.get("debt_to_equity"), r.get("pe")
        if surp is None:
            last, etone, esort = "—", "", 0
        else:
            etone = "pos" if surp > 1 else "neg" if surp < -1 else "muted"
            tag = "beat" if surp > 1 else "miss" if surp < -1 else "in-line"
            last, esort = f'{"+" if surp >= 0 else "−"}{abs(surp):.1f}% {tag}', surp
        trs.append(
            f'<tr data-t="{_esc(r.get("ticker"))}" data-health="{_HEALTH_RANK.get(lbl, 0)}" '
            f'data-roe="{roe if roe is not None else -999}" '
            f'data-de="{de if de is not None else 999}" data-pe="{pe if pe is not None else 999}" '
            f'data-earn="{esort}">'
            f'<td>{_ticker(r.get("ticker"), names)}</td>'
            f'<td class="mono small {tone}"><b>{_esc(lbl)}{_esc(floors)}</b></td>'
            f'<td class="right mono t2">{pct(roe / 100) if roe is not None else "—"}</td>'
            f'<td class="right mono t2">{num(de, 2) if de is not None else "—"}</td>'
            f'<td class="right mono t2">{num(pe, 1) if pe is not None else "—"}</td>'
            f'<td class="right mono {etone}">{last}</td>'
            f'<td class="right mono muted">{_esc(r.get("next_earnings") or "—")}</td></tr>')
    head = (f'{_th("health_score", "Company", sort="t")}'
            f'<th data-sort="health">Health</th>'
            f'{_th("roe", "ROE", sort="roe", align="right")}'
            f'{_th("debt_to_equity", "Debt/Eq", sort="de", align="right")}'
            f'<th class="right" data-sort="pe">P/E</th>'
            f'{_th("earnings_surprise", "Last earnings", sort="earn", align="right")}'
            f'{_th("next_earnings", "Next", align="right")}')
    return (f'<section class="card scroll-x"><div class="card-hd">'
            f'<h3><span class="term" data-term="health_score">Company health</span></h3>'
            f'<span class="hint">Click a column to sort</span></div>'
            f'<p class="muted">Is each holding financially sound — profitability + balance sheet '
            f'vs. its sector\'s minimums.</p>'
            f'<table class="tbl sortable" data-sort-key="" data-sort-dir="1">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(trs)}</tbody></table></section>')


def _sentiment_section(rows: list[dict], names: dict | None = None) -> str:
    rows = [r for r in (rows or []) if r.get("label") and r.get("label") != "UNAVAILABLE"]
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: (r.get("sentiment_score") or 0))[:10]
    tr = "".join(
        f'<tr><td>{_ticker(r.get("ticker"), names)}</td>'
        f'<td class="right mono">{num(r.get("sentiment_score"), 3)}</td>'
        f'<td>{_esc(r.get("label"))}</td>'
        f'<td class="right mono muted">{_esc(r.get("n_headlines"))}</td></tr>' for r in rows)
    return (f'<section class="card"><h3><span class="term" data-term="finbert">News sentiment</span></h3>'
            f'<table class="tbl"><thead><tr><th>Ticker</th>'
            f'{_th("sentiment_score", "Score", align="right")}'
            f'<th>Label</th><th class="right">Headlines</th></tr></thead>'
            f'<tbody>{tr}</tbody></table></section>')


def _copilot_section(copilot: dict) -> str:
    if not (copilot.get("available") and copilot.get("commentary")):
        return ""
    paras = "".join(f"<p>{_esc(par.strip())}</p>"
                    for par in copilot["commentary"].split("\n\n") if par.strip())
    return (f'<section class="card copilot"><h3><span class="term" data-term="copilot">'
            f'Co-pilot take</span></h3>'
            f'<p class="muted">Claude ({_esc(copilot.get("model", "—"))}) · advisory only, '
            f'never trades</p>{paras}</section>')


def _tournament_section(t: dict) -> str:
    board = t.get("leaderboard") or []
    if not board:
        return ""
    bar_rows = []
    for r in board[:12]:
        tot = r.get("total")
        tone = "muted" if r.get("group") == "control" else ("pos" if (tot or 0) >= 0 else "neg")
        lbl = _esc(r.get("label"))
        bar_rows.append((_gloss.strategy_key(r.get("label")), lbl, tot, pct(tot)))
    strip = (f'<p class="muted">Winner beat the market by <b>{pct(t.get("beat_spy"))}</b> · '
             f'beat random by <b>{pct(t.get("beat_random"))}</b> · fresh-data rank '
             f'{_esc(t.get("oos_rank", "—"))}.</p>')
    return (f'<section class="card"><h3><span class="term" data-term="tournament">'
            f'Strategy tournament</span> {_asof(t.get("as_of"))}</h3>'
            f'<p class="muted">{_esc(t.get("verdict", ""))}</p>{strip}'
            f'{_diverging_bars(bar_rows)}'
            f'<p class="callout">A guarded hypothesis, not proof — in-sample-aware, '
            f'controls included.</p></section>')


def _zone_header(zid: str, title: str) -> str:
    return (f'<div id="{zid}" class="zone-h"><span>{_esc(title)}</span>'
            f'<span class="rule"></span></div>')


# ── glossary embed + client JS ───────────────────────────────────────────────
_PAGE_JS = r"""(function(){
  var G = __GLOSSARY_JSON__;
  function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':s);return d.innerHTML;}

  // ── restore prefs ──
  try{
    if(localStorage.getItem('qt_learn')==='1') document.body.setAttribute('data-learn','1');
    var t=localStorage.getItem('qt_theme');
    if(t==='light'){ document.documentElement.setAttribute('data-theme','light'); }
    else if(!t && window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches){
      document.documentElement.setAttribute('data-theme','light'); }
    syncToggleLabels();
  }catch(e){}

  // ── tooltip / worked-example popover ──
  var tip=document.createElement('div'); tip.className='qt-tip'; document.body.appendChild(tip);
  var pinned=false;
  function place(el){ tip.style.visibility='hidden'; tip.style.opacity='1';
    var r=el.getBoundingClientRect(),tw=tip.offsetWidth,th=tip.offsetHeight;
    var left=Math.max(8,Math.min(r.left,innerWidth-tw-8)); var top=r.bottom+8;
    if(top+th>innerHeight-8) top=r.top-th-8;
    tip.style.left=left+'px'; tip.style.top=Math.max(8,top)+'px'; tip.style.visibility=''; }
  function short(el){ if(pinned) return; var e=G[el.getAttribute('data-term')]; if(!e) return;
    tip.style.pointerEvents='none'; tip.innerHTML=esc(e.short||''); place(el); }
  function rich(el){ var e=G[el.getAttribute('data-term')]; if(!e) return; pinned=true;
    var h='<div class="qt-tip-h">'+esc(e.plain)+(e.term&&e.term!==e.plain?' <span class="qt-tip-t">('+esc(e.term)+')</span>':'')+'</div>';
    h+='<div>'+esc(e.long||e.short||'')+'</div>';
    if(e.example) h+='<div class="qt-tip-ex"><b>Example:</b> '+esc(e.example)+'</div>';
    if(e.theory) h+='<div class="qt-tip-th">'+esc(e.theory)+'</div>';
    tip.style.pointerEvents='auto'; tip.innerHTML=h; place(el); }
  document.addEventListener('mouseover',function(ev){ var el=ev.target.closest&&ev.target.closest('[data-term]'); if(el) short(el); });
  document.addEventListener('mouseout',function(ev){ var el=ev.target.closest&&ev.target.closest('[data-term]'); if(el&&!pinned) tip.style.opacity='0'; });
  document.addEventListener('focusin',function(ev){ var el=ev.target.closest&&ev.target.closest('[data-term]'); if(el&&!pinned) short(el); });
  document.addEventListener('focusout',function(ev){ var el=ev.target.closest&&ev.target.closest('[data-term]'); if(el&&!pinned) tip.style.opacity='0'; });
  document.addEventListener('click',function(ev){
    var el=ev.target.closest&&ev.target.closest('[data-term]');
    if(el){ ev.stopPropagation(); rich(el); return; }
    if(pinned && !tip.contains(ev.target)){ pinned=false; tip.style.opacity='0'; }
  },true);
  document.addEventListener('keydown',function(ev){
    if(ev.key==='Escape'){ pinned=false; tip.style.opacity='0'; closeGloss(); return; }
    if(ev.key==='Enter'||ev.key===' '){ var el=document.activeElement;
      if(el&&el.matches&&el.matches('[data-term]')){ ev.preventDefault();
        if(pinned){ pinned=false; tip.style.opacity='0'; } else rich(el); } }
  });
  // make every term keyboard-focusable + labelled
  document.querySelectorAll('[data-term]').forEach(function(el){
    if(!el.getAttribute('data-kb')){ el.setAttribute('tabindex','0'); el.setAttribute('role','button'); el.setAttribute('data-kb','1'); }
    var e=G[el.getAttribute('data-term')];
    if(e && !el.getAttribute('aria-label') && el.tagName!=='BUTTON') el.setAttribute('aria-label', e.plain+'. '+(e.short||''));
  });

  // ── toolbar toggles ──
  function syncToggleLabels(){
    var lb=document.getElementById('qt-learn'); if(lb) lb.setAttribute('aria-pressed', document.body.getAttribute('data-learn')==='1'?'true':'false');
    var tb=document.getElementById('qt-theme'); if(tb) tb.textContent = document.documentElement.getAttribute('data-theme')==='light'?'Dark mode':'Light mode';
    var lbtxt=document.getElementById('qt-learn-state'); if(lbtxt) lbtxt.textContent = document.body.getAttribute('data-learn')==='1'?'on':'off';
  }
  var learnBtn=document.getElementById('qt-learn');
  if(learnBtn) learnBtn.addEventListener('click',function(){
    var on=document.body.getAttribute('data-learn')!=='1';
    if(on) document.body.setAttribute('data-learn','1'); else document.body.removeAttribute('data-learn');
    try{localStorage.setItem('qt_learn',on?'1':'0');}catch(e){} syncToggleLabels(); });
  var themeBtn=document.getElementById('qt-theme');
  if(themeBtn) themeBtn.addEventListener('click',function(){
    var light=document.documentElement.getAttribute('data-theme')!=='light';
    document.documentElement.setAttribute('data-theme',light?'light':'');
    try{localStorage.setItem('qt_theme',light?'light':'dark');}catch(e){} syncToggleLabels(); });
  var refreshBtn=document.getElementById('qt-refresh');
  if(refreshBtn) refreshBtn.addEventListener('click',function(){ try{location.reload();}catch(e){} });

  // ── glossary modal ──
  var glossEl=null;
  function renderGloss(q){ q=(q||'').toLowerCase(); var list=glossEl.querySelector('#qt-glist');
    var keys=Object.keys(G).sort(function(a,b){return (G[a].plain||'').localeCompare(G[b].plain||'');});
    var html='';
    keys.forEach(function(k){ var e=G[k]; var hay=((e.plain||'')+' '+(e.term||'')+' '+(e.short||'')).toLowerCase();
      if(q&&hay.indexOf(q)<0) return;
      html+='<div class="qt-gitem"><h3>'+esc(e.plain)+(e.term&&e.term!==e.plain?' <span class="qt-tip-t">('+esc(e.term)+')</span>':'')+'</h3><p>'+esc(e.long||e.short||'')+'</p>'+(e.example?'<p class="qt-gex"><b>Example:</b> '+esc(e.example)+'</p>':'')+'</div>'; });
    list.innerHTML=html||'<p class="muted">No terms match.</p>'; }
  function openGloss(){
    if(!glossEl){ glossEl=document.createElement('div'); glossEl.className='qt-gloss';
      glossEl.innerHTML='<div class="qt-gloss-box"><div class="qt-gloss-hd"><h2>Glossary</h2>'
        +'<input id="qt-gsearch" placeholder="Search terms…"><button id="qt-gx" aria-label="Close">✕</button></div><div id="qt-glist"></div></div>';
      document.body.appendChild(glossEl);
      glossEl.addEventListener('click',function(ev){ if(ev.target===glossEl) closeGloss(); });
      glossEl.querySelector('#qt-gx').addEventListener('click',closeGloss);
      var s=glossEl.querySelector('#qt-gsearch'); s.addEventListener('input',function(){ renderGloss(s.value); });
    }
    renderGloss(''); glossEl.style.display='flex';
    setTimeout(function(){ var s=glossEl.querySelector('#qt-gsearch'); if(s) s.focus(); },30);
  }
  function closeGloss(){ if(glossEl){ glossEl.style.display='none'; var gb=document.getElementById('qt-gloss'); if(gb) gb.focus(); } }
  var glossBtn=document.getElementById('qt-gloss'); if(glossBtn) glossBtn.addEventListener('click',openGloss);

  // ── client-side table sort ──
  function cellVal(tr,key){ var v=tr.getAttribute('data-'+key);
    if(v===null){ var i={t:0,sec:1}[key]; var td=tr.children[i==null?0:i]; return (td?td.textContent:'').trim().toLowerCase(); }
    var n=parseFloat(v); return isNaN(n)? v.toLowerCase() : n; }
  function applySort(table,key,dir){
    var tb=table.tBodies[0]; if(!tb) return;
    var rows=[].slice.call(tb.rows).filter(function(r){return !r.classList.contains('total');});
    rows.sort(function(a,b){ var av=cellVal(a,key),bv=cellVal(b,key);
      if(av<bv) return -1*dir; if(av>bv) return 1*dir; return 0; });
    rows.forEach(function(r){ tb.appendChild(r); });
    table.setAttribute('data-sort-key',key); table.setAttribute('data-sort-dir',dir);
    table.querySelectorAll('th[data-sort]').forEach(function(th){
      var arr=th.querySelector('.sarr'); if(arr) arr.remove();
      if(th.getAttribute('data-sort')===key){ var s=document.createElement('span'); s.className='sarr'; s.textContent=dir<0?' ▾':' ▴'; th.appendChild(s); } });
  }
  document.querySelectorAll('table.sortable').forEach(function(table){
    table.querySelectorAll('th[data-sort]').forEach(function(th){
      th.style.cursor='pointer'; th.setAttribute('role','button'); th.setAttribute('tabindex','0');
      function go(){ var key=th.getAttribute('data-sort'); var cur=table.getAttribute('data-sort-key');
        var dir=table.getAttribute('data-sort-dir')|0||1;
        dir = (cur===key)? -dir : (key==='t'||key==='sec'?1:-1);
        applySort(table,key,dir); }
      th.addEventListener('click',go);
      th.addEventListener('keydown',function(ev){ if(ev.key==='Enter'||ev.key===' '){ ev.preventDefault(); go(); } });
    });
    var k=table.getAttribute('data-sort-key'); if(k) applySort(table,k,parseInt(table.getAttribute('data-sort-dir'),10)||1);
  });

  // ── scroll-spy + back-to-top ──
  var links={}; document.querySelectorAll('#qtnav [data-jump]').forEach(function(a){ links[a.getAttribute('data-jump')]=a; });
  var ids=Object.keys(links);
  var secs=ids.map(function(id){ return {id:id, el:document.getElementById(id)}; }).filter(function(s){ return s.el; });
  var btn=document.createElement('button'); btn.className='qt-totop'; btn.textContent='↑';
  btn.setAttribute('aria-label','Back to top'); document.body.appendChild(btn);
  btn.addEventListener('click',function(){ try{scrollTo({top:0,behavior:'smooth'});}catch(e){scrollTo(0,0);} });
  var curId=null;
  function onScroll(){ var on=scrollY>500; btn.style.opacity=on?'1':'0'; btn.style.pointerEvents=on?'auto':'none';
    var cur=secs.length?secs[0].id:null;
    for(var i=0;i<secs.length;i++){ if(secs[i].el.getBoundingClientRect().top<=140) cur=secs[i].id; }
    if(cur&&cur!==curId){ curId=cur;
      ids.forEach(function(i){ links[i].classList.remove('active'); });
      if(links[cur]) links[cur].classList.add('active'); } }
  window.addEventListener('scroll',onScroll,{passive:true}); onScroll();

  // ── auto-refresh: pick up new scheduled runs without losing your place ──
  // The file is regenerated by every scheduled run; reload the tab every 15 min
  // to show it. Scroll position survives via sessionStorage; a reload is
  // skipped while a worked-example is pinned or the glossary is open, and
  // retried at the next tick. ("Check for data" remains the manual reload.)
  try{ var sy=sessionStorage.getItem('qt_scroll');
    if(sy!==null){ sessionStorage.removeItem('qt_scroll'); scrollTo(0, parseInt(sy,10)||0); } }catch(e){}
  setInterval(function(){
    if(pinned) return;
    // NB: openGloss/closeGloss toggle display via inline style — keep this
    // check in sync if the modal ever moves to class-based visibility.
    if(glossEl && glossEl.style.display==='flex') return;
    try{ sessionStorage.setItem('qt_scroll', String(scrollY)); }catch(e){}
    try{ location.reload(); }catch(e){}
  }, 15*60*1000);
})();"""


_STYLE = """
:root{
  --bg:#0a0d13; --surface:#111621; --surface2:#161c29; --inset:#0d121b; --raise:#1a2130;
  --border:#232c3b; --border-soft:#1a222f; --text:#e7ebf3; --text2:#c2cad9; --muted:#98a1b3; --muted2:#767f92;
  --pos:#3fc17d; --pos-dim:#14301f; --pos-line:#1f5738; --neg:#f0595c; --neg-dim:#33161a; --neg-line:#6b2529;
  --accent:#5b9dff; --warn:#e2b23f; --warn-dim:#2c2410;
  --shadow:0 1px 2px #0007, 0 10px 28px #0005;
  --mono:"IBM Plex Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  color-scheme:dark;
}
html[data-theme="light"]{
  --bg:#eef1f6; --surface:#ffffff; --surface2:#f3f6fa; --inset:#eef2f8; --raise:#f7f9fc;
  --border:#d7dee8; --border-soft:#e7ecf3; --text:#131a24; --text2:#3a4453; --muted:#5b6472;
  --muted2:#8791a1; --pos:#1a9d5c; --pos-dim:#dcf5e7; --pos-line:#a9e0c2; --neg:#d63b3f; --neg-dim:#fbe3e4; --neg-line:#f2b8ba;
  --accent:#2f6fd6; --warn:#b8860b; --warn-dim:#f7edd0;
  --shadow:0 1px 2px #0000001a, 0 8px 22px #0000001f; color-scheme:light;
}
*{box-sizing:border-box;}
html,body{margin:0;background:var(--bg);}
body{color:var(--text);font-family:var(--sans);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums;}
.shell{max-width:1160px;margin:0 auto;padding:26px 22px 80px;}
h2,h3{letter-spacing:-.01em;}
.pos{color:var(--pos);} .neg{color:var(--neg);} .warn{color:var(--warn);}
.muted{color:var(--muted);} .muted2{color:var(--muted2);} .text{color:var(--text);} .t2{color:var(--text2);}
.thin{font-weight:400;} .small{font-size:12.5px;} .right{text-align:right;}
a{color:var(--accent);}
@keyframes qtfade{from{opacity:0;transform:translateY(4px)}to{opacity:1}}
@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important;}}
[data-term]{border-bottom:1px dotted var(--muted2);cursor:help;}
[data-term]:focus-visible,button:focus-visible,[data-jump]:focus-visible,th[role=button]:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px;}
.term{border-bottom:1px dotted var(--muted2);cursor:help;}
button.i{all:unset;cursor:pointer;font-size:10px;width:15px;height:15px;line-height:15px;text-align:center;border-radius:50%;background:var(--inset);color:var(--accent);margin-left:4px;border:1px solid var(--border-soft);vertical-align:middle;}
.explain{display:none;}
body[data-learn="1"] .explain{display:block;font-size:11.5px;color:var(--muted);line-height:1.45;margin-top:8px;border-top:1px solid var(--border-soft);padding-top:7px;}
/* header */
header.top{display:flex;align-items:center;gap:14px;flex-wrap:wrap;border-bottom:1px solid var(--border-soft);padding-bottom:16px;}
.logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(145deg,var(--accent),#2f6fd6);display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-weight:600;color:#fff;}
.brand{font-weight:600;font-size:17px;line-height:1;}
.brand-sub{font-size:11px;color:var(--muted2);letter-spacing:.08em;text-transform:uppercase;margin-top:3px;}
.pill{display:inline-flex;align-items:center;gap:7px;padding:5px 11px 5px 9px;border-radius:999px;font-size:12.5px;font-weight:600;}
.pill .dot{width:7px;height:7px;border-radius:50%;}
.pill.pos{background:var(--pos-dim);border:1px solid var(--pos-line);color:var(--pos);} .pill.pos .dot{background:var(--pos);}
.pill.neg{background:var(--neg-dim);border:1px solid var(--neg-line);color:var(--neg);} .pill.neg .dot{background:var(--neg);}
.pill.warn{background:var(--warn-dim);border:1px solid var(--warn);color:var(--warn);} .pill.warn .dot{background:var(--warn);}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:14px;}
.ts{font-family:var(--mono);font-size:12px;color:var(--muted);}
.btn{font:inherit;font-size:12.5px;cursor:pointer;color:var(--text2);background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 12px;display:inline-flex;align-items:center;gap:7px;}
.btn:hover{border-color:var(--accent);color:var(--text);}
.btn .live{width:6px;height:6px;border-radius:50%;background:var(--pos);box-shadow:0 0 0 3px var(--pos-dim);}
/* toolbar */
.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:9px;margin:16px 0 20px;}
.chipbtn{font:inherit;font-size:13px;cursor:pointer;border-radius:999px;padding:7px 15px;background:var(--surface);border:1px solid var(--border);color:var(--text2);}
.chipbtn:hover{border-color:var(--accent);color:var(--text);}
#qt-learn[aria-pressed=true]{background:var(--pos-dim);border-color:var(--pos-line);color:var(--pos);}
.tb-hint{color:var(--muted2);font-size:12.5px;}
/* nav */
nav#qtnav{position:sticky;top:0;z-index:40;display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin:0 -22px 20px;padding:9px 22px;background:color-mix(in srgb, var(--bg) 84%, transparent);backdrop-filter:blur(8px);border-bottom:1px solid var(--border-soft);font-size:12.5px;}
@supports not (backdrop-filter: blur(8px)){nav#qtnav{background:var(--bg);}}
nav#qtnav a{color:var(--muted);text-decoration:none;padding:5px 11px;border-radius:999px;white-space:nowrap;}
nav#qtnav a.active{color:var(--accent);background:var(--surface);font-weight:600;}
/* verdict */
.verdict{position:relative;overflow:hidden;background:linear-gradient(160deg,var(--surface2),var(--surface));border:1px solid var(--border);border-radius:16px;padding:22px 24px;margin-bottom:20px;box-shadow:var(--shadow);scroll-margin-top:64px;}
.verdict-bar{position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--warn);}
.eyebrow{display:flex;align-items:center;gap:10px;font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--muted2);}
.eyebrow .rule,.zone-h .rule{height:1px;flex:1;background:var(--border-soft);}
.verdict-h{margin:6px 0 10px;font-size:22px;font-weight:600;line-height:1.25;}
.verdict-body{margin:0 0 16px;font-size:15px;line-height:1.6;color:var(--text2);max-width:72ch;}
.vchips{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;}
.vchip{background:var(--inset);border:1px solid var(--border-soft);border-radius:11px;padding:12px 14px;}
.vchip-hd{display:flex;align-items:center;gap:7px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted2);}
.vchip-hd .dot{width:8px;height:8px;border-radius:50%;background:var(--muted2);}
.vchip-hd .dot.pos{background:var(--pos);} .vchip-hd .dot.neg{background:var(--neg);} .vchip-hd .dot.warn{background:var(--warn);} .vchip-hd .dot.muted{background:var(--muted2);}
.vchip-val{font-size:15px;font-weight:600;margin-top:6px;}
.vchip-note{font-size:12px;color:var(--muted);margin-top:2px;}
/* automation banner */
.autobar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;border-radius:10px;padding:9px 14px;margin-bottom:22px;font-size:13px;}
.autobar span{color:var(--text2);opacity:.9;}
.autobar.pos{background:var(--pos-dim);border:1px solid var(--pos-line);color:var(--pos);}
.autobar.neg{background:var(--neg-dim);border:1px solid var(--neg-line);color:var(--neg);}
.autobar.warn{background:var(--warn-dim);border:1px solid var(--warn);color:var(--warn);}
/* cards + zones */
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:var(--shadow);}
.card.scroll-x{overflow-x:auto;}
.card h3{margin:0 0 4px;font-size:16px;font-weight:600;}
.card-hd{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px;}
.hint{font-size:12px;color:var(--muted2);}
.zone-h{display:flex;align-items:center;gap:10px;margin:4px 0 14px;font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted2);scroll-margin-top:60px;}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:8px;}
.callout{margin:12px 0 0;font-size:12.5px;color:var(--muted);background:var(--inset);border-radius:8px;padding:9px 11px;}
.empty{color:var(--muted);font-size:14px;padding:8px 0;}
.asof{font-size:12px;color:var(--muted2);font-weight:400;}
/* equity */
.chart{width:100%;height:auto;font-family:var(--mono);}
.eq-hd{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:6px;}
.eq-legend{display:flex;gap:16px;font-size:12.5px;}
.eq-legend span{display:inline-flex;align-items:center;gap:6px;color:var(--text2);}
.lg-strat{width:16px;height:3px;border-radius:2px;background:var(--accent);}
.lg-spy{width:16px;height:0;border-top:2px dashed var(--muted);}
/* KPIs */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:13px;margin-bottom:14px;}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:13px;padding:15px 16px;box-shadow:var(--shadow);}
.kpi-label{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);}
.kpi-val{font-size:24px;font-weight:600;margin-top:8px;}
.kpi-sub{font-size:11px;color:var(--muted2);margin-top:3px;}
/* tables */
.tbl{width:100%;border-collapse:collapse;font-size:14px;}
.tbl th{text-align:left;font-weight:600;padding:6px 8px;border-bottom:1px solid var(--border-soft);color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.03em;}
.tbl th.right{text-align:right;}
.tbl td{padding:8px;border-bottom:1px solid var(--border-soft);}
.tbl td.right{text-align:right;}
.tbl tbody tr:hover{background:var(--raise);}
.tbl tfoot td{padding:10px 8px;font-weight:600;border-top:1px solid var(--border);}
.coname{color:var(--muted2);font-size:12px;font-weight:400;}
.pnlpct{display:flex;align-items:center;justify-content:flex-end;gap:9px;}
.pbar{position:relative;width:70px;height:8px;background:var(--inset);border-radius:4px;overflow:hidden;flex:0 0 auto;}
.pbar i{position:absolute;top:0;height:100%;border-radius:4px;}
.pbar .pbar-mid{left:50%;top:0;bottom:0;width:1px;background:var(--border);}
.sarr{color:var(--accent);}
/* stats grid */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:11px;}
.stats.two{grid-template-columns:1fr 1fr;}
.stat{background:var(--inset);border:1px solid var(--border-soft);border-radius:11px;padding:12px 13px;}
.stat-v{font-size:21px;font-weight:600;}
.stat-l{font-size:11.5px;color:var(--muted);margin-top:3px;}
/* picks */
.pick{padding:12px 0;border-bottom:1px solid var(--border-soft);}
.pick-hd{display:flex;align-items:baseline;gap:9px;}
.pick-score{margin-left:auto;font-weight:600;font-size:15px;}
.sigs{display:grid;gap:4px;margin-top:9px;}
.sigrow{display:grid;grid-template-columns:118px 1fr 34px;align-items:center;gap:9px;}
.siglbl{color:var(--muted2);font-size:11px;border-bottom:1px dotted var(--border);cursor:help;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.sigbar{background:var(--inset);border-radius:4px;height:7px;overflow:hidden;}
.sigbar i{display:block;height:100%;border-radius:4px;}
.sigval{color:var(--muted);font-size:11px;text-align:right;}
/* decisions */
.feed{list-style:none;margin:0;padding:0;}
.feed li{display:flex;gap:11px;padding:10px 0;border-bottom:1px solid var(--border-soft);font-size:13.5px;}
.tag{flex:0 0 auto;font-size:10px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;padding:2px 7px;border-radius:5px;height:fit-content;margin-top:2px;}
.tag.pos{background:var(--pos-dim);color:var(--pos);} .tag.warn{background:var(--warn-dim);color:var(--warn);} .tag.muted{background:var(--inset);color:var(--muted);}
.dec-text{color:var(--text2);line-height:1.5;}
/* diverging bars */
.dbars{display:grid;gap:9px;}
.db-row{display:grid;grid-template-columns:130px 1fr;align-items:center;gap:10px;}
.db-lbl{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.db-track{position:relative;height:11px;background:var(--inset);border-radius:5px;}
.db-zero{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--border);}
.db-fill{position:absolute;top:0;height:100%;border-radius:5px;}
.db-val{font-size:11px;color:var(--muted);margin-top:3px;}
/* donut */
.donut-wrap{display:flex;align-items:center;gap:20px;flex-wrap:wrap;}
.donut-lg{display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;font-size:12px;color:var(--muted);flex:1;min-width:200px;}
.lg{display:flex;align-items:center;gap:7px;} .lg i{width:9px;height:9px;border-radius:2px;flex:0 0 auto;} .lg b{color:var(--text);margin-left:auto;}
/* sector picks */
.sp-block{margin-top:14px;border-top:1px solid var(--border-soft);padding-top:12px;}
.sp-hd{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted2);margin-bottom:8px;}
.sp-list{display:grid;gap:5px;max-height:180px;overflow:auto;}
.sp-row{display:flex;align-items:center;gap:8px;font-size:12.5px;}
.sp-sec{color:var(--muted);width:150px;flex:0 0 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.sp-passed{width:34px;text-align:right;}
.copilot p{color:var(--text2);line-height:1.55;font-size:14px;}
/* tooltip + modal + totop */
.qt-tip{position:fixed;z-index:9000;max-width:320px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;box-shadow:0 10px 34px #000a;font:14px/1.5 var(--sans);color:var(--text);pointer-events:none;opacity:0;transition:opacity .1s;}
.qt-tip-h{font-weight:600;margin-bottom:6px;} .qt-tip-t{color:var(--muted);font-weight:400;}
.qt-tip-ex{margin-top:8px;padding:8px 10px;background:var(--inset);border-radius:7px;border-left:3px solid var(--pos);color:var(--text2);font-size:13px;}
.qt-tip-th{margin-top:8px;color:var(--muted);font-style:italic;font-size:13px;}
.qt-gloss{position:fixed;inset:0;z-index:9500;background:#010409d0;display:none;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto;}
.qt-gloss-box{max-width:760px;width:100%;background:var(--bg);border:1px solid var(--border);border-radius:16px;padding:22px 24px;}
.qt-gloss-hd{display:flex;align-items:center;gap:12px;margin-bottom:14px;}
.qt-gloss-hd h2{margin:0;font-size:18px;color:var(--text);}
#qt-gsearch{flex:1;font:14px var(--sans);background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 12px;color:var(--text);}
#qt-gx{all:unset;cursor:pointer;color:var(--muted);font-size:22px;padding:0 6px;}
.qt-gitem{padding:12px 0;border-bottom:1px solid var(--border-soft);}
.qt-gitem h3{margin:0 0 4px;font-size:14px;color:var(--text);}
.qt-gitem p{margin:4px 0 0;font-size:13px;color:var(--text2);line-height:1.5;}
.qt-gex{color:var(--muted)!important;font-size:12.5px!important;}
.qt-totop{position:fixed;right:20px;bottom:20px;z-index:8000;width:42px;height:42px;border-radius:50%;border:1px solid var(--border);background:var(--surface);color:var(--text);font-size:18px;cursor:pointer;box-shadow:0 6px 20px #0006;opacity:0;pointer-events:none;transition:opacity .15s;}
footer{color:var(--muted2);font-size:12px;margin-top:28px;border-top:1px solid var(--border-soft);padding-top:14px;line-height:1.6;}
@media (max-width:520px){.shell{padding:18px 13px 60px;} .verdict-h{font-size:19px;} h1{font-size:24px;}}
"""


def dashboard_html(data: dict) -> str:
    regime = data.get("regime") or {}
    snaps = data.get("snapshots") or []
    picks = data.get("top_picks") or []
    sectors = data.get("sectors") or {}
    summary = data.get("summary") or {}
    names = data.get("names") or {}
    as_of = data.get("as_of", "")

    updated = (f'Updated {str(as_of)[:16].replace("T", " ")} UTC' if as_of
               else "Updated —")

    # zone: Today (screener + picks/decisions) — always has picks/decisions cards
    today_grid = (f'<div class="grid2">{_picks_section(picks, sectors, names)}'
                  f'{_decisions_section(data.get("decisions") or [])}</div>')
    screen_zone = (_zone_header("screen", "Today's screen")
                   + _screener_stats(summary) + today_grid)
    # zone: Is it working? — fleet leaderboard leads; drop the header if empty
    fleet = _fleet_section(data.get("fleet") or [])
    sc = _scorecard_section(data.get("scorecard"))
    sl = _signal_lab_section(data.get("signal_lab") or {})
    working_zone = ((_zone_header("working", "Is it working?") + fleet
                     + f'<div class="grid2">{sc}{sl}</div>')
                    if (fleet or sc or sl) else "")
    # zone: Under the hood — donut + vetoes, then health/sentiment/tournament/copilot
    donut = _sector_donut(sectors)
    vet = _vetoes_section(sectors, summary, names)
    hud_grid = f'<div class="grid2">{donut}{vet}</div>' if (donut or vet) else ""
    extras = (_company_health_section(data.get("health"), names)
              + _sentiment_section(data.get("sentiment"), names)
              + _tournament_section(data.get("tournament") or {})
              + _copilot_section(data.get("copilot") or {}))
    hud_inner = hud_grid + extras
    hud_zone = (_zone_header("hud", "Under the hood") + hud_inner) if hud_inner else ""

    body = (
        f'<header class="top">'
        f'<div style="display:flex;align-items:center;gap:11px;"><div class="logo">Q</div>'
        f'<div><div class="brand">Quant Tracker</div>'
        f'<div class="brand-sub">Paper-trading research</div></div></div>'
        f'{_regime_pill(regime)}'
        f'<div class="hdr-right"><span class="ts">{_esc(updated)}</span>'
        f'<button id="qt-refresh" type="button" class="btn"><span class="live"></span>'
        f'Check for data</button></div></header>'

        f'<div class="toolbar">'
        f'<button id="qt-learn" type="button" class="chipbtn" aria-pressed="false">◎ Learn mode '
        f'<span id="qt-learn-state">off</span></button>'
        f'<button id="qt-gloss" type="button" class="chipbtn">Glossary</button>'
        f'<button id="qt-theme" type="button" class="chipbtn">Light mode</button>'
        f'<span class="tb-hint">New here? Turn on <b class="muted">Learn mode</b>, or hover any '
        f'<span style="border-bottom:1px dotted var(--muted2)">underlined term</span>.</span></div>'

        f'<nav id="qtnav">'
        f'<a href="#verdict" data-jump="verdict">Today\'s read</a>'
        f'<a href="#equity" data-jump="equity">Equity</a>'
        f'<a href="#money" data-jump="money">My money</a>'
        f'<a href="#positions" data-jump="positions">Positions</a>'
        f'<a href="#screen" data-jump="screen">Screen</a>'
        f'<a href="#working" data-jump="working">Is it working?</a>'
        f'<a href="#hud" data-jump="hud">Under the hood</a></nav>'

        f'{_verdict_section(data)}'
        f'{_auto_banner(data.get("last_run") or {})}'

        f'<section id="equity" class="card" style="scroll-margin-top:64px">'
        f'<div class="eq-hd"><div><h3>Strategy vs the market</h3>'
        f'<p class="muted small">Paper account value over time vs. buying '
        f'{_dterm("spy", "SPY")} — both {_dterm("equity_curve", "indexed to 100")} at start.</p></div>'
        f'<div class="eq-legend"><span><span class="lg-strat"></span>Strategy</span>'
        f'<span class="muted"><span class="lg-spy"></span>SPY</span></div></div>'
        f'{_svg_equity(snaps)}</section>'

        f'{_zone_header("money", "My money")}{_kpis(data)}'
        f'{_positions_section(data.get("positions") or [], names)}'

        f'{screen_zone}{working_zone}{hud_zone}'

        f'<footer>Auto-generated by quant-tracker — regenerated each run. Paper money, research '
        f'only — <b class="muted">not financial advice</b>. Auto-refreshes every 15 min '
        f'(keeps your scroll position); <b>Check for data</b> reloads now.</footer>'
    )

    gloss_json = _gloss.as_json().replace("</", "<\\/")
    script_block = "<script>\n" + _PAGE_JS.replace("__GLOSSARY_JSON__", gloss_json) + "\n</script>"

    return (
        '<!DOCTYPE html>\n<html lang="en"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>Quant Tracker — Dashboard</title>\n'
        f'<style>{_STYLE}</style>\n'
        f'</head>\n<body>\n<div class="shell">\n{body}\n</div>\n{script_block}\n</body></html>'
    )


__all__ = ["dashboard_html"]
