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
_SIGNALS = ("arima", "kalman", "garch", "monte_carlo", "sharpe", "momentum")


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
    """A tiny '?' button JS wires to the glossary entry for `key` (or nothing)."""
    if not _gloss.has(key):
        return ""
    return (f'<button class="i" type="button" data-term="{_esc(key)}" '
            f'aria-label="What does this mean?">?</button>')


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


def _kpi(label: str, value: str, sub: str = "", tone: str = "", key: str = "") -> str:
    cls = f" {tone}" if tone else ""
    sub_html = f'<div class="kpi-sub">{_esc(sub)}</div>' if sub else ""
    label_html = _term(key) if key else _esc(label)
    return (f'<div class="kpi{cls}"><div class="kpi-label">{label_html}</div>'
            f'<div class="kpi-val">{_esc(value)}</div>{sub_html}</div>')


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
    bench_line = (f'<polyline points="{bench_poly}" fill="none" stroke="#8b949e" '
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
            f'fill="#3fb950" class="eqdot"/></g>')
    return f'''<svg viewBox="0 0 {w} {h}" class="chart" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Equity curve — strategy vs SPY, indexed to 100 at start. Hover a point for values.">
  <line x1="{pad}" y1="{base_y:.1f}" x2="{w - pad}" y2="{base_y:.1f}" stroke="#30363d" stroke-width="1"/>
  {bench_line}
  <polyline points="{strat_poly}" fill="none" stroke="#3fb950" stroke-width="2.5"/>
  {"".join(dots)}
  <text x="{pad}" y="{pad - 10}" class="axis">indexed to 100 at start</text>
  <text x="{w - pad}" y="{pad - 10}" class="axis" text-anchor="end">strategy ● &nbsp; SPY ┄</text>
</svg>'''


def _card(title: str, inner: str, extra_cls: str = "") -> str:
    return f'<section class="card {extra_cls}"><h2>{title}</h2>{inner}</section>'


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


def _picks_section(picks: list[dict], sectors: dict) -> str:
    if not picks:
        return _card(_title("\U0001F3AF", "Top picks", "top_overall"),
                     '<div class="empty">No screener run yet.</div>')
    look = _signal_lookup(sectors)
    rows = []
    for p in picks[:8]:
        s = look.get(p.get("ticker"), {})
        rows.append(
            f'<div class="pick"><div class="pick-hd">'
            f'<strong>{_esc(p.get("ticker"))}</strong>'
            f'<span class="pick-sec">{_esc(p.get("sector", ""))}</span>'
            f'<span class="pick-score" title="overall score">{num(p.get("composite_score", p.get("score")), 3)}'
            f'{_ibtn("composite")}</span>'
            f'</div>{_signal_bars(s.get("signal_scores", {}))}</div>')
    return _card(_title("\U0001F3AF", "Top picks", "composite",
                        sub_key="composite"), "".join(rows))


def _sector_table(sectors: dict) -> str:
    if not sectors:
        return ""
    rows = []
    for name, stocks in sectors.items():
        stocks = stocks or []
        passed = sum(1 for s in stocks if s.get("passed_veto"))
        top = next((s for s in stocks if s.get("rank") == 1), stocks[0] if stocks else {})
        rows.append(
            f"<tr><td>{_esc(name.replace('_', ' '))}</td>"
            f"<td><strong>{_esc(top.get('ticker', '—'))}</strong></td>"
            f"<td>{num(top.get('composite_score'), 3) if top.get('composite_score') is not None else '—'}</td>"
            f"<td>{passed}/{len(stocks)}</td></tr>")
    body = (f'<table class="tbl"><thead><tr>{_th("sector", "Sector")}'
            f'<th>Top pick</th>{_th("composite", "Score")}'
            f'{_th("veto", "Passed")}</tr></thead><tbody>{"".join(rows)}</tbody></table>')
    return _card(_title("\U0001F3E2", "By sector", "sector"), body)


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


