"""render/html.py — PURE builder for a self-contained visual dashboard.

Emits a single ``Dashboard.html`` (inline CSS + inline SVG charts, NO external
libraries or CDNs) so it opens in any browser, offline, and syncs via Drive.
Like the Markdown notes it is a regenerated render artifact — never hand-edited.
``render.build`` does the IO and calls ``dashboard_html``.
"""
from __future__ import annotations

import html as _html
import re

from render.markdown import money, num, pct

_REFRESH_SECONDS = 900  # an open tab reloads itself every 15 min


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _bold(s: str) -> str:
    """Escape, then turn markdown **bold** into <strong> (decisions use it)."""
    out = _esc(s)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)


def _regime_color(label: str) -> str:
    return {"bull": "#3fb950", "bear": "#f85149"}.get(
        str(label).lower(), "#d29922")  # sideways / unknown → amber


def _kpi(label: str, value: str, sub: str = "", tone: str = "") -> str:
    cls = f" {tone}" if tone else ""
    sub_html = f'<div class="kpi-sub">{_esc(sub)}</div>' if sub else ""
    return (f'<div class="kpi{cls}"><div class="kpi-label">{_esc(label)}</div>'
            f'<div class="kpi-val">{_esc(value)}</div>{sub_html}</div>')


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
    base_y = pad + ih - (ih * (100.0 - lo) / (hi - lo))   # the "100" baseline
    bench_line = (f'<polyline points="{bench_poly}" fill="none" '
                  f'stroke="#8b949e" stroke-width="2" stroke-dasharray="4 4"/>'
                  if bench_poly else "")
    return f'''<svg viewBox="0 0 {w} {h}" class="chart" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Equity curve">
  <line x1="{pad}" y1="{base_y:.1f}" x2="{w - pad}" y2="{base_y:.1f}" stroke="#30363d" stroke-width="1"/>
  {bench_line}
  <polyline points="{strat_poly}" fill="none" stroke="#3fb950" stroke-width="2.5"/>
  <text x="{pad}" y="{pad - 10}" class="axis">indexed to 100 at start</text>
  <text x="{w - pad}" y="{pad - 10}" class="axis" text-anchor="end">strategy ● &nbsp; SPY ┄</text>
</svg>'''


def _scorecard_section(sc: dict | None) -> str:
    if not sc:
        return ""
    horizons = sc.get("horizons", {}) or {}
    rows = []
    for key, m in horizons.items():
        m = m or {}
        n = m.get("n", 0)
        rows.append(
            f"<tr><td>{_esc(key)}</td><td>{_esc(n)}</td>"
            f"<td>{pct(m.get('hit_rate')) if m.get('hit_rate') is not None else '—'}</td>"
            f"<td>{pct(m.get('avg_alpha')) if m.get('avg_alpha') is not None else '—'}</td></tr>")
    graded = any((m or {}).get("n") for m in horizons.values())
    verdict = ("Tracking is too early to judge — picks need a few weeks of "
               "forward data." if not graded else
               "Grading past picks against what prices actually did (alpha = pick − SPY).")
    paper = sc.get("paper", {}) or {}
    paper_line = ""
    if paper.get("status") in ("ok", "cash_only"):
        paper_line = (f'<p class="muted">Paper vs SPY: '
                      f'{pct(paper.get("port_return"))} vs {pct(paper.get("spy_return"))} '
                      f'(<strong>{pct(paper.get("excess"))}</strong> excess, '
                      f'{_esc(paper.get("n_days"))} days)</p>')
    table = (f'<table class="tbl"><thead><tr><th>Horizon</th><th>Picks</th>'
             f'<th>Hit rate</th><th>Avg alpha</th></tr></thead><tbody>'
             f'{"".join(rows)}</tbody></table>' if rows else "")
    return (f'<section class="card"><h2>📊 Scorecard</h2>'
            f'<p class="muted">{_esc(verdict)}</p>{table}{paper_line}</section>')


def dashboard_html(data: dict) -> str:
    regime = data.get("regime") or {}
    rlabel = regime.get("label", "unknown")
    snap = data.get("latest_snapshot") or {}
    picks = data.get("top_picks") or []
    decisions = data.get("decisions") or []
    copilot = data.get("copilot") or {}
    as_of = data.get("as_of", "")

    total_value = snap.get("total_value")
    pnl = None
    if snap:
        pnl = (snap.get("unrealized_pnl") or 0) + (snap.get("realized_pnl_ytd") or 0)
    dd = snap.get("drawdown_from_peak")

    kpis = "".join([
        _kpi("Portfolio", money(total_value) if total_value else "$10,000",
             "paper money"),
        _kpi("Total P&L", money(pnl) if pnl is not None else "$0.00",
             "realized + unrealized",
             tone="pos" if (pnl or 0) >= 0 else "neg"),
        _kpi("Drawdown", pct(dd) if dd is not None else "0.0%", "from peak",
             tone="neg" if (dd or 0) < 0 else ""),
        _kpi("Positions", str(snap.get("n_positions", len(data.get("positions") or []))),
             "open paper holdings"),
    ])

    pick_rows = "".join(
        f"<tr><td><strong>{_esc(p.get('ticker'))}</strong></td>"
        f"<td>{_esc(p.get('sector', ''))}</td>"
        f"<td>{num(p.get('composite_score', p.get('score')), 3)}</td></tr>"
        for p in picks[:12])
    picks_table = (f'<table class="tbl"><thead><tr><th>Ticker</th><th>Sector</th>'
                   f'<th>Score</th></tr></thead><tbody>{pick_rows}</tbody></table>'
                   if pick_rows else '<div class="empty">No screener run yet.</div>')

    feed = "".join(f'<li>{_bold(d)}</li>' for d in decisions[:12])
    feed_html = (f'<ul class="feed">{feed}</ul>' if feed
                 else '<div class="empty">No decisions logged yet.</div>')

    copilot_html = ""
    if copilot.get("available") and copilot.get("commentary"):
        paras = "".join(f"<p>{_esc(par.strip())}</p>"
                        for par in copilot["commentary"].split("\n\n") if par.strip())
        copilot_html = (
            f'<section class="card copilot"><h2>🤖 Co-pilot take</h2>'
            f'<p class="muted">Claude ({_esc(copilot.get("model", "—"))}) · '
            f'advisory only, never trades</p>{paras}</section>')

    chart = _svg_equity(data.get("snapshots") or [])

    return f'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{_REFRESH_SECONDS}">
