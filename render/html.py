"""render/html.py — PURE builder for a comprehensive, self-contained dashboard.

Emits a single ``Dashboard.html`` (inline CSS + inline SVG charts, NO external
libraries or CDNs) so it opens in any browser, offline, and syncs via Drive.
Like the Markdown notes it is a regenerated render artifact — never hand-edited.
``render.build`` does the IO and calls ``dashboard_html``.

Every value that originates from data (tickers, regime, prose) is HTML-escaped
before it reaches the page (Insight B13 — content is data, never markup).
"""
from __future__ import annotations

import html as _html
import re

from render import glossary as _gloss
from render.markdown import money, num, pct

_REFRESH_SECONDS = 900  # an open tab reloads itself every 15 min
# The signals the engine actually emits per pick (== EXPECTED_SIGNAL_KEYS).
# Momentum is measured-only (held), so it is NOT a per-pick bar — its explainer
# lives in the Signal-Lab IC table, where it IS measured (see _signal_lab_section).
_SIGNALS = ("arima", "kalman", "garch", "monte_carlo", "sharpe")


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _bold(s: str) -> str:
    """Escape, then turn markdown **bold** into <strong> (decisions use it)."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _esc(s))


def _regime_color(label: str) -> str:
    return {"bull": "#3fb950", "bear": "#f85149"}.get(
        str(label).lower(), "#d29922")  # sideways / unknown → amber


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


def _term(key: str) -> str:
    """Plain label (+ real term) + info button + a Learn-mode inline note."""
    e = _gloss.GLOSSARY.get(key)
    if not e:
        return _esc(key)
    inner = _esc(e["plain"])
    if e.get("term") and e["term"] != e["plain"]:
        inner += f' <span class="tterm">({_esc(e["term"])})</span>'
    return (f'<span class="tlbl">{inner}{_ibtn(key)}</span>'
            f'<span class="explain">{_esc(e.get("short", ""))}</span>')


def _th(key: str, text: str | None = None) -> str:
    """A table header cell whose title is a glossary term + info button."""
    label = text if text is not None else _gloss.GLOSSARY.get(key, {}).get("plain", key)
    return f'<th>{_esc(label)}{_ibtn(key)}</th>'


def _title(emoji: str, text: str, key: str = "", sub_key: str = "") -> str:
    """Section <h2> inner: emoji + plain title + section info button + subtitle."""
    sub = _gloss.short(sub_key or key)
    sub_html = f'<span class="h2sub">{_esc(sub)}</span>' if sub else ""
    return f'{emoji} {_esc(text)}{_ibtn(key) if key else ""}{sub_html}'


def _asof(iso) -> str:
    """A muted 'as of <date time>' stamp for cached/stale-able sections."""
    if not iso:
        return ""
    return f' <span class="asof">· as of {_esc(str(iso)[:16].replace("T", " "))}</span>'


# GICS classifies aerospace & defense as a sub-industry of Industrials, so there's
# no "Defense" sector. For the dashboard view we break these names out into their
# own group — DISPLAY ONLY; the engine's stored sector + diversification stay GICS.
DEFENSE_TICKERS = frozenset({
    "GD", "RTX", "LMT", "NOC", "BA", "GE", "LHX", "HII", "TDG", "AXON", "LDOS", "HWM",
})


def _display_sector(ticker, sector) -> str:
    """Dashboard sector for grouping — 'Defense' for A&D names, else the GICS sector."""
    if ticker and str(ticker).upper() in DEFENSE_TICKERS:
        return "Defense"
    return sector or "Other"


def _ticker(sym, names: dict | None = None) -> str:
    """'<TICKER> · Company Name' — the name is muted and omitted if unknown."""
    nm = (names or {}).get(str(sym).upper()) if sym else None
    co = f' <span class="coname">{_esc(nm)}</span>' if nm else ""
    return f'<strong>{_esc(sym)}</strong>{co}'


def _kpi(label: str, value: str, sub: str = "", tone: str = "", key: str = "",
         big: bool = False) -> str:
    cls = (" big" if big else "") + (f" {tone}" if tone else "")
    sub_html = f'<div class="kpi-sub">{_esc(sub)}</div>' if sub else ""
    label_html = _term(key) if key else _esc(label)
    return (f'<div class="kpi{cls}"><div class="kpi-label">{label_html}</div>'
            f'<div class="kpi-val">{_esc(value)}</div>{sub_html}</div>')


def _headline(rlabel: str, total_value, pnl, n_pos: int) -> str:
    """One plain-English glance line: market mood + portfolio + P&L + holdings."""
    mood = {"bull": "Calm, rising", "bear": "Falling",
            "sideways": "Choppy"}.get(str(rlabel).lower(), str(rlabel).title())
    val = money(total_value) if total_value else "$10,000"
    base = (total_value - pnl) if (total_value and pnl is not None) else 10000
    pct_s = f" ({'+' if (pnl or 0) >= 0 else ''}{(pnl / base * 100):.1f}%)" if (pnl and base) else ""
    tone = "pos" if (pnl or 0) >= 0 else "neg"
    pnl_s = (f'<b class="{tone}">{money(pnl)}{pct_s}</b>') if pnl is not None else "<b>$0.00</b>"
    return (f'<div class="headline"><span class="hl-mood" '
            f'style="color:{_regime_color(rlabel)}">{_esc(mood)} market</span>'
            f' · paper portfolio <b>{_esc(val)}</b> · P&amp;L {pnl_s}'
            f' · {_esc(n_pos)} holdings{_ibtn("regime")}</div>')


def _equity_caption(snaps: list[dict]) -> str:
    pts = [(s.get("total_value"), s.get("benchmark_value")) for s in (snaps or [])
           if s.get("total_value")]
    if len(pts) < 2:
        return ""
    v0, vN = float(pts[0][0]) or 1.0, float(pts[-1][0])
    strat = (vN / v0 - 1.0) * 100.0
    spy = ""
    if pts[0][1] and pts[-1][1]:
        s = (float(pts[-1][1]) / float(pts[0][1]) - 1.0) * 100.0
        spy = f" vs SPY {'+' if s >= 0 else ''}{s:.1f}%"
    sign = "+" if strat >= 0 else ""
    return (f'<p class="muted cap">Strategy <b>{sign}{strat:.1f}%</b>{spy} '
            f'over {len(pts)} snapshots (both indexed to 100 at start).</p>')


def _zone(zid: str, emoji: str, title: str, inner: str) -> str:
    """A titled, anchorable group of cards (feeds the in-page section nav).
    Renders nothing when the zone has no content (avoids bare headers)."""
    if not (inner or "").strip():
        return ""
    return (f'<section class="zone" id="{zid}">'
            f'<h2 class="zone-h">{emoji} {_esc(title)}</h2>{inner}</section>')


def _signal_lookup(sectors: dict) -> dict:
    out: dict = {}
    for stocks in (sectors or {}).values():
        for s in (stocks or []):
            out[s.get("ticker")] = s
    return out


def _signal_bars(scores: dict) -> str:
    if not scores:
        return ""
    rows = []
    for sig in _SIGNALS:
        v = scores.get(sig)
        if v is None:
            continue
        wpct = max(0.0, min(1.0, float(v))) * 100.0
        plain = _gloss.GLOSSARY.get(sig, {}).get("plain", sig.replace("_", " "))
        rows.append(
            f'<div class="sigrow"><span class="siglbl">{_esc(plain)}{_ibtn(sig)}</span>'
            f'<span class="sigbar"><i style="width:{wpct:.0f}%"></i></span>'
            f'<span class="sigval">{num(v, 2)}</span></div>')
    return f'<div class="sigs">{"".join(rows)}</div>'


def _svg_equity(snaps: list[dict], w: int = 760, h: int = 240) -> str:
    pts = [(s.get("total_value"), s.get("benchmark_value")) for s in snaps
           if s.get("total_value")]
    if len(pts) < 2:
        return ('<div class="empty">The equity curve builds after the first '
                'monthly buy and a few daily snapshots accumulate.</div>')
    v0 = float(pts[0][0]) or 1.0
    b0 = float(pts[0][1]) if pts[0][1] else None
    strat = [float(v) / v0 * 100.0 for v, _ in pts]
    bench = ([float(b) / b0 * 100.0 if b else None for _, b in pts]
             if b0 else [None] * len(pts))
    ys = [y for y in strat + [b for b in bench if b is not None]]
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = 28
    iw, ih = w - 2 * pad, h - 2 * pad

    def xy(i, val):
        x = pad + (iw * i / (len(pts) - 1))
        y = pad + ih - (ih * (val - lo) / (hi - lo))
        return f"{x:.1f},{y:.1f}"

    strat_poly = " ".join(xy(i, v) for i, v in enumerate(strat))
    bench_poly = " ".join(xy(i, b) for i, b in enumerate(bench) if b is not None)
    base_y = pad + ih - (ih * (100.0 - lo) / (hi - lo))
    bench_line = (f'<polyline points="{bench_poly}" fill="none" class="eq-bench" '
                  f'stroke-width="2" stroke-dasharray="4 4"/>' if bench_poly else "")
    # Per-point hover targets: a native <title> tooltip (works with zero JS,
    # offline) + a dot the JS can light up. Shows the indexed strategy vs SPY value.
    dots = []
    slice_w = iw / (len(pts) - 1)
    for i, v in enumerate(strat):
        cx = pad + iw * i / (len(pts) - 1)
        b = bench[i]
        tip = (f"Point {i + 1}: strategy {v:.1f}"
               + (f" · SPY {b:.1f}" if b is not None else "")
               + f" · {'+' if v >= 100 else ''}{v - 100:.1f} vs start")
        dots.append(
            f'<g class="eqpt"><rect x="{cx - slice_w / 2:.1f}" y="{pad}" '
            f'width="{slice_w:.1f}" height="{ih}" fill="transparent">'
            f'<title>{_esc(tip)}</title></rect>'
            f'<circle cx="{cx:.1f}" cy="{xy(i, v).split(",")[1]}" r="3" '
            f'class="eqdot"/></g>')
    return f'''<svg viewBox="0 0 {w} {h}" class="chart" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Equity curve — strategy vs SPY, indexed to 100 at start. Hover a point for values.">
  <line x1="{pad}" y1="{base_y:.1f}" x2="{w - pad}" y2="{base_y:.1f}" class="eq-base" stroke-width="1"/>
  {bench_line}
  <polyline points="{strat_poly}" fill="none" class="eq-strat" stroke-width="2.5"/>
  {"".join(dots)}
  <text x="{pad}" y="{pad - 10}" class="axis">indexed to 100 at start</text>
  <text x="{w - pad}" y="{pad - 10}" class="axis" text-anchor="end">strategy ● &nbsp; SPY ┄</text>
</svg>'''


# ── visual encoding: hand-rolled bars/donut (no libraries) ───────────────────
# HTML bars use CSS var() so they theme with light/dark; the donut uses inline
# SVG with categorical hues (readable on both themes).
def _diverging_bars(rows: list[tuple]) -> str:
    """Zero-centred bars: negative grows left (red), positive right (green).
    rows = [(label_html, value, right_html)]; value scaled by the max magnitude."""
    rows = [r for r in rows if r[1] is not None]
    if not rows:
        return ""
    mx = max(abs(v) for _, v, _ in rows) or 1.0
    out = []
    for lbl, v, right in rows:
        w = min(50.0, abs(v) / mx * 50.0)
        fill = (f'<i class="db-fill pos" style="left:50%;width:{w:.1f}%"></i>' if v >= 0
                else f'<i class="db-fill neg" style="right:50%;width:{w:.1f}%"></i>')
        out.append(f'<div class="db-row"><span class="db-lbl">{lbl}</span>'
                   f'<span class="db-track"><i class="db-zero"></i>{fill}</span>'
                   f'<span class="db-val">{right}</span></div>')
    return f'<div class="dbars">{"".join(out)}</div>'


def _hbars(rows: list[tuple]) -> str:
    """Left-anchored horizontal bars. rows = [(label_html, value, text, tone)]."""
    rows = [r for r in rows if r[1] is not None]
    if not rows:
        return ""
    mx = max(abs(v) for _, v, _, _ in rows) or 1.0
    out = []
    for lbl, v, txt, tone in rows:
        w = min(100.0, abs(v) / mx * 100.0)
        out.append(f'<div class="hb-row"><span class="hb-lbl">{lbl}</span>'
                   f'<span class="hb-track"><i class="hb-fill {tone}" '
                   f'style="width:{w:.1f}%"></i></span>'
                   f'<span class="hb-val {tone}">{_esc(txt)}</span></div>')
    return f'<div class="hbars">{"".join(out)}</div>'


_DONUT_HUES = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#a371f7",
               "#39c5cf", "#ec6547", "#db61a2", "#8b949e", "#e3b341"]


def _svg_donut(parts: list[tuple], size: int = 150) -> str:
    """Ring chart from [(label, value)] using proportional stroke-dasharray arcs."""
    parts = [(l, float(v)) for l, v in parts if v and float(v) > 0]
    if not parts:
        return ""
    tot = sum(v for _, v in parts)
    r = size / 2.0
    sw = size * 0.17
    rad = r - sw / 2.0
    off, segs, legend = 0.0, [], []
    for i, (l, v) in enumerate(parts):
        frac = v / tot * 100.0
        c = _DONUT_HUES[i % len(_DONUT_HUES)]
        segs.append(
            f'<circle cx="{r}" cy="{r}" r="{rad:.1f}" fill="none" stroke="{c}" '
            f'stroke-width="{sw:.1f}" pathLength="100" stroke-dasharray="{frac:.2f} 100" '
            f'stroke-dashoffset="{-off:.2f}" transform="rotate(-90 {r} {r})"/>')
        legend.append(f'<span class="lg"><i style="background:{c}"></i>'
                      f'{_esc(str(l).replace("_", " "))} <b>{frac:.0f}%</b></span>')
        off += frac
    return (f'<div class="donut-wrap"><svg viewBox="0 0 {size} {size}" class="donut" '
            f'width="{size}" height="{size}" role="img" aria-label="Allocation by sector">'
            f'{"".join(segs)}</svg><div class="donut-lg">{"".join(legend)}</div></div>')


def _card(title: str, inner: str, extra_cls: str = "", cid: str = "") -> str:
    idattr = f' id="{cid}"' if cid else ""
    return f'<section class="card {extra_cls}"{idattr}><h2>{title}</h2>{inner}</section>'


def _chip(value, label: str, key: str = "", neg: bool = False) -> str:
    cls = " neg" if neg else ""
    lbl = (f'{_esc(label)}{_ibtn(key)}') if key else _esc(label)
    return f'<div class="chip{cls}"><span>{_esc(value)}</span>{lbl}</div>'


def _screener_stats(summary: dict) -> str:
    if not summary:
        return ""
    chips = [
        (summary.get("total_screened"), "Stocks looked at", "universe"),
        (summary.get("total_passed_veto"), "Passed safety checks", "veto"),
        (f'{summary.get("veto_rate_pct", 0)}%', "Screened out", "veto_rate"),
        (summary.get("total_skipped"), "Skipped (old data)", "delisted_stale"),
        (summary.get("total_failed"), "Failed checks", "veto"),
        (summary.get("total_sectors"), "Industry groups", "sector"),
    ]
    inner = "".join(_chip(v, lbl, key) for v, lbl, key in chips if v is not None)
    return _card(_title("\U0001F50D", "Screener", "universe"),
                 f'<div class="chips">{inner}</div>')


def _picks_section(picks: list[dict], sectors: dict, names: dict | None = None) -> str:
    if not picks:
        return _card(_title("\U0001F3AF", "Top picks", "top_overall"),
                     '<div class="empty">No screener run yet.</div>')
    look = _signal_lookup(sectors)
    rows = []
    for p in picks[:8]:
        s = look.get(p.get("ticker"), {})
        rows.append(
            f'<div class="pick"><div class="pick-hd">'
            f'{_ticker(p.get("ticker"), names)}'
            f'<span class="pick-sec">{_esc(p.get("sector", ""))}</span>'
            f'<span class="pick-score" title="overall score">{num(p.get("composite_score", p.get("score")), 3)}'
            f'{_ibtn("composite")}</span>'
            f'</div>{_signal_bars(s.get("signal_scores", {}))}</div>')
    return _card(_title("\U0001F3AF", "Top picks", "composite",
                        sub_key="composite"), "".join(rows))


def _sector_table(sectors: dict, names: dict | None = None) -> str:
    if not sectors:
        return ""
    # Break the A&D names out of Industrials into a "Defense" group (display only).
    remapped: dict = {}
    for name, stocks in sectors.items():
        for s in (stocks or []):
            remapped.setdefault(_display_sector(s.get("ticker"), name), []).append(s)
    sectors = remapped
    rows, donut_parts = [], []
    for name, stocks in sectors.items():
        stocks = stocks or []
        passed = sum(1 for s in stocks if s.get("passed_veto"))
        if passed:
            donut_parts.append((name, passed))
        top = next((s for s in stocks if s.get("rank") == 1), stocks[0] if stocks else {})
        rows.append(
            f"<tr><td>{_esc(name.replace('_', ' '))}</td>"
            f"<td>{_ticker(top.get('ticker', '—'), names)}</td>"
            f"<td>{num(top.get('composite_score'), 3) if top.get('composite_score') is not None else '—'}</td>"
            f"<td>{passed}/{len(stocks)}</td></tr>")
    donut = (f'<p class="muted">Candidates passing the safety checks {_ibtn("veto")}, '
             f'by sector:</p>{_svg_donut(donut_parts)}' if donut_parts else "")
    body = (f'<table class="tbl"><thead><tr>{_th("sector", "Sector")}'
            f'<th>Top pick</th>{_th("composite", "Score")}'
            f'{_th("veto", "Passed")}</tr></thead><tbody>{"".join(rows)}</tbody></table>')
    return _card(_title("\U0001F3E2", "By sector", "sector"), donut + body)


def _veto_key(reason: str) -> str:
    """Map a raw veto reason to its glossary key (default: the generic 'veto')."""
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


def _vetoes_section(sectors: dict, summary: dict) -> str:
    vetoed = [(s.get("ticker"), s.get("veto_reason"))
              for stocks in (sectors or {}).values() for s in (stocks or [])
              if not s.get("passed_veto") and s.get("veto_reason")]
    counts: dict = {}
    for _, reason in vetoed:
        counts[reason] = counts.get(reason, 0) + 1
    if not vetoed and not (summary or {}).get("total_skipped"):
        return ""
    chips = "".join(_chip(n, r, _veto_key(r), neg=True)
                    for r, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    skip = (summary or {}).get("total_skipped")
    skip_chip = _chip(skip, "delisted/stale skip", "delisted_stale") if skip else ""
    sample = ", ".join(_esc(t) for t, _ in vetoed[:18])
    sample_html = f'<p class="muted">Vetoed: {sample}</p>' if sample else ""
    return _card(_title("\U0001F6AB", "Vetoes & skips", "veto"),
                 f'<div class="chips">{chips}{skip_chip}</div>{sample_html}')


def _positions_section(positions: list[dict], names: dict | None = None) -> str:
    if not positions:
        inner = ('<div class="empty">No open positions yet — the first paper buys '
                 'land in the monthly 1st–5th window.</div>')
        return _card(_title("\U0001F4BC", "Positions", "positions"), inner)
    # Compute per-ticker value + P&L (not stored columns), then group by sector
    # with per-sector subtotals + % of portfolio + a grand total.
    by_sector: dict = {}
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
        by_sector.setdefault(_display_sector(p.get("ticker"), p.get("sector")), []).append(
            (p, sh, cost, price, mv, upnl))
    total_mv = sum(mv or 0 for items in by_sector.values() for *_, mv, _ in items)

    def _pnl_pct(pnl, basis):
        return (pnl / basis) if (pnl is not None and basis) else None

    rows = []
    for sector in sorted(by_sector, key=lambda s: -sum((r[4] or 0) for r in by_sector[s])):
        items = sorted(by_sector[sector], key=lambda t: (t[5] is None, -(t[5] or 0)))
        for p, sh, cost, price, mv, upnl in items:
            tone = "" if upnl is None else ("pos" if upnl >= 0 else "neg")
            share = (mv / total_mv) if (mv and total_mv) else None
            rows.append(
                f"<tr><td>{_ticker(p.get('ticker'), names)}</td>"
                f"<td>{num(sh, 2)}</td><td>{money(cost)}</td><td>{money(price)}</td>"
                f"<td>{money(mv) if mv is not None else '—'}</td>"
                f"<td class='{tone}'>{money(upnl) if upnl is not None else '—'}</td>"
                f"<td class='{tone}'>{pct(_pnl_pct(upnl, cost*sh)) if upnl is not None else '—'}</td>"
                f"<td>{pct(share) if share is not None else '—'}</td></tr>")
        s_mv = sum(r[4] or 0 for r in items)
        s_pnl = sum(r[5] or 0 for r in items)
        s_basis = sum(r[2] * r[1] for r in items)
        st = "pos" if s_pnl >= 0 else "neg"
        rows.append(
            f"<tr class='subtotal'><td colspan='4'>{_esc(sector.replace('_',' '))} "
            f"<span class='muted'>· {len(items)}</span></td>"
            f"<td>{money(s_mv)}</td><td class='{st}'>{money(s_pnl)}</td>"
            f"<td class='{st}'>{pct(_pnl_pct(s_pnl, s_basis))}</td>"
            f"<td>{pct(s_mv/total_mv) if total_mv else '—'}</td></tr>")
    g_pnl = sum(r[5] or 0 for items in by_sector.values() for r in items)
    g_basis = sum(r[2] * r[1] for items in by_sector.values() for r in items)
    gt = "pos" if g_pnl >= 0 else "neg"
    rows.append(
        f"<tr class='grand'><td colspan='4'>TOTAL</td><td>{money(total_mv)}</td>"
        f"<td class='{gt}'>{money(g_pnl)}</td>"
        f"<td class='{gt}'>{pct(_pnl_pct(g_pnl, g_basis))}</td><td>100%</td></tr>")
    body = (f'<table class="tbl"><thead><tr><th>Ticker / Sector</th>{_th("shares", "Shares")}'
            f'{_th("cost_basis", "Cost")}<th>Price</th>{_th("market_value", "Value")}'
            f'{_th("unrealized_pnl", "P&L $")}{_th("unrealized_pnl", "P&L %")}'
            f'<th>% Port</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    return _card(_title("\U0001F4BC", "Positions by sector", "positions"), body)


def _sentiment_section(rows: list[dict], names: dict | None = None) -> str:
    rows = [r for r in (rows or []) if r.get("label") and r.get("label") != "UNAVAILABLE"]
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: (r.get("sentiment_score") if r.get("sentiment_score")
                                       is not None else 0))[:10]
    tr = "".join(
        f"<tr><td>{_ticker(r.get('ticker'), names)}</td>"
        f"<td>{num(r.get('sentiment_score'), 3)}</td><td>{_esc(r.get('label'))}</td>"
        f"<td>{_esc(r.get('n_headlines'))}</td></tr>" for r in rows)
    body = (f'<table class="tbl"><thead><tr><th>Ticker</th>{_th("sentiment_score", "Score")}'
            f'{_th("finbert", "Label")}<th>Headlines</th></tr></thead>'
            f'<tbody>{tr}</tbody></table>')
    return _card(_title("\U0001F4F0", "News sentiment", "finbert"), body)


def _scorecard_section(sc: dict | None) -> str:
    if not sc:
        return ""
    horizons = sc.get("horizons", {}) or {}
    rows = []
    for key, m in horizons.items():
        m = m or {}
        rows.append(
            f"<tr><td>{_esc(key)}</td><td>{_esc(m.get('n', 0))}</td>"
            f"<td>{pct(m.get('hit_rate')) if m.get('hit_rate') is not None else '—'}</td>"
            f"<td>{pct(m.get('avg_alpha')) if m.get('avg_alpha') is not None else '—'}</td></tr>")
    graded = any((m or {}).get("n") for m in horizons.values())
    verdict = ("Too early to judge — picks need a few weeks of forward data."
               if not graded else
               "Grading past picks vs what prices actually did (alpha = pick − SPY).")
    paper = sc.get("paper", {}) or {}
    paper_line = ""
    if paper.get("status") in ("ok", "cash_only"):
        paper_line = (f'<p class="muted">Paper vs SPY: {pct(paper.get("port_return"))} vs '
                      f'{pct(paper.get("spy_return"))} '
                      f'(<strong>{pct(paper.get("excess"))}</strong> excess, '
                      f'{_esc(paper.get("n_days"))} days)</p>')
    table = (f'<table class="tbl"><thead><tr>{_th("horizon", "Horizon")}<th>Picks</th>'
             f'{_th("hit_rate", "Hit rate")}{_th("alpha", "Avg alpha")}</tr></thead><tbody>'
             f'{"".join(rows)}</tbody></table>' if rows else "")
    return _card(_title("\U0001F4CA", "Scorecard", "scorecard"),
                 f'<p class="muted">{_esc(verdict)}</p>{table}{paper_line}')


def _copilot_section(copilot: dict) -> str:
    if not (copilot.get("available") and copilot.get("commentary")):
        return ""
    paras = "".join(f"<p>{_esc(par.strip())}</p>"
                    for par in copilot["commentary"].split("\n\n") if par.strip())
    return _card(_title("\U0001F916", "Co-pilot take", "copilot"),
                 f'<p class="muted">Claude ({_esc(copilot.get("model", "—"))}) · '
                 f'advisory only, never trades</p>{paras}', extra_cls="copilot")


_NAV = [("Dashboard.md", "Dashboard"), ("Regime.md", "Regime"),
        ("Decisions.md", "Decisions"), ("Performance.md", "Performance"),
        ("Scorecard.md", "Scorecard"), ("SignalLab.md", "Signal Lab"),
        ("Tournament.md", "Tournament"),
        ("Review.md", "Review"), ("Copilot.md", "Co-pilot"),
        ("Clusters.md", "Clusters"), ("Sentiment.md", "Sentiment"),
        ("Start Here.md", "Start Here")]


def _signal_lab_section(sl: dict) -> str:
    sigs = sl.get("signals") or {}
    if not sigs:
        return ""
    bar_rows = []
    for s, d in sorted(sigs.items(), key=lambda kv: -(kv[1].get("ic") or -9)):
        ic = d.get("ic")
        plain = _gloss.GLOSSARY.get(s, {}).get("plain")
        name = (f"{_esc(plain)} {_ibtn(s)}" if plain else _esc(s))
        right = f'{pct(ic)} · {_esc(d.get("verdict", "")[:18])}'
        bar_rows.append((name, ic, right))
    tbl = (f'<p class="muted">Prediction accuracy {_ibtn("ic")} per signal — '
           f'bars left of centre predict <b>backwards</b>, right predict forwards.</p>'
           + _diverging_bars(bar_rows))
    val = sl.get("validation") or {}
    strip = ""
    if val.get("candidate_oos") is not None:
        strip = (f'<p class="muted">Fresh-data test {_ibtn("out_of_sample")} '
                 f'({_esc(val.get("n_oos"))} quarters): '
                 f'candidate <strong>{pct(val.get("candidate_oos"))}</strong> · '
                 f'default {pct(val.get("default_oos"))} · SPY {pct(val.get("spy_oos"))}</p>')
    return _card(_title("\U0001F52C", "Signal Lab", "ic") + _asof(sl.get("as_of")),
                 f'{strip}{tbl}')


def _tournament_section(t: dict) -> str:
    board = t.get("leaderboard") or []
    if not board:
        return ""
    rows = []
    for r in board[:12]:
        tot = r.get("total")
        tone = "pos" if (tot or 0) >= 0 else "neg"
        ctl = " ·ctl" if r.get("group") == "control" else ""
        rows.append(
            f"<tr><td>{_esc(r.get('rank'))}</td>"
            f"<td><strong>{_esc(r.get('label'))}</strong>{ctl} "
            f"{_ibtn(_gloss.strategy_key(r.get('label')))}</td>"
            f"<td class='{tone}'>{pct(tot)}</td>"
            f"<td>{num(r.get('sharpe'), 2) if r.get('sharpe') is not None else '—'}</td>"
            f"<td>{pct(r.get('excess'))}</td></tr>")
    bar_rows = []
    for r in board[:12]:
        tot = r.get("total")
        tone = "ctl" if r.get("group") == "control" else ("pos" if (tot or 0) >= 0 else "neg")
        lbl = f'{_esc(r.get("label"))} {_ibtn(_gloss.strategy_key(r.get("label")))}'
        bar_rows.append((lbl, tot, pct(tot), tone))
    bars = _hbars(bar_rows)
    tbl = (f'<table class="tbl"><thead><tr><th>#</th><th>Strategy</th>'
           f'{_th("excess", "Total")}{_th("sharpe", "Sharpe")}'
           f'{_th("excess", "vs SPY")}</tr></thead><tbody>{"".join(rows)}</tbody></table>')
    strip = (f'<p class="muted">Winner beat the market {_ibtn("spy")} by '
             f'<strong>{pct(t.get("beat_spy"))}</strong> · beat random {_ibtn("control")} by '
             f'<strong>{pct(t.get("beat_random"))}</strong> · fresh-data rank {_ibtn("out_of_sample")} '
             f'{_esc(t.get("oos_rank","—"))}</p>')
    return _card(_title("\U0001F3C6", "Strategy tournament", "tournament") + _asof(t.get("as_of")),
                 f'<p class="muted">{_esc(t.get("verdict",""))}</p>{strip}{bars}'
                 f'<details class="more-tbl"><summary>Full leaderboard table</summary>{tbl}</details>')


# In-page section anchors (the primary nav) — labels match the zone/section ids.
_ZONE_NAV = [("equity", "Equity"), ("money", "My money"), ("today", "Today"),
             ("working", "Is it working?"), ("hud", "Under the hood")]


def _nav() -> str:
    jump = "".join(f'<a href="#{zid}" data-jump="{zid}">{_esc(lbl)}</a>'
                   for zid, lbl in _ZONE_NAV)
    notes = " · ".join(f'<a href="{_esc(href)}">{_esc(label)}</a>'
                       for href, label in _NAV[:6])
    return (f'<nav class="nav" id="topnav"><span class="nav-jump">{jump}</span>'
            f'<details class="nav-notes"><summary>Obsidian notes ▾</summary>'
            f'<div class="nav-notes-list">{notes}</div></details></nav>')


def _run_banner(lr: dict) -> str:
    """Automation-health beacon: green=OK, red=failed, amber=stale (missed cadence)."""
    if not lr:
        return ""
    job, ended = _esc(lr.get("job")), _esc(str(lr.get("ended")).replace("T", " "))
    info = _ibtn("automation_health")
    if lr.get("status") == "fail":
        return (f'<div class="runbar fail">⚠ Last scheduled run (<strong>{job}</strong>) '
                f'FAILED at {ended} — check <code>logs/</code>.{info}</div>')
    if lr.get("stale"):
        return (f'<div class="runbar warn">⚠ No scheduled run in '
                f'<strong>{_esc(lr.get("age_h"))}h</strong> (last: {job} at {ended}) — '
                f'is the Mac asleep, or are the launchd agents loaded?{info}</div>')
    return (f'<div class="runbar ok">✓ Automation healthy — last run '
            f'<strong>{job}</strong> at {ended}.{info}</div>')


# Vanilla client JS (no libraries). `__GLOSSARY_JSON__` is substituted at render
# time. Affordances: hover/focus a "?" → one-line tooltip; click → worked-example
# popover; 🎓 Learn mode reveals inline notes (persisted); searchable glossary modal.
_PAGE_JS = r"""(function(){
  var G = __GLOSSARY_JSON__;
  var tip = document.getElementById('tip');
  var pinned = false;
  function esc(s){ var d=document.createElement('div'); d.textContent=(s==null?'':s); return d.innerHTML; }
  function termHead(e){ return esc(e.plain)+(e.term&&e.term!==e.plain?' <small>('+esc(e.term)+')</small>':''); }
  function place(el){
    tip.style.visibility='hidden'; tip.classList.add('show');
    var r=el.getBoundingClientRect(), tw=tip.offsetWidth, th=tip.offsetHeight;
    var left=Math.max(8, Math.min(r.left, window.innerWidth-tw-8));
    var top=r.bottom+8; if(top+th>window.innerHeight-8) top=r.top-th-8;
    tip.style.left=left+'px'; tip.style.top=Math.max(8,top)+'px'; tip.style.visibility='';
  }
  function showShort(btn){ if(pinned) return; var e=G[btn.dataset.term]; if(!e) return;
    tip.className=''; tip.innerHTML=esc(e.short||''); place(btn); tip.classList.add('show'); }
  function showRich(btn){ var e=G[btn.dataset.term]; if(!e) return; pinned=true;
    var h='<h4>'+termHead(e)+'</h4><div>'+esc(e.long||e.short||'')+'</div>';
    if(e.example) h+='<div class="ex"><b>Example:</b> '+esc(e.example)+'</div>';
    if(e.theory) h+='<div class="th">'+esc(e.theory)+'</div>';
    tip.className='rich'; tip.innerHTML=h; place(btn); tip.classList.add('show'); }
  function hide(){ if(!pinned) tip.classList.remove('show'); }
  function unpin(){ pinned=false; tip.classList.remove('show'); }
  document.querySelectorAll('button.i').forEach(function(btn){
    btn.addEventListener('mouseenter',function(){ showShort(btn); });
    btn.addEventListener('mouseleave',hide);
    btn.addEventListener('focus',function(){ showShort(btn); });
    btn.addEventListener('blur',hide);
    btn.addEventListener('click',function(ev){ ev.stopPropagation(); showRich(btn); });
  });
  document.addEventListener('click',function(ev){ if(pinned && !tip.contains(ev.target)) unpin(); });
  document.addEventListener('keydown',function(ev){ if(ev.key==='Escape'){ unpin(); closeGloss(); } });
  var lb=document.getElementById('learnBtn');
  function setLearn(on){ document.body.classList.toggle('learn',on); lb.classList.toggle('on',on);
    lb.setAttribute('aria-pressed',on?'true':'false');
    try{ localStorage.setItem('qt_learn',on?'1':'0'); }catch(e){} }
  lb.addEventListener('click',function(){ setLearn(!document.body.classList.contains('learn')); });
  try{ if(localStorage.getItem('qt_learn')==='1') setLearn(true); }catch(e){}
  // theme: stored pref wins; otherwise follow the OS preference. Default dark.
  var tb=document.getElementById('themeBtn');
  function setTheme(light){ document.body.classList.toggle('light',light);
    if(tb) tb.textContent=(light?'☀️ Theme':'🌙 Theme');
    try{ localStorage.setItem('qt_theme',light?'light':'dark'); }catch(e){} }
  (function(){ var pref=null; try{ pref=localStorage.getItem('qt_theme'); }catch(e){}
    if(pref==='light') setTheme(true);
    else if(!pref && window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches) setTheme(true); })();
  if(tb) tb.addEventListener('click',function(){ setTheme(!document.body.classList.contains('light')); });
  var intro=document.getElementById('intro'), introX=document.getElementById('introX');
  try{ if(localStorage.getItem('qt_intro')==='0' && intro) intro.hidden=true; }catch(e){}
  if(introX) introX.addEventListener('click',function(){ intro.hidden=true;
    try{ localStorage.setItem('qt_intro','0'); }catch(e){} });
  var gloss=document.getElementById('gloss'), list=document.getElementById('glossList'),
      search=document.getElementById('glossSearch');
  function renderGloss(q){ q=(q||'').toLowerCase();
    var keys=Object.keys(G).sort(function(a,b){ return G[a].plain.localeCompare(G[b].plain); });
    var html='';
    keys.forEach(function(k){ var e=G[k];
      var hay=(e.plain+' '+(e.term||'')+' '+(e.short||'')).toLowerCase();
      if(q && hay.indexOf(q)<0) return;
      html+='<div class="g"><h3>'+termHead(e)+'</h3><p>'+esc(e.long||e.short||'')+'</p>'+
        (e.example?'<p class="ex"><b>Example:</b> '+esc(e.example)+'</p>':'')+'</div>'; });
    list.innerHTML = html || '<p class="g">No terms match.</p>'; }
  function openGloss(){ renderGloss(''); gloss.classList.add('show'); search.value=''; search.focus(); }
  function closeGloss(){ if(gloss && gloss.classList.contains('show')){
    gloss.classList.remove('show'); var b=document.getElementById('glossBtn'); if(b) b.focus(); } }
  document.getElementById('glossBtn').addEventListener('click',openGloss);
  document.getElementById('glossX').addEventListener('click',closeGloss);
  gloss.addEventListener('click',function(ev){ if(ev.target===gloss) closeGloss(); });
  search.addEventListener('input',function(){ renderGloss(search.value); });
  // in-page nav: highlight the section in view + back-to-top
  var jumps=Array.prototype.slice.call(document.querySelectorAll('.nav-jump a'));
  var byId={}; jumps.forEach(function(a){ byId[a.dataset.jump]=a; });
  var targets=jumps.map(function(a){ return document.getElementById(a.dataset.jump); }).filter(Boolean);
  if('IntersectionObserver' in window && targets.length){
    var io=new IntersectionObserver(function(entries){
      entries.forEach(function(en){ if(en.isIntersecting){
        jumps.forEach(function(a){ a.classList.remove('active'); });
        var a=byId[en.target.id]; if(a) a.classList.add('active'); } });
    }, {rootMargin:'-45% 0px -50% 0px', threshold:0});
    targets.forEach(function(t){ io.observe(t); });
  }
  var toTop=document.getElementById('toTop');
  if(toTop){ window.addEventListener('scroll',function(){
      toTop.classList.toggle('show', window.scrollY>500); }, {passive:true});
    toTop.addEventListener('click',function(){
      try{ window.scrollTo({top:0,behavior:'smooth'}); }catch(e){ window.scrollTo(0,0); } }); }
})();"""


def dashboard_html(data: dict) -> str:
    regime = data.get("regime") or {}
    rlabel = regime.get("label", "unknown")
    rconf = regime.get("confidence")
    snap = data.get("latest_snapshot") or {}
    picks = data.get("top_picks") or []
    sectors = data.get("sectors") or {}
    names = data.get("names") or {}
    decisions = data.get("decisions") or []
    as_of = data.get("as_of", "")

    total_value = snap.get("total_value")
    pnl = ((snap.get("unrealized_pnl") or 0) + (snap.get("realized_pnl_ytd") or 0)
           if snap else None)
    dd = snap.get("drawdown_from_peak")
    n_pos = snap.get("n_positions", len(data.get("positions") or []))
    kpis_primary = (
        _kpi("Portfolio", money(total_value) if total_value else "$10,000", "paper money",
             key="paper_value", big=True)
        + _kpi("Total P&L", money(pnl) if pnl is not None else "$0.00",
               "realized + unrealized", tone="pos" if (pnl or 0) >= 0 else "neg",
               key="total_pnl", big=True))
    kpis_secondary = (
        _kpi("Drawdown", pct(dd) if dd is not None else "0.0%", "from peak",
             tone="neg" if (dd or 0) < 0 else "", key="drawdown")
        + _kpi("Positions", str(n_pos), "open paper holdings", key="positions")
        + _kpi("Cash", money(snap.get("cash")) if snap.get("cash") is not None else "$10,000",
               "available", key="cash")
        + _kpi("Picks", str(len(picks)), "top-overall this run", key="top_overall"))

    feed = "".join(f'<li>{_bold(d)}</li>' for d in decisions[:14])
    feed_html = (f'<ul class="feed">{feed}</ul>' if feed
                 else '<div class="empty">No decisions logged yet.</div>')

    conf_html = f' · {pct(rconf)} conf' if rconf is not None else ""

    # Embed the glossary for the client JS. `</` is neutralised so no value can
    # break out of the <script> element. Content is first-party + static (B13 n/a).
    gloss_json = _gloss.as_json().replace("</", "<\\/")
    script_block = "<script>\n" + _PAGE_JS.replace("__GLOSSARY_JSON__", gloss_json) + "\n</script>"

    return f'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{_REFRESH_SECONDS}">
<title>Quant Tracker — Dashboard</title>
<style>
  /* ── design tokens (dark = default, tuned) ── */
  :root {{
    --bg: #0d1117; --surface: #161b22; --surface2: #0d1117; --inset: #0d1117;
    --border: #2b313a; --border-soft: #21262d; --text: #e6edf3; --text2: #c9d1d9;
    --muted: #8b949e; --muted2: #6e7681; --pos: #3fb950; --neg: #f85149;
    --accent: #58a6ff; --accent2: #79c0ff; --bar: #388bfd;
    --shadow: 0 1px 2px #0007, 0 6px 20px #0004;
    --fs-1: 11px; --fs-2: 12px; --fs-3: 13px; --fs-4: 15px; --fs-5: 17px;
    --fs-6: 22px; --fs-kpi: 24px;
    --sp-1: 6px; --sp-2: 10px; --sp-3: 14px; --sp-4: 18px; --sp-5: 22px; --sp-6: 28px;
    color-scheme: dark;
  }}
  body.light {{
    --bg: #f6f8fa; --surface: #ffffff; --surface2: #f0f3f6; --inset: #f0f3f6;
    --border: #d0d7de; --border-soft: #e4e8ec; --text: #1f2328; --text2: #32383f;
    --muted: #59636e; --muted2: #7a828b; --pos: #1a7f37; --neg: #cf222e;
    --accent: #0969da; --accent2: #0a5cc5; --bar: #1f6feb;
    --shadow: 0 1px 2px #0000001a, 0 6px 18px #0000001f;
    color-scheme: light;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
    font: var(--fs-4)/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px 20px 64px; }}
  header {{ display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap;
    border-bottom: 1px solid var(--border-soft); padding-bottom: var(--sp-3); margin-bottom: var(--sp-4); }}
  header h1 {{ font-size: var(--fs-6); margin: 0; font-weight: 650; }}
  .badge {{ padding: 3px 12px; border-radius: 999px; font-weight: 650; font-size: var(--fs-3);
    text-transform: uppercase; letter-spacing: .04em; color: #0d1117; }}
  .updated {{ margin-left: auto; color: var(--muted); font-size: var(--fs-3); }}
  /* z-index order: nav 40 < toTop 45 < tip 50 < gloss 60 */
  .nav {{ position: sticky; top: 0; z-index: 40; display: flex; flex-wrap: wrap;
    align-items: center; gap: 4px 6px; margin: 0 -20px var(--sp-5); padding: 8px 20px;
    font-size: var(--fs-3); background: var(--bg);
    background: color-mix(in srgb, var(--bg) 88%, transparent);
    backdrop-filter: blur(6px); border-bottom: 1px solid var(--border-soft); }}
  .nav-jump {{ display: flex; flex-wrap: wrap; gap: 4px 4px; }}
  .nav-jump a {{ color: var(--muted); text-decoration: none; padding: 4px 10px;
    border-radius: 999px; white-space: nowrap; }}
  .nav-jump a:hover {{ color: var(--text); background: var(--surface); }}
  .nav-jump a.active {{ color: var(--accent); background: var(--surface); font-weight: 600; }}
  .nav-notes {{ margin-left: auto; font-size: var(--fs-2); }}
  .nav-notes summary {{ cursor: pointer; color: var(--muted2); list-style: none; }}
  .nav-notes summary::-webkit-details-marker {{ display: none; }}
  .nav-notes-list {{ position: absolute; right: 20px; margin-top: 6px; background: var(--surface);
    border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; max-width: 80vw;
    box-shadow: var(--shadow); }}
  .nav-notes-list a {{ color: var(--accent); text-decoration: none; }}
  #toTop {{ position: fixed; right: 18px; bottom: 18px; z-index: 45; width: 40px; height: 40px;
    border-radius: 50%; border: 1px solid var(--border); background: var(--surface);
    color: var(--text); font-size: 18px; cursor: pointer; box-shadow: var(--shadow);
    opacity: 0; pointer-events: none; transition: opacity .15s; }}
  #toTop.show {{ opacity: 1; pointer-events: auto; }}
  .runbar {{ border-radius: 10px; padding: 9px 14px; margin-bottom: var(--sp-4); font-size: var(--fs-3);
    border: 1px solid; }}
  .runbar.ok {{ background: #0f2417; border-color: #1f5132; color: #59d27e; }}
  .runbar.fail {{ background: #2d1213; border-color: #6e2528; color: #ff7b72; }}
  .runbar.warn {{ background: #2b2412; border-color: #6b5722; color: #e3b341; }}
  .runbar code {{ font-family: ui-monospace, monospace; }}
  .grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: var(--sp-3); margin-bottom: var(--sp-5); }}
  .kpi {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: var(--sp-3); box-shadow: var(--shadow); }}
  .kpi-label {{ color: var(--muted); font-size: var(--fs-1); text-transform: uppercase; letter-spacing: .04em; }}
  .kpi-val {{ font-size: var(--fs-kpi); font-weight: 680; margin-top: 4px; }}
  .kpi-sub {{ color: var(--muted2); font-size: var(--fs-1); margin-top: 2px; }}
  .kpi.pos .kpi-val {{ color: var(--pos); }} .kpi.neg .kpi-val {{ color: var(--neg); }}
  .kpi.big .kpi-val {{ font-size: 30px; }}
  .kpi-row1 {{ display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); margin-bottom: var(--sp-3); }}
  .kpi-row2 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--sp-3); margin-bottom: var(--sp-5); }}
  .headline {{ font-size: 17px; line-height: 1.5; margin: 2px 0 var(--sp-4);
    padding: var(--sp-3) var(--sp-4); background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; box-shadow: var(--shadow); }}
  .headline b {{ color: var(--text); }} .headline b.pos {{ color: var(--pos); }}
  .headline b.neg {{ color: var(--neg); }} .hl-mood {{ font-weight: 650; }}
  .cap {{ margin-top: var(--sp-2); }}
  .zone {{ margin-bottom: var(--sp-5); scroll-margin-top: 12px; }}
  .zone-h {{ font-size: var(--fs-2); text-transform: uppercase; letter-spacing: .06em;
    color: var(--muted2); font-weight: 600; margin: 0 0 var(--sp-2); padding-left: 2px; }}
  .hero {{ padding: var(--sp-4) 20px; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-4); }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: var(--sp-4) 20px; margin-bottom: var(--sp-4); box-shadow: var(--shadow); }}
  .card h2 {{ font-size: var(--fs-5); margin: 0 0 var(--sp-3); font-weight: 640; }}
  .card h2 .muted {{ font-weight: 400; }}
  .muted {{ color: var(--muted); font-size: var(--fs-3); margin: 0 0 var(--sp-2); }}
  table.tbl {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  .tbl th {{ text-align: left; color: var(--muted); font-weight: 550; font-size: var(--fs-2);
    text-transform: uppercase; letter-spacing: .03em; padding: 6px 8px; border-bottom: 1px solid var(--border-soft); }}
  .tbl td {{ padding: 7px 8px; border-bottom: 1px solid var(--border-soft); }}
  .tbl tr:last-child td {{ border-bottom: 0; }}
  td.pos {{ color: var(--pos); }} td.neg {{ color: var(--neg); }}
  .tbl tr.subtotal td {{ font-weight: 640; border-top: 1px solid var(--border);
    background: var(--inset); }}
  .tbl tr.grand td {{ font-weight: 700; border-top: 2px solid var(--border);
    font-size: var(--fs-4); }}
  .chips {{ display: flex; flex-wrap: wrap; gap: var(--sp-2); }}
  .chip {{ background: var(--inset); border: 1px solid var(--border-soft); border-radius: 10px;
    padding: 8px 12px; font-size: var(--fs-2); color: var(--muted); }}
  .chip span {{ display: block; font-size: 19px; font-weight: 680; color: var(--text); }}
  .chip.neg span {{ color: var(--neg); }}
  .pick {{ padding: var(--sp-2) 0; border-bottom: 1px solid var(--border-soft); }}
  .pick:last-child {{ border-bottom: 0; }}
  .pick-hd {{ display: flex; align-items: baseline; gap: var(--sp-2); }}
  .pick-sec {{ color: var(--muted); font-size: var(--fs-2); }}
  .coname {{ color: var(--muted); font-weight: 400; font-size: var(--fs-2); }}
  .pick-score {{ margin-left: auto; font-variant-numeric: tabular-nums; color: var(--text); font-weight: 640; }}
  .sigs {{ margin-top: 7px; display: grid; gap: 3px; }}
  .sigrow {{ display: grid; grid-template-columns: 90px 1fr 36px; align-items: center; gap: 8px; }}
  .siglbl {{ color: var(--muted2); font-size: var(--fs-1); }}
  .sigbar {{ background: var(--inset); border-radius: 4px; height: 7px; overflow: hidden; }}
  .sigbar i {{ display: block; height: 100%; background: var(--bar); }}
  .sigval {{ color: var(--muted); font-size: var(--fs-1); text-align: right; font-variant-numeric: tabular-nums; }}
  .feed {{ list-style: none; margin: 0; padding: 0; }}
  .feed li {{ padding: 9px 0; border-bottom: 1px solid var(--border-soft); font-size: 14px; }}
  .feed li:last-child {{ border-bottom: 0; }}
  .chart {{ width: 100%; height: auto; }}
  .chart .axis {{ fill: var(--muted2); font-size: var(--fs-1); }}
  /* equity chart strokes via tokens so the curve is correct in light mode too */
  .chart .eq-base {{ stroke: var(--border); }}
  .chart .eq-bench {{ stroke: var(--muted); }}
  .chart .eq-strat {{ stroke: var(--pos); }}
  .chart .eqdot {{ fill: var(--pos); }}
  /* diverging + horizontal bars (theme-aware via var()) */
  .dbars, .hbars {{ display: grid; gap: 6px; margin: 4px 0 2px; }}
  .db-row {{ display: grid; grid-template-columns: minmax(96px,1.2fr) 1fr 92px;
    align-items: center; gap: 10px; }}
  .db-lbl {{ font-size: var(--fs-2); color: var(--text); display: flex; align-items: center; }}
  .db-track {{ position: relative; height: 10px; background: var(--inset); border-radius: 5px; }}
  .db-zero {{ position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--border); }}
  .db-fill {{ position: absolute; top: 0; height: 100%; border-radius: 5px; }}
  .db-fill.pos {{ background: var(--pos); }} .db-fill.neg {{ background: var(--neg); }}
  .db-val {{ font-size: var(--fs-2); text-align: right; color: var(--muted);
    font-variant-numeric: tabular-nums; }}
  .hb-row {{ display: grid; grid-template-columns: minmax(120px,1.4fr) 1fr 64px;
    align-items: center; gap: 10px; }}
  .hb-lbl {{ font-size: var(--fs-2); color: var(--text); display: flex; align-items: center; }}
  .hb-track {{ position: relative; height: 12px; background: var(--inset); border-radius: 6px; overflow: hidden; }}
  .hb-fill {{ display: block; height: 100%; background: var(--accent); border-radius: 6px; }}
  .hb-fill.pos {{ background: var(--pos); }} .hb-fill.neg {{ background: var(--neg); }}
  .hb-fill.ctl {{ background: var(--muted2); }}
  .hb-val {{ font-size: var(--fs-2); text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); }}
  .hb-val.pos {{ color: var(--pos); }} .hb-val.neg {{ color: var(--neg); }}
  /* donut */
  .donut-wrap {{ display: flex; align-items: center; gap: 18px; flex-wrap: wrap; margin-top: 4px; }}
  .donut {{ flex: 0 0 auto; }}
  .donut-lg {{ display: grid; grid-template-columns: 1fr 1fr; gap: 3px 16px; font-size: var(--fs-2); color: var(--muted); }}
  .donut-lg .lg {{ display: flex; align-items: center; gap: 6px; }}
  .donut-lg .lg i {{ width: 9px; height: 9px; border-radius: 2px; flex: 0 0 auto; }}
  .donut-lg .lg b {{ color: var(--text); font-weight: 600; }}
  .more-tbl {{ margin-top: var(--sp-2); }}
  .more-tbl summary {{ cursor: pointer; color: var(--accent); font-size: var(--fs-2); }}
  .empty {{ color: var(--muted2); font-size: 14px; padding: var(--sp-3) 0; }}
  .copilot p {{ font-size: 14.5px; }}
  footer {{ color: var(--muted2); font-size: var(--fs-2); margin-top: var(--sp-6);
    border-top: 1px solid var(--border-soft); padding-top: var(--sp-3); }}
  @media (max-width: 820px) {{ .grid {{ grid-template-columns: repeat(2, 1fr); }}
    .kpi-row2 {{ grid-template-columns: repeat(2, 1fr); }}
    .cols {{ grid-template-columns: 1fr; }} }}

  /* ── educational layer ── */
  .toolbar {{ display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2);
    margin: 2px 0 var(--sp-4); }}
  .toolbar button {{ font: inherit; font-size: var(--fs-3); cursor: pointer; color: var(--text2);
    background: var(--surface); border: 1px solid var(--border); border-radius: 999px;
    padding: 6px 14px; display: inline-flex; align-items: center; gap: 6px; }}
  .toolbar button:hover {{ border-color: var(--accent); color: var(--text); }}
  .toolbar .on {{ background: #132a18; border-color: #2ea043; color: #59d27e; }}
  body.light .toolbar .on {{ background: #dafbe1; border-color: #1a7f37; color: #1a7f37; }}
  .toolbar .hint {{ color: var(--muted2); font-size: var(--fs-2); }}
  button.i {{ all: unset; cursor: help; display: inline-flex; align-items: center;
    justify-content: center; width: 15px; height: 15px; margin-left: 5px;
    border-radius: 50%; background: var(--inset); color: var(--accent2); font-size: 10px;
    font-weight: 700; vertical-align: middle; line-height: 1; border: 1px solid var(--border); }}
  button.i:hover, button.i:focus-visible {{ background: var(--accent); color: #fff; outline: none; }}
  button.i:focus-visible {{ box-shadow: 0 0 0 2px var(--accent); }}
  .tterm {{ color: var(--muted2); font-weight: 400; }}
  .tlbl {{ display: inline-flex; align-items: center; }}
  .explain {{ display: none; }}
  body.learn .explain {{ display: block; color: var(--muted); font-size: 11.5px;
    font-weight: 400; line-height: 1.45; margin-top: 3px; max-width: 46ch; }}
  body.learn .kpi {{ min-height: 0; }}
  .h2sub {{ display: block; color: var(--muted); font-size: var(--fs-2); font-weight: 400; margin-top: 3px; }}
  .eqdot {{ opacity: 0; transition: opacity .12s; }}
  .eqpt:hover .eqdot {{ opacity: 1; }}
  /* tooltip popover (hover/focus = short; click = full explainer) */
  #tip {{ position: fixed; z-index: 50; max-width: 320px; background: var(--surface);
    border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px;
    box-shadow: 0 8px 28px #0009; font-size: var(--fs-3); line-height: 1.5;
    color: var(--text); pointer-events: none; opacity: 0; transition: opacity .1s; }}
  #tip.show {{ opacity: 1; }}
  #tip.rich {{ pointer-events: auto; }}
  #tip h4 {{ margin: 0 0 6px; font-size: var(--fs-3); }}
  #tip h4 small {{ color: var(--muted); font-weight: 400; }}
  #tip .ex {{ margin-top: 8px; padding: 8px 10px; background: var(--inset); border-radius: 7px;
    border-left: 3px solid var(--pos); color: var(--text2); }}
  #tip .th {{ margin-top: 8px; color: var(--muted); font-style: italic; }}
  #tip .more {{ margin-top: 8px; color: var(--accent); font-size: var(--fs-2); }}
  /* glossary modal */
  #gloss {{ position: fixed; inset: 0; z-index: 60; background: #010409cc;
    display: none; padding: 40px 16px; overflow: auto; }}
  #gloss.show {{ display: block; }}
  #gloss .panel {{ max-width: 760px; margin: 0 auto; background: var(--bg);
    border: 1px solid var(--border); border-radius: 14px; padding: 22px 24px; }}
  #gloss .gh {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
  #gloss h2 {{ margin: 0; font-size: 18px; }}
  #gloss input {{ flex: 1; font: inherit; font-size: 14px; background: var(--surface);
    border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; color: var(--text); }}
  #gloss .x {{ all: unset; cursor: pointer; color: var(--muted); font-size: 22px; padding: 0 6px; }}
  #gloss .g {{ padding: 12px 0; border-bottom: 1px solid var(--border-soft); }}
  #gloss .g h3 {{ margin: 0 0 4px; font-size: 14px; }}
  #gloss .g h3 small {{ color: var(--muted2); font-weight: 400; }}
  #gloss .g p {{ margin: 4px 0 0; font-size: var(--fs-3); color: var(--text2); line-height: 1.5; }}
  #gloss .g .ex {{ color: var(--muted); font-size: 12.5px; }}
  /* onboarding intro */
  .intro {{ background: var(--inset); border: 1px solid var(--accent); border-radius: 12px;
    padding: 14px 18px; margin-bottom: var(--sp-4); font-size: 13.5px; color: var(--text2); }}
  .intro[hidden] {{ display: none; }}
  .intro b {{ color: var(--text); }}
  .intro ul {{ margin: 8px 0 0; padding-left: 18px; }} .intro li {{ margin: 3px 0; }}
  .asof {{ color: var(--muted2); font-weight: 400; font-size: var(--fs-1); }}
  @media (max-width: 480px) {{ #tip {{ max-width: 90vw; }} }}
  @media (max-width: 600px) {{
    /* dense tables scroll within their card instead of overflowing the page */
    .card {{ overflow-x: auto; }}
    .tbl td, .tbl th {{ white-space: nowrap; }}
    .kpi.big .kpi-val {{ font-size: 24px; }}
    .headline {{ font-size: 15px; }}
    .db-row {{ grid-template-columns: minmax(74px, 1fr) 1fr 74px; }}
    .hb-row {{ grid-template-columns: minmax(96px, 1.3fr) 1fr 56px; }}
    .db-lbl, .hb-lbl {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .donut-lg {{ grid-template-columns: 1fr; }}
  }}
  @media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
</style></head>
<body><div class="wrap">
  <header>
    <h1>\U0001F916 Quant Tracker</h1>
    <span class="badge" style="background:{_regime_color(rlabel)}">{_esc(rlabel)}{conf_html}</span>{_ibtn("regime")}
    <span class="updated">Updated {_esc(str(as_of)[:16].replace("T", " "))} UTC</span>
  </header>
  <div class="toolbar">
    <button id="learnBtn" type="button" aria-pressed="false">\U0001F393 Learn mode</button>
    <button id="glossBtn" type="button">\U0001F4D6 Glossary</button>
    <button id="themeBtn" type="button" aria-label="Toggle light/dark theme">\U0001F319 Theme</button>
    <span class="hint">New here? Turn on <b>Learn mode</b>, or click any <b>?</b> for a plain-English explanation.</span>
  </div>
  <div class="intro" id="intro">
    <b>How to read this dashboard.</b> It's a paper-trading research tool — pretend money, no real trades.
    Every number has a <b>?</b>: hover it for a one-line plain-English definition, or click it for a worked
    example. Turn on <b>\U0001F393 Learn mode</b> to show explanations everywhere, or open the <b>\U0001F4D6 Glossary</b>
    to search every term.
    <ul>
      <li><b>Top of page</b> — the market's mood and your pretend portfolio's value, profit, and risk.</li>
      <li><b>Middle</b> — this run's best stock ideas and why each scored well.</li>
      <li><b>Lower down</b> — honest evidence: did past picks actually beat the market? do the signals really predict?</li>
    </ul>
    <button class="x" id="introX" type="button" style="float:right;margin-top:-44px">Got it ✕</button>
  </div>
  {_nav()}
  {_run_banner(data.get("last_run") or {})}
  {_headline(rlabel, total_value, pnl, n_pos)}

  {_card(_title("\U0001F4C8", "Equity curve — strategy vs the market", "equity_curve"),
         _svg_equity(data.get("snapshots") or []) + _equity_caption(data.get("snapshots") or []),
         extra_cls="hero", cid="equity")}

  {_zone("money", "\U0001F4B0", "My money",
         f'<div class="kpi-row1">{kpis_primary}</div>'
         f'<div class="kpi-row2">{kpis_secondary}</div>'
         + _positions_section(data.get("positions") or [], names))}

  {_zone("today", "\U0001F4C5", "Today's read",
         _screener_stats(data.get("summary"))
         + f'<div class="cols">{_picks_section(picks, sectors, names)}'
           f'{_card("\U0001F9E0 Recent decisions", feed_html)}</div>'
         + f'<div class="cols">{_sector_table(sectors, names)}'
           f'{_vetoes_section(sectors, data.get("summary"))}</div>')}

  {_zone("working", "\U0001F9EA", "Is it working?",
         _scorecard_section(data.get("scorecard"))
         + _signal_lab_section(data.get("signal_lab") or {})
         + _tournament_section(data.get("tournament") or {}))}

  {_zone("hud", "\U0001F50E", "Under the hood",
         _sentiment_section(data.get("sentiment"), names)
         + _copilot_section(data.get("copilot") or {}))}

  <footer>Auto-generated by quant-tracker — do not edit; regenerated each run.
  Paper money, research only — not financial advice. Reloads every {_REFRESH_SECONDS // 60} min.</footer>
</div>
<button id="toTop" type="button" aria-label="Back to top" title="Back to top">↑</button>
<div id="tip" role="tooltip"></div>
<div id="gloss" role="dialog" aria-modal="true" aria-label="Glossary">
  <div class="panel">
    <div class="gh"><h2>\U0001F4D6 Glossary</h2>
      <input id="glossSearch" type="text" placeholder="Search terms…" aria-label="Search glossary">
      <button class="x" id="glossX" type="button" aria-label="Close">✕</button></div>
    <div id="glossList"></div>
  </div>
</div>
{script_block}
</body></html>'''


__all__ = ["dashboard_html"]