def _positions_section(positions: list[dict]) -> str:
    if not positions:
        inner = ('<div class="empty">No open positions yet — the first paper buys '
                 'land in the monthly 1st–5th window.</div>')
        return _card(_title("\U0001F4BC", "Positions", "positions"), inner)
    rows = []
    for p in positions:
        upnl = p.get("unrealized_pnl")
        tone = "pos" if (upnl or 0) >= 0 else "neg"
        rows.append(
            f"<tr><td><strong>{_esc(p.get('ticker'))}</strong></td>"
            f"<td>{num(p.get('shares', p.get('quantity')), 2)}</td>"
            f"<td>{money(p.get('avg_cost', p.get('cost_basis', p.get('entry_price'))))}</td>"
            f"<td>{money(p.get('current_price'))}</td>"
            f"<td>{money(p.get('market_value'))}</td>"
            f"<td class='{tone}'>{money(upnl) if upnl is not None else '—'}</td></tr>")
    body = (f'<table class="tbl"><thead><tr><th>Ticker</th>{_th("shares", "Shares")}'
            f'{_th("cost_basis", "Cost")}<th>Price</th>{_th("market_value", "Value")}'
            f'{_th("unrealized_pnl", "Unreal. P&amp;L")}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')
    return _card(_title("\U0001F4BC", "Positions", "positions"), body)


def _sentiment_section(rows: list[dict]) -> str:
    rows = [r for r in (rows or []) if r.get("label") and r.get("label") != "UNAVAILABLE"]
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: (r.get("sentiment_score") if r.get("sentiment_score")
                                       is not None else 0))[:10]
    tr = "".join(
        f"<tr><td><strong>{_esc(r.get('ticker'))}</strong></td>"
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
    rows = []
    for s, d in sorted(sigs.items(), key=lambda kv: -(kv[1].get("ic") or -9)):
        ic = d.get("ic")
        tone = "pos" if (ic or 0) > 0.02 else "neg" if (ic or 0) < -0.02 else ""
        rows.append(f"<tr><td><strong>{_esc(s)}</strong></td>"
                    f"<td class='{tone}'>{pct(ic)}</td><td>{_esc(d.get('verdict'))}</td></tr>")
    tbl = (f'<table class="tbl"><thead><tr><th>Signal</th>{_th("ic", "IC")}'
           f'{_th("verdict", "Verdict")}</tr></thead><tbody>{"".join(rows)}</tbody></table>')
    val = sl.get("validation") or {}
    strip = ""
    if val.get("candidate_oos") is not None:
        strip = (f'<p class="muted">Fresh-data test {_ibtn("out_of_sample")} '
                 f'({_esc(val.get("n_oos"))} quarters): '
                 f'candidate <strong>{pct(val.get("candidate_oos"))}</strong> · '
                 f'default {pct(val.get("default_oos"))} · SPY {pct(val.get("spy_oos"))}</p>')
    return _card(_title("\U0001F52C", "Signal Lab", "ic"), f'{strip}{tbl}')


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
            f"<td><strong>{_esc(r.get('label'))}</strong>{ctl}</td>"
            f"<td class='{tone}'>{pct(tot)}</td>"
            f"<td>{num(r.get('sharpe'), 2) if r.get('sharpe') is not None else '—'}</td>"
            f"<td>{pct(r.get('excess'))}</td></tr>")
    tbl = (f'<table class="tbl"><thead><tr><th>#</th><th>Strategy</th>'
           f'{_th("excess", "Total")}{_th("sharpe", "Sharpe")}'
           f'{_th("excess", "vs SPY")}</tr></thead><tbody>{"".join(rows)}</tbody></table>')
    strip = (f'<p class="muted">Winner beat the market {_ibtn("spy")} by '
             f'<strong>{pct(t.get("beat_spy"))}</strong> · beat random {_ibtn("control")} by '
             f'<strong>{pct(t.get("beat_random"))}</strong> · fresh-data rank {_ibtn("out_of_sample")} '
             f'{_esc(t.get("oos_rank","—"))}</p>')
    return _card(_title("\U0001F3C6", "Strategy tournament", "tournament"),
                 f'<p class="muted">{_esc(t.get("verdict",""))}</p>{strip}{tbl}')


def _nav() -> str:
    links = "".join(f'<a href="{_esc(href)}">{_esc(label)}</a>' for href, label in _NAV)
    return f'<nav class="nav">{links}</nav>'