<title>Quant Tracker — Dashboard</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0d1117; color: #e6edf3;
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }}
  .wrap {{ max-width: 1080px; margin: 0 auto; padding: 24px 20px 64px; }}
  header {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    border-bottom: 1px solid #21262d; padding-bottom: 16px; margin-bottom: 24px; }}
  header h1 {{ font-size: 22px; margin: 0; font-weight: 650; }}
  .badge {{ padding: 3px 12px; border-radius: 999px; font-weight: 650;
    font-size: 13px; text-transform: uppercase; letter-spacing: .04em;
    color: #0d1117; }}
  .updated {{ margin-left: auto; color: #8b949e; font-size: 13px; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
    margin-bottom: 22px; }}
  .kpi {{ background: #161b22; border: 1px solid #21262d; border-radius: 12px;
    padding: 16px; }}
  .kpi-label {{ color: #8b949e; font-size: 12px; text-transform: uppercase;
    letter-spacing: .04em; }}
  .kpi-val {{ font-size: 24px; font-weight: 680; margin-top: 4px; }}
  .kpi-sub {{ color: #6e7681; font-size: 12px; margin-top: 2px; }}
  .kpi.pos .kpi-val {{ color: #3fb950; }} .kpi.neg .kpi-val {{ color: #f85149; }}
  .cols {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 18px; }}
  .card {{ background: #161b22; border: 1px solid #21262d; border-radius: 12px;
    padding: 18px 20px; margin-bottom: 18px; }}
  .card h2 {{ font-size: 15px; margin: 0 0 12px; font-weight: 640; }}
  .muted {{ color: #8b949e; font-size: 13px; margin: 0 0 10px; }}
  table.tbl {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  .tbl th {{ text-align: left; color: #8b949e; font-weight: 550; font-size: 12px;
    text-transform: uppercase; letter-spacing: .03em; padding: 6px 8px;
    border-bottom: 1px solid #21262d; }}
  .tbl td {{ padding: 7px 8px; border-bottom: 1px solid #1c2129; }}
  .tbl tr:last-child td {{ border-bottom: 0; }}
  .feed {{ list-style: none; margin: 0; padding: 0; }}
  .feed li {{ padding: 9px 0; border-bottom: 1px solid #1c2129; font-size: 14px; }}
  .feed li:last-child {{ border-bottom: 0; }}
  .chart {{ width: 100%; height: auto; }}
  .chart .axis {{ fill: #6e7681; font-size: 11px; }}
  .empty {{ color: #6e7681; font-size: 14px; padding: 14px 0; }}
  .copilot p {{ font-size: 14.5px; }}
  footer {{ color: #6e7681; font-size: 12px; margin-top: 26px;
    border-top: 1px solid #21262d; padding-top: 14px; }}
  @media (max-width: 760px) {{ .grid {{ grid-template-columns: repeat(2, 1fr); }}
    .cols {{ grid-template-columns: 1fr; }} }}
</style></head>
<body><div class="wrap">
  <header>
    <h1>🤖 Quant Tracker</h1>
    <span class="badge" style="background:{_regime_color(rlabel)}">{_esc(rlabel)}</span>
    <span class="updated">Updated {_esc(str(as_of)[:16].replace("T", " "))} UTC</span>
  </header>

  <div class="grid">{kpis}</div>

  <section class="card"><h2>📈 Equity curve — strategy vs SPY</h2>{chart}</section>

  <div class="cols">
    <section class="card"><h2>🎯 Top picks</h2>{picks_table}</section>
    <section class="card"><h2>🧠 Recent decisions</h2>{feed_html}</section>
  </div>

  {_scorecard_section(data.get("scorecard"))}
  {copilot_html}

  <footer>Auto-generated by quant-tracker — do not edit; regenerated each run.
  Paper money, research only — not financial advice. Reloads every {_REFRESH_SECONDS // 60} min.</footer>
</div></body></html>'''


__all__ = ["dashboard_html"]