def _run_banner(lr: dict) -> str:
    """Automation-health beacon: green=OK, red=failed, amber=stale (missed cadence)."""
    if not lr:
        return ""
    job, ended = _esc(lr.get("job")), _esc(str(lr.get("ended")).replace("T", " "))
    if lr.get("status") == "fail":
        return (f'<div class="runbar fail">⚠ Last scheduled run (<strong>{job}</strong>) '
                f'FAILED at {ended} — check <code>logs/</code>.</div>')
    if lr.get("stale"):
        return (f'<div class="runbar warn">⚠ No scheduled run in '
                f'<strong>{_esc(lr.get("age_h"))}h</strong> (last: {job} at {ended}) — '
                f'is the Mac asleep, or are the launchd agents loaded?</div>')
    return (f'<div class="runbar ok">✓ Automation healthy — last run '
            f'<strong>{job}</strong> at {ended}.</div>')


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
    var left=Math.min(Math.max(8,r.left), window.innerWidth-tw-8);
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
  document.addEventListener('click',function(){ if(pinned) unpin(); });
  document.addEventListener('keydown',function(ev){ if(ev.key==='Escape'){ unpin(); closeGloss(); } });
  var lb=document.getElementById('learnBtn');
  function setLearn(on){ document.body.classList.toggle('learn',on); lb.classList.toggle('on',on);
    lb.setAttribute('aria-pressed',on?'true':'false');
    try{ localStorage.setItem('qt_learn',on?'1':'0'); }catch(e){} }
  lb.addEventListener('click',function(){ setLearn(!document.body.classList.contains('learn')); });
  try{ if(localStorage.getItem('qt_learn')==='1') setLearn(true); }catch(e){}
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
  function closeGloss(){ if(gloss) gloss.classList.remove('show'); }
  document.getElementById('glossBtn').addEventListener('click',openGloss);
  document.getElementById('glossX').addEventListener('click',closeGloss);
  gloss.addEventListener('click',function(ev){ if(ev.target===gloss) closeGloss(); });
  search.addEventListener('input',function(){ renderGloss(search.value); });
})();"""


def dashboard_html(data: dict) -> str:
    regime = data.get("regime") or {}
    rlabel = regime.get("label", "unknown")
    rconf = regime.get("confidence")
    snap = data.get("latest_snapshot") or {}
    picks = data.get("top_picks") or []
    sectors = data.get("sectors") or {}
    decisions = data.get("decisions") or []
    as_of = data.get("as_of", "")

    total_value = snap.get("total_value")
    pnl = ((snap.get("unrealized_pnl") or 0) + (snap.get("realized_pnl_ytd") or 0)
           if snap else None)
    dd = snap.get("drawdown_from_peak")
    kpis = "".join([
        _kpi("Portfolio", money(total_value) if total_value else "$10,000", "paper money",
             key="paper_value"),
        _kpi("Total P&L", money(pnl) if pnl is not None else "$0.00",
             "realized + unrealized", tone="pos" if (pnl or 0) >= 0 else "neg",
             key="total_pnl"),
        _kpi("Drawdown", pct(dd) if dd is not None else "0.0%", "from peak",
             tone="neg" if (dd or 0) < 0 else "", key="drawdown"),
        _kpi("Positions", str(snap.get("n_positions", len(data.get("positions") or []))),
             "open paper holdings", key="positions"),
        _kpi("Cash", money(snap.get("cash")) if snap.get("cash") is not None else "$10,000",
             "available", key="cash"),
        _kpi("Picks", str(len(picks)), "top-overall this run", key="top_overall"),
    ])

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
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0d1117; color: #e6edf3;
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px 20px 64px; }}
  header {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    border-bottom: 1px solid #21262d; padding-bottom: 16px; margin-bottom: 18px; }}
  header h1 {{ font-size: 22px; margin: 0; font-weight: 650; }}
  .badge {{ padding: 3px 12px; border-radius: 999px; font-weight: 650; font-size: 13px;
    text-transform: uppercase; letter-spacing: .04em; color: #0d1117; }}
  .updated {{ margin-left: auto; color: #8b949e; font-size: 13px; }}
  .nav {{ display: flex; flex-wrap: wrap; gap: 6px 14px; margin-bottom: 22px; font-size: 13px; }}
  .nav a {{ color: #58a6ff; text-decoration: none; }}
  .nav a:hover {{ text-decoration: underline; }}
  .runbar {{ border-radius: 10px; padding: 9px 14px; margin-bottom: 18px; font-size: 13px;
    border: 1px solid; }}
  .runbar.ok {{ background: #0f2417; border-color: #1f5132; color: #59d27e; }}
  .runbar.fail {{ background: #2d1213; border-color: #6e2528; color: #ff7b72; }}
  .runbar.warn {{ background: #2b2412; border-color: #6b5722; color: #e3b341; }}
  .runbar code {{ font-family: ui-monospace, monospace; }}
  .grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 22px; }}
  .kpi {{ background: #161b22; border: 1px solid #21262d; border-radius: 12px; padding: 14px; }}
  .kpi-label {{ color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }}
  .kpi-val {{ font-size: 21px; font-weight: 680; margin-top: 4px; }}
  .kpi-sub {{ color: #6e7681; font-size: 11px; margin-top: 2px; }}
  .kpi.pos .kpi-val {{ color: #3fb950; }} .kpi.neg .kpi-val {{ color: #f85149; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
  .card {{ background: #161b22; border: 1px solid #21262d; border-radius: 12px;
    padding: 18px 20px; margin-bottom: 18px; }}
  .card h2 {{ font-size: 15px; margin: 0 0 12px; font-weight: 640; }}
  .card h2 .muted {{ font-weight: 400; }}
  .muted {{ color: #8b949e; font-size: 13px; margin: 0 0 10px; }}
  table.tbl {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  .tbl th {{ text-align: left; color: #8b949e; font-weight: 550; font-size: 12px;
    text-transform: uppercase; letter-spacing: .03em; padding: 6px 8px; border-bottom: 1px solid #21262d; }}
  .tbl td {{ padding: 7px 8px; border-bottom: 1px solid #1c2129; }}
  .tbl tr:last-child td {{ border-bottom: 0; }}
  td.pos {{ color: #3fb950; }} td.neg {{ color: #f85149; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 10px; }}
  .chip {{ background: #0d1117; border: 1px solid #21262d; border-radius: 10px;
    padding: 8px 12px; font-size: 12px; color: #8b949e; }}
  .chip span {{ display: block; font-size: 19px; font-weight: 680; color: #e6edf3; }}
  .chip.neg span {{ color: #f85149; }}
  .pick {{ padding: 10px 0; border-bottom: 1px solid #1c2129; }}
  .pick:last-child {{ border-bottom: 0; }}
  .pick-hd {{ display: flex; align-items: baseline; gap: 10px; }}
  .pick-sec {{ color: #8b949e; font-size: 12px; }}
  .pick-score {{ margin-left: auto; font-variant-numeric: tabular-nums; color: #3fb950; font-weight: 640; }}
  .sigs {{ margin-top: 7px; display: grid; gap: 3px; }}
  .sigrow {{ display: grid; grid-template-columns: 78px 1fr 36px; align-items: center; gap: 8px; }}
  .siglbl {{ color: #6e7681; font-size: 11px; }}
  .sigbar {{ background: #0d1117; border-radius: 4px; height: 7px; overflow: hidden; }}
  .sigbar i {{ display: block; height: 100%; background: #388bfd; }}
  .sigval {{ color: #8b949e; font-size: 11px; text-align: right; font-variant-numeric: tabular-nums; }}
  .feed {{ list-style: none; margin: 0; padding: 0; }}
  .feed li {{ padding: 9px 0; border-bottom: 1px solid #1c2129; font-size: 14px; }}
  .feed li:last-child {{ border-bottom: 0; }}
  .chart {{ width: 100%; height: auto; }}
  .chart .axis {{ fill: #6e7681; font-size: 11px; }}
  .empty {{ color: #6e7681; font-size: 14px; padding: 14px 0; }}
  .copilot p {{ font-size: 14.5px; }}
  footer {{ color: #6e7681; font-size: 12px; margin-top: 26px; border-top: 1px solid #21262d; padding-top: 14px; }}
  @media (max-width: 820px) {{ .grid {{ grid-template-columns: repeat(2, 1fr); }}
    .cols {{ grid-template-columns: 1fr; }} }}

  /* ── educational layer ── */
  .toolbar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
    margin: 2px 0 18px; }}
  .toolbar button {{ font: inherit; font-size: 13px; cursor: pointer; color: #c9d1d9;
    background: #161b22; border: 1px solid #30363d; border-radius: 999px;
    padding: 6px 14px; display: inline-flex; align-items: center; gap: 6px; }}
  .toolbar button:hover {{ border-color: #58a6ff; color: #e6edf3; }}
  .toolbar .on {{ background: #132a18; border-color: #2ea043; color: #59d27e; }}
  .toolbar .hint {{ color: #6e7681; font-size: 12px; }}
  button.i {{ all: unset; cursor: help; display: inline-flex; align-items: center;
    justify-content: center; width: 15px; height: 15px; margin-left: 5px;
    border-radius: 50%; background: #21303f; color: #58a6ff; font-size: 10px;
    font-weight: 700; vertical-align: middle; line-height: 1; }}
  button.i:hover, button.i:focus-visible {{ background: #1f6feb; color: #fff; outline: none; }}
  button.i:focus-visible {{ box-shadow: 0 0 0 2px #1f6feb55; }}
  .tterm {{ color: #6e7681; font-weight: 400; }}
  .tlbl {{ display: inline-flex; align-items: center; }}
  .explain {{ display: none; }}
  body.learn .explain {{ display: block; color: #8b949e; font-size: 11.5px;
    font-weight: 400; line-height: 1.45; margin-top: 3px; max-width: 46ch; }}
  body.learn .kpi {{ min-height: 0; }}
  .h2sub {{ display: block; color: #8b949e; font-size: 12px; font-weight: 400; margin-top: 3px; }}
  .eqdot {{ opacity: 0; transition: opacity .12s; }}
  .eqpt:hover .eqdot {{ opacity: 1; }}
  /* tooltip popover (hover/focus = short; click = full explainer) */
  #tip {{ position: fixed; z-index: 50; max-width: 320px; background: #1c2333;
    border: 1px solid #30435f; border-radius: 10px; padding: 12px 14px;
    box-shadow: 0 8px 28px #0009; font-size: 13px; line-height: 1.5;
    color: #e6edf3; pointer-events: none; opacity: 0; transition: opacity .1s; }}
  #tip.show {{ opacity: 1; }}
  #tip.rich {{ pointer-events: auto; }}
  #tip h4 {{ margin: 0 0 6px; font-size: 13px; }}
  #tip h4 small {{ color: #8b949e; font-weight: 400; }}
  #tip .ex {{ margin-top: 8px; padding: 8px 10px; background: #11161f; border-radius: 7px;
    border-left: 3px solid #2ea043; color: #c9d1d9; }}
  #tip .th {{ margin-top: 8px; color: #8b949e; font-style: italic; }}
  #tip .more {{ margin-top: 8px; color: #58a6ff; font-size: 12px; }}
  /* glossary modal */
  #gloss {{ position: fixed; inset: 0; z-index: 60; background: #010409cc;
    display: none; padding: 40px 16px; overflow: auto; }}
  #gloss.show {{ display: block; }}
  #gloss .panel {{ max-width: 760px; margin: 0 auto; background: #0d1117;
    border: 1px solid #30363d; border-radius: 14px; padding: 22px 24px; }}
  #gloss .gh {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
  #gloss h2 {{ margin: 0; font-size: 18px; }}
  #gloss input {{ flex: 1; font: inherit; font-size: 14px; background: #161b22;
    border: 1px solid #30363d; border-radius: 8px; padding: 8px 12px; color: #e6edf3; }}
  #gloss .x {{ all: unset; cursor: pointer; color: #8b949e; font-size: 22px; padding: 0 6px; }}
  #gloss .g {{ padding: 12px 0; border-bottom: 1px solid #1c2129; }}
  #gloss .g h3 {{ margin: 0 0 4px; font-size: 14px; }}
  #gloss .g h3 small {{ color: #6e7681; font-weight: 400; }}
  #gloss .g p {{ margin: 4px 0 0; font-size: 13px; color: #b3bcc7; line-height: 1.5; }}
  #gloss .g .ex {{ color: #7d8896; font-size: 12.5px; }}
  /* onboarding intro */
  .intro {{ background: #11202e; border: 1px solid #1f425f; border-radius: 12px;
    padding: 14px 18px; margin-bottom: 18px; font-size: 13.5px; color: #c9d1d9; }}
  .intro[hidden] {{ display: none; }}
  .intro b {{ color: #e6edf3; }}
  .intro ul {{ margin: 8px 0 0; padding-left: 18px; }} .intro li {{ margin: 3px 0; }}
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
  <div class="grid">{kpis}</div>

  {_screener_stats(data.get("summary"))}
  {_card(_title("\U0001F4C8", "Equity curve — strategy vs the market", "equity_curve"),
         _svg_equity(data.get("snapshots") or []))}

  <div class="cols">
    {_picks_section(picks, sectors)}
    {_card("\U0001F9E0 Recent decisions", feed_html)}
  </div>
  <div class="cols">
    {_sector_table(sectors)}
    {_vetoes_section(sectors, data.get("summary"))}
  </div>

  {_positions_section(data.get("positions") or [])}
  {_sentiment_section(data.get("sentiment"))}
  {_signal_lab_section(data.get("signal_lab") or {})}
  {_tournament_section(data.get("tournament") or {})}
  {_scorecard_section(data.get("scorecard"))}
  {_copilot_section(data.get("copilot") or {})}

  <footer>Auto-generated by quant-tracker — do not edit; regenerated each run.
  Paper money, research only — not financial advice. Reloads every {_REFRESH_SECONDS // 60} min.</footer>
</div>
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
