"""cli/track.py — the `track` command (engine driver).

Replaces the Dash app + Fly cron with local rituals:

    track doctor                 off-Drive preflight (store local, vault canonical)
    track refresh [--full]       pull watchlist prices + sector ETF performance (daily)
    track seed [--full|--refresh] seed the 220-stock screener universe into the cache
    track screen  [--retrain]    run the regime-aware screener (weekly)
    track paper monitor          daily monitor — stops, decay rescore, equity snapshot
    track paper cycle            monthly buy cycle (no-op outside the buy window)
    track paper stop [--clear]   set / clear the trading halt flag
    track report                 regenerate the Obsidian notes in `90 Tracker/`
    track score                  grade past picks vs actual returns (Scorecard.md)
    track review                 weekly-review slide deck (Review.md; Slides Extended)
    track clusters               k-means diversification clusters (Clusters.md)
    track sentiment              FinBERT news-sentiment overlay (Sentiment.md)
    track copilot                AI co-pilot's take on the latest cycle (Copilot.md; opt-in)
    track signal-lab             per-signal IC + a validated re-weighting (SignalLab.md)
    track tournament             race ~20 strategy variants over history (Tournament.md; ~15-25 min)
    track sim                    strategy portfolio backtest (StrategyBacktest.md; ~10-15 min)
    track backtest               retrospective skill check (Backtest.md; ~minutes)
    track status                 quick terminal summary

Every command that touches the DB or the vault runs `doctor` first and aborts
on a non-zero result (the off-Drive invariant, enforced mechanically). The
process chdir's to the repo root so the engine's relative paths resolve.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# NOTE: the chdir to REPO_ROOT happens in main(), not at import, so importing
# this module (e.g. in tests) has no side effect on the process working dir.


# ── Preflight gate ───────────────────────────────────────────────────────────

def _preflight() -> None:
    """Run doctor; abort the command on any unsafe path. Off-Drive invariant."""
    import doctor

    report = doctor.run()
    if not report["all_safe"]:
        print("✗ doctor preflight FAILED — refusing to touch the DB or vault.\n",
              file=sys.stderr)
        doctor.main([])  # human-readable detail
        raise SystemExit(2)


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_doctor(args: argparse.Namespace) -> int:
    import doctor

    return doctor.main(["--json"] if args.json else [])


def cmd_refresh(args: argparse.Namespace) -> int:
    _preflight()
    from tasks import refresh_prices, refresh_sectors

    extra = ["--full"] if args.full else []
    rc = refresh_prices.main(extra)
    rc |= refresh_sectors.main(extra)
    return rc


def cmd_seed(args: argparse.Namespace) -> int:
    _preflight()
    from tasks import seed_universe

    extra: list[str] = []
    if args.refresh:
        extra.append("--refresh")
    elif args.full:
        extra.append("--full")
    return seed_universe.main(extra)


def cmd_screen(args: argparse.Namespace) -> int:
    _preflight()
    from screener.screener_main import print_summary, run_screener

    results = run_screener(force_retrain=args.retrain)
    print_summary(results)
    # Close the autonomous loop: refresh the auto_trader's screener cache so the
    # monthly buy cycle reads fresh picks (copies the run JSON + stamps
    # _cached_at). Best-effort — a cache hiccup must not fail the screen.
    try:
        from auto_trader.scripts.pre_run_screener import main as prep_main
        prep_main(["--skip-run"])
        print("  trader cache refreshed (monthly buy will use these picks)")
    except Exception as exc:
        print(f"  warning: could not refresh trader cache: {exc}", file=sys.stderr)
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    _preflight()
    if args.action == "monitor":
        from auto_trader.scripts.daily_run import main as run
        return run([])
    if args.action == "cycle":
        from auto_trader.scripts.monthly_run import main as run
        return run([])
    if args.action == "stop":
        from auto_trader.scripts.emergency_stop import main as run
        return run(["--clear"] if args.clear else [])
    if args.action == "repair":
        import os
        from pathlib import Path
        from mock_broker import repair_to_real_entry
        state = os.getenv("MOCK_BROKER_STATE",
                          str(REPO_ROOT / "store" / "mock_broker.json"))
        changed = repair_to_real_entry(state)
        if not changed:
            print("Paper repair: nothing to fix (already real, or no cached "
                  "entry prices). Run `track paper monitor` to refresh marks.")
            return 0
        print(f"Repaired {len(changed)} position(s) to real entry prices "
              f"(dollars preserved):")
        for c in changed:
            print(f"  {c['ticker']:<6} entry ${c['entry_price']:.2f} "
                  f"({c['entry_date']}) · {c['shares']:.3f} sh · "
                  f"${c['dollars']:.0f} invested")
        print("→ run `track paper monitor` then `track report` to mark-to-market.")
        return 0
    print(f"unknown paper action: {args.action}", file=sys.stderr)
    return 2


def cmd_report(args: argparse.Namespace) -> int:
    _preflight()
    from render.build import build_all
    from render.markdown import tracker_dir

    _write_readme_if_absent(tracker_dir())
    summary = build_all()
    print(f"Rendered {len(summary['written'])} notes → {summary['vault']}")
    print(f"  screener run: {'yes' if summary['had_screener_run'] else 'none yet'} · "
          f"positions: {summary['n_positions']} · "
          f"equity snapshots: {summary['n_snapshots']} · "
          f"pruned closed positions: {summary['pruned_positions']}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    _preflight()
    from render.build import build_all
    from render.markdown import tracker_dir
    from screener.backtest.scorecard import compute_scorecard
    from render import notes
    from render.markdown import atomic_write

    data = compute_scorecard()
    snaps = []
    try:
        from auto_trader.state.portfolio_db import get_portfolio_snapshots
        snaps = get_portfolio_snapshots(days=3650)
    except Exception:
        pass
    atomic_write(tracker_dir() / "Scorecard.md", notes.scorecard_note(data, snaps))
    h = data["horizons"]
    # Echo the same plain verdict the note leads with (strip markdown emphasis).
    verdict, _ = notes._scorecard_verdict(h)
    plain = verdict.replace("**", "").replace("[[", "").replace("]]", "")
    print(f"Scorecard written → {tracker_dir()}/Scorecard.md")
    print(f"  {plain}")
    print(f"  graded {data['n_graded_runs']}/{data['n_runs']} runs · "
          f"7d={h['7d']['n']} 28d={h['28d']['n']} 84d={h['84d']['n']} picks")
    return 0


def cmd_clusters(args: argparse.Namespace) -> int:
    _preflight()
    from render import notes
    from render.markdown import atomic_write, tracker_dir
    from screener.analysis.clustering import compute_clusters

    data = compute_clusters(k=args.k, lookback=args.lookback)
    atomic_write(tracker_dir() / "Clusters.md", notes.clusters_note(data))
    print(f"Clusters written → {tracker_dir()}/Clusters.md")
    if data.get("clusters"):
        sil = data.get("silhouette")
        print(f"  {data['k']} clusters over {data['n_tickers']} stocks "
              f"(silhouette {'n/a' if sil is None else f'{sil:.3f}'}, "
              f"{data.get('n_skipped', 0)} skipped)")
    else:
        print("  not enough cached price history yet — run `track seed` first.")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    _preflight()
    from datetime import datetime, timezone

    from render import build, slides
    from render.markdown import atomic_write, tracker_dir
    from screener.backtest.scorecard import compute_scorecard

    results = build.latest_screener_results() or {}
    regime = results.get("regime", {})
    top = results.get("summary", {}).get("top_overall", [])
    snaps = []
    try:
        from auto_trader.state.portfolio_db import get_portfolio_snapshots
        snaps = get_portfolio_snapshots(days=3650)
    except Exception:
        pass
    deck = slides.review_deck(regime, top, compute_scorecard(), snaps,
                              as_of=datetime.now(timezone.utc).isoformat())
    atomic_write(tracker_dir() / "Review.md", deck)
    print(f"Review deck written → {tracker_dir()}/Review.md")
    print("  open in Obsidian with the 'Slides Extended' plugin to present; "
          "export to HTML to share.")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    _preflight()
    from datetime import datetime, timezone

    from render import notes
    from render.markdown import atomic_write, tracker_dir

    print("Retrospective backtest — re-runs the strategy over history.")
    print(f"  sampled for speed: {args.windows} windows · {args.samples} IC dates · "
          f"~{args.max_per_sector}/sector · ~{args.max_tickers} tickers (~15 min).")
    try:
        from screener.backtest.walk_forward import _summarize, run_walk_forward
        from screener.backtest.signal_ic import compute_signal_ic
        from screener.backtest.regime_accuracy import evaluate_regime_predictive_power

        print("  [1/3] walk-forward — do the top picks beat the average stock?")
        wf = _summarize(run_walk_forward(n_windows=args.windows,
                                         max_per_sector=args.max_per_sector))
        print("  [2/3] information coefficient — do the signals predict returns?")
        ic = compute_signal_ic(n_samples=args.samples, max_tickers=args.max_tickers)
        print("  [3/3] regime accuracy — is the bull/bear call real?")
        rg = evaluate_regime_predictive_power()
    except Exception as exc:
        print(f"\n✗ Backtest could not run: {exc}", file=sys.stderr)
        print("  Usually this means there isn't enough cached price history yet — "
              "run `./track seed --full` first, then retry.", file=sys.stderr)
        return 1

    data = {"as_of": datetime.now(timezone.utc).isoformat(),
            "walk_forward": wf, "ic": ic, "regime": rg}
    atomic_write(tracker_dir() / "Backtest.md", notes.backtest_note(data))
    print(f"\nBacktest written → {tracker_dir()}/Backtest.md")
    print(f"  walk-forward: {wf.get('n_windows')} windows, mean lift "
          f"{wf.get('mean_lift', 0):.4f}, win rate {wf.get('win_rate', 0):.0%}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    _preflight()
    from datetime import datetime, timezone

    from render import notes
    from render.markdown import atomic_write, tracker_dir
    from tasks import refresh_health
    from utils.db import (fetch_earnings, fetch_earnings_history,
                          fetch_latest_fundamentals, list_health, ticker_names)

    extra: list[str] = list(args.tickers or [])
    if args.universe:
        extra.append("--universe")
    if args.limit:
        extra += ["--limit", str(args.limit)]
    refresh_health.main(extra)

    names = ticker_names()
    rows = []
    for h in list_health():
        t = h["ticker"]
        fund = fetch_latest_fundamentals(t) or {}
        hist = fetch_earnings_history(t, limit=4)
        last = hist[0] if hist else {}
        rows.append({**h, "name": names.get(t), "pe": fund.get("pe"),
                     "peg": fund.get("peg"), "div_yield": fund.get("div_yield"),
                     "next_earnings": fetch_earnings(t), "earnings": hist,
                     "last_surprise_pct": last.get("surprise_pct")})
    data = {"as_of": datetime.now(timezone.utc).isoformat(), "rows": rows}
    atomic_write(tracker_dir() / "CompanyHealth.md", notes.company_health_note(data))
    print(f"Company-health note written → {tracker_dir()}/CompanyHealth.md "
          f"({len(rows)} companies)")
    return 0


def cmd_sentiment(args: argparse.Namespace) -> int:
    _preflight()
    from datetime import datetime, timezone

    from render import notes
    from render.markdown import atomic_write, tracker_dir
    from screener.config import SENTIMENT_VETO_ENABLED, SENTIMENT_VETO_THRESHOLD
    from tasks import refresh_sentiment
    from utils.db import list_sentiment

    extra: list[str] = list(args.tickers or [])
    if args.limit:
        extra += ["--limit", str(args.limit)]
    refresh_sentiment.main(extra)

    data = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "rows": list_sentiment(),
        "veto_enabled": SENTIMENT_VETO_ENABLED,
        "threshold": SENTIMENT_VETO_THRESHOLD,
    }
    atomic_write(tracker_dir() / "Sentiment.md", notes.sentiment_note(data))
    print(f"Sentiment note written → {tracker_dir()}/Sentiment.md")
    return 0


def cmd_signal_lab(args: argparse.Namespace) -> int:
    _preflight()
    import json
    from datetime import datetime, timezone

    from render import notes
    from render.markdown import atomic_write, tracker_dir
    from screener.signal_lab.lab import analyze_signals, recommend_weights
    from screener.tournament.panel import build_signal_panel
    from screener.tournament.run import _segment
    from screener.tournament.variants import _strat

    panel = build_signal_panel(years=args.years, rebalance=args.rebalance,
                               max_per_sector=args.max_per_sector,
                               use_cache=not args.rebuild)
    if len(panel.get("rows", [])) < 1:
        print("No signal panel yet — run `track tournament` first (it builds the panel).")
        return 1
    analysis = analyze_signals(panel)

    # Clean out-of-sample validation: derive the candidate from IN-SAMPLE dates
    # only, then judge it on the held-out dates (no leakage).
    segs = panel["segments"]
    n, n_is = len(segs), max(2, round(len(segs) * 0.66))
    is_dates = {s["d0"] for s in segs[:n_is]}
    panel_is = {**panel, "segments": segs[:n_is],
                "rows": [r for r in panel["rows"] if r["d0"] in is_dates]}
    cand_is = recommend_weights(analyze_signals(panel_is))

    def _oos(spec):
        eq = 1.0
        for seg in segs[n_is:]:
            rows = [r for r in panel["rows"] if r["d0"] == seg["d0"]]
            ret, _ = _segment(seg, rows, spec)
            eq *= (1.0 + ret)
        return eq - 1.0

    spy = 1.0
    for seg in segs[n_is:]:
        spy *= (1.0 + (seg.get("spy_return") or 0.0))
    val = {"candidate_oos": _oos(_strat("c", "candidate", weights=cand_is)),
           "default_oos": _oos(_strat("d", "weighting", weights=None)),
           "spy_oos": spy - 1.0, "n_oos": n - n_is}

    from screener.config import WEIGHT_MATRIX_MODE
    data = {"as_of": datetime.now(timezone.utc).isoformat(),
            "n_dates": analysis["n_dates"], "n_rows": analysis["n_rows"],
            "signals": analysis["signals"], "correlation": analysis["correlation"],
            "candidate_weights": recommend_weights(analysis), "validation": val,
            "mode": os.getenv("WEIGHT_MATRIX_MODE", WEIGHT_MATRIX_MODE)}
    atomic_write(tracker_dir() / "SignalLab.md", notes.signal_lab_note(data))
    try:
        sc = REPO_ROOT / "store" / "last_signal_lab.json"
        sc.parent.mkdir(parents=True, exist_ok=True)
        sc.write_text(json.dumps({
            "as_of": data["as_of"],
            "signals": {s: {"ic": d["ic"], "verdict": d["verdict"]}
                        for s, d in analysis["signals"].items()},
            "candidate_weights": data["candidate_weights"], "validation": val}))
    except Exception as exc:
        print(f"  (signal-lab sidecar not written: {exc})", file=sys.stderr)

    print("Per-signal predictive power (IC):")
    for s, d in sorted(analysis["signals"].items(), key=lambda kv: -(kv[1]["ic"] or -9)):
        print(f"  {s:12} IC {(d['ic'] or 0)*100:+5.1f}%   {d['verdict']}")
    print(f"\nOut-of-sample ({val['n_oos']} held-out quarters): candidate "
          f"{val['candidate_oos']*100:+.1f}% · default {val['default_oos']*100:+.1f}% · "
          f"SPY {val['spy_oos']*100:+.1f}%")
    print(f"→ {tracker_dir()}/SignalLab.md")
    return 0


def cmd_tournament(args: argparse.Namespace) -> int:
    _preflight()
    import json
    from datetime import datetime, timezone

    from render import notes
    from render.markdown import atomic_write, tracker_dir
    from screener.tournament.attribution import attribute
    from screener.tournament.panel import build_signal_panel
    from screener.tournament.run import run_tournament
    from screener.tournament.variants import default_variants

    print("Building signal panel (first run is slow ~15-25 min; cached after)…")
    panel = build_signal_panel(years=args.years, rebalance=args.rebalance,
                               max_per_sector=args.max_per_sector,
                               use_cache=not args.rebuild)
    if len(panel.get("rows", [])) < 1:
        print("Not enough cached history to build the panel — run `track seed` first.")
        return 1
    from screener.config import TOURNAMENT_COST_BPS
    cost_bps = args.costs_bps if args.costs_bps is not None else TOURNAMENT_COST_BPS
    print(f"Panel: {len(panel['rows'])} rows over {len(panel['segments'])} rebalances. "
          f"Running {len(default_variants())} variants (net of {cost_bps:.0f}bps)…")
    tour = run_tournament(panel, default_variants(), cost_bps=cost_bps)
    attr = attribute(tour, panel)
    data = {"as_of": datetime.now(timezone.utc).isoformat(),
            "n_segments": tour["n_segments"], "n_in_sample": tour["n_in_sample"],
            "cost_bps": cost_bps,
            "ranked": tour["ranked"], "attribution": attr,
            "years": args.years, "rebalance": args.rebalance}
    atomic_write(tracker_dir() / "Tournament.md", notes.tournament_note(data))
    try:  # sidecar so the HTML dashboard can show a leaderboard card
        sc = REPO_ROOT / "store" / "last_tournament.json"
        sc.parent.mkdir(parents=True, exist_ok=True)
        sc.write_text(json.dumps({
            "as_of": data["as_of"], "winner": attr.get("winner"),
            "verdict": attr.get("verdict"), "beat_spy": attr.get("beat_spy"),
            "beat_random": attr.get("beat_random"), "oos_rank": attr.get("oos_rank"),
            "leaderboard": [{"rank": r["rank"], "label": r["label"],
                             "group": r["group"],
                             "total": r["full"].get("total_return"),
                             "sharpe": r["full"].get("sharpe"),
                             "excess": r["full"].get("excess")} for r in tour["ranked"]],
        }))
    except Exception as exc:
        print(f"  (tournament sidecar not written: {exc})", file=sys.stderr)
    print(f"\n🏆 Winner: {attr.get('winner')}")
    print(attr.get("verdict", ""))
    print(f"→ {tracker_dir()}/Tournament.md")
    return 0


def cmd_copilot(args: argparse.Namespace) -> int:
    _preflight()
    import utils.config  # noqa: F401 — triggers load_dotenv so .env's ANTHROPIC_API_KEY is set
    from datetime import datetime, timezone

    from render import notes
    from render.build import _decisions, _paper_reads, latest_screener_results
    from render.markdown import atomic_write, tracker_dir
    from screener.copilot.advisor import copilot_review

    results = latest_screener_results() or {}
    paper = _paper_reads()
    snaps = paper.get("snapshots") or []
    latest = snaps[-1] if snaps else {}
    portfolio = {
        "total_value": latest.get("total_value"),
        "cash": latest.get("cash"),
        "n_positions": latest.get("n_positions", len(paper.get("positions", []))),
        "drawdown_from_peak": latest.get("drawdown_from_peak"),
    } if latest else {}

    verdict = None
    try:
        from screener.backtest.scorecard import compute_scorecard
        sc = compute_scorecard()
        verdict = sc.get("verdict") if isinstance(sc, dict) else None
    except Exception:
        pass

    decisions = _decisions(paper.get("trades", []))
    context = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "regime": results.get("regime", {}),
        "top_picks": results.get("summary", {}).get("top_overall", []),
        "portfolio": portfolio,
        "scorecard_verdict": verdict,
        "recent_decisions": [notes._decision_text(d) for d in decisions[:8]],
    }
    review = copilot_review(context)
    # Cache the take off-Drive so the HTML dashboard can show it without an API call.
    try:
        import json
        sidecar = REPO_ROOT / "store" / "last_copilot.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({**review, "as_of": context["as_of"],
                                       "regime": context.get("regime", {})}))
    except Exception as exc:
        print(f"  (could not cache co-pilot take: {exc})", file=sys.stderr)
    atomic_write(tracker_dir() / "Copilot.md", notes.copilot_note(review, context))
    if review.get("available"):
        print(f"Co-pilot take written → {tracker_dir()}/Copilot.md")
    else:
        print(f"Co-pilot off ({review.get('reason')}); wrote enablement note "
              f"→ {tracker_dir()}/Copilot.md")
    return 0


def cmd_sim(args: argparse.Namespace) -> int:
    _preflight()
    from datetime import datetime, timezone

    from render import notes
    from render.markdown import atomic_write, tracker_dir

    print(f"Strategy backtest — simulating {args.years}y of {args.rebalance}ly "
          f"rebalancing over history (~10–15 min, sampled)…")
    try:
        from screener.backtest.portfolio_backtest import run_portfolio_backtest
        data = run_portfolio_backtest(years=args.years, rebalance=args.rebalance,
                                      max_per_sector=args.max_per_sector)
    except Exception as exc:
        print(f"\n✗ Strategy backtest could not run: {exc}", file=sys.stderr)
        print("  Usually means not enough cached history — run `./track seed --full` first.",
              file=sys.stderr)
        return 1

    _ = datetime.now(timezone.utc)
    atomic_write(tracker_dir() / "StrategyBacktest.md", notes.strategy_backtest_note(data))
    m = data.get("metrics", {})
    print(f"\nStrategy backtest written → {tracker_dir()}/StrategyBacktest.md")
    if data.get("equity_curve"):
        print(f"  total {m.get('total_return', 0):+.1%} vs SPY {m.get('spy_total_return', 0):+.1%} "
              f"· CAGR {m.get('cagr', 0):+.1%} · maxDD {m.get('max_drawdown', 0):.1%} "
              f"· {data.get('n_rebalances')} rebalances")
    else:
        print("  not enough cached history — run `./track seed --full` first.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _preflight()
    from render.build import latest_screener_results
    from render.markdown import money, pct

    print("quant-tracker status\n" + "-" * 40)
    results = latest_screener_results()
    if results:
        r = results.get("regime", {})
        print(f"Regime:     {str(r.get('label','?')).upper()} "
              f"({pct(float(r.get('confidence',0) or 0))}) "
              f"as of {str(results.get('generated_at',''))[:10]}")
        top = results.get("summary", {}).get("top_overall", [])
        if top:
            picks = ", ".join(f"{s['ticker']}({s['composite_score']:.2f})" for s in top[:5])
            print(f"Top picks:  {picks}")
    else:
        print("Regime:     (no screener run yet — `track screen`)")

    try:
        from auto_trader.state import portfolio_db as pdb
        pdb.initialize_db()
        positions = pdb.get_all_positions()
        snaps = pdb.get_portfolio_snapshots(days=1)
        latest = snaps[-1] if snaps else {}
        print(f"Paper:      {len(positions)} open position(s) · "
              f"value {money(latest.get('total_value'))} · "
              f"unrealized {money(latest.get('unrealized_pnl'))}")
    except Exception as exc:
        print(f"Paper:      (ledger unavailable: {exc})")
    return 0


# ── Hand-authored vault README (written once, never regenerated) ─────────────

_README = """# 90 Tracker — generated by quant-tracker

Everything in this folder is **auto-generated** by the off-Drive engine at
`~/dev/quant-tracker` and is regenerated by `track report`. Do not hand-edit
these notes — your changes will be overwritten.

- `Dashboard.md` — regime, paper P&L, latest picks (Dataview views).
- `Regime.md` — current market regime + signal weights.
- `Screener/Run-*.md` — one note per screener run (per-sector top picks, vetoes).
- `Positions/<TICKER>.md` — one note per open paper position (Dataview source).
- `Journal/<date>.md` — paper-trading fills per day.
- `Performance.md` — equity curve, P&L, drawdown.

The Dashboard/Performance views need the **Dataview** community plugin enabled.
Source of truth is the SQLite cache in `~/dev/quant-tracker/store/` (rebuildable);
this folder is just the human-readable surface.
"""


def _write_readme_if_absent(tracker_root: Path) -> None:
    readme = tracker_root / "README.md"
    if not readme.exists():
        tracker_root.mkdir(parents=True, exist_ok=True)
        readme.write_text(_README, encoding="utf-8")


# ── Parser ───────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="track", description="Quant Tracker engine driver")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="off-Drive preflight")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    r = sub.add_parser("refresh", help="pull prices + sector performance (watchlist)")
    r.add_argument("--full", action="store_true", help="full history backfill")
    r.set_defaults(func=cmd_refresh)

    sd = sub.add_parser("seed", help="seed the 220-stock screener universe into the cache")
    sd.add_argument("--full", action="store_true", help="2y history (first-run bootstrap)")
    sd.add_argument("--refresh", action="store_true", help="top up only tickers >24h stale")
    sd.set_defaults(func=cmd_seed)

    s = sub.add_parser("screen", help="run the regime-aware screener")
    s.add_argument("--retrain", action="store_true", help="force HMM retrain")
    s.set_defaults(func=cmd_screen)

    pa = sub.add_parser("paper", help="paper-trading cycles (mock broker)")
    pa.add_argument("action", choices=["monitor", "cycle", "stop", "repair"])
    pa.add_argument("--clear", action="store_true", help="(stop) clear the halt flag")
    pa.set_defaults(func=cmd_paper)

    rep = sub.add_parser("report", help="regenerate Obsidian notes")
    rep.set_defaults(func=cmd_report)

    scp = sub.add_parser("score", help="grade past picks vs actual returns (Scorecard.md)")
    scp.set_defaults(func=cmd_score)

    rv = sub.add_parser("review", help="weekly-review slide deck (Review.md; Slides Extended)")
    rv.set_defaults(func=cmd_review)

    cl = sub.add_parser("clusters", help="k-means diversification clusters (Clusters.md)")
    cl.add_argument("--k", type=int, default=None, help="cluster count (default: auto via silhouette)")
    cl.add_argument("--lookback", type=int, default=252, help="trading days for vol/return (default 252)")
    cl.set_defaults(func=cmd_clusters)

    se = sub.add_parser("sentiment", help="FinBERT news-sentiment overlay (Sentiment.md)")
    se.add_argument("tickers", nargs="*", help="specific tickers (default: universe)")
    se.add_argument("--limit", type=int, default=None, help="only the first N tickers")
    se.set_defaults(func=cmd_sentiment)

    he = sub.add_parser("health", help="company-health snapshot (CompanyHealth.md)")
    he.add_argument("tickers", nargs="*", help="specific tickers (default: held positions)")
    he.add_argument("--universe", action="store_true", help="score the whole universe")
    he.add_argument("--limit", type=int, default=None, help="only the first N tickers")
    he.set_defaults(func=cmd_health)

    cp = sub.add_parser("copilot", help="AI co-pilot's take on the latest cycle (Copilot.md; opt-in)")
    cp.set_defaults(func=cmd_copilot)

    sl = sub.add_parser("signal-lab", help="per-signal IC + validated re-weighting (SignalLab.md)")
    sl.add_argument("--years", type=int, default=3)
    sl.add_argument("--rebalance", choices=["month", "quarter"], default="quarter")
    sl.add_argument("--max-per-sector", type=int, default=10, dest="max_per_sector")
    sl.add_argument("--rebuild", action="store_true", help="ignore the cached panel")
    sl.set_defaults(func=cmd_signal_lab)

    tn = sub.add_parser("tournament", help="race ~20 strategy variants over history (Tournament.md)")
    tn.add_argument("--years", type=int, default=3)
    tn.add_argument("--rebalance", choices=["month", "quarter"], default="quarter")
    tn.add_argument("--max-per-sector", type=int, default=10, dest="max_per_sector")
    tn.add_argument("--rebuild", action="store_true", help="ignore the cached panel")
    tn.add_argument("--costs-bps", type=float, default=None, dest="costs_bps",
                    help="round-trip transaction cost in bps (default: config 20)")
    tn.set_defaults(func=cmd_tournament)

    sm = sub.add_parser("sim", help="strategy portfolio backtest (StrategyBacktest.md; ~10-15 min)")
    sm.add_argument("--years", type=int, default=3, help="lookback years (default 3)")
    sm.add_argument("--rebalance", choices=["month", "quarter"], default="quarter")
    sm.add_argument("--max-per-sector", type=int, default=8, dest="max_per_sector",
                    help="candidates scored per sector (default 8)")
    sm.set_defaults(func=cmd_sim)

    bt = sub.add_parser("backtest", help="retrospective skill check (Backtest.md; ~minutes)")
    bt.add_argument("--windows", type=int, default=3, help="walk-forward windows (default 3)")
    bt.add_argument("--samples", type=int, default=4, help="IC sample dates (default 4)")
    bt.add_argument("--max-per-sector", type=int, default=8, dest="max_per_sector",
                    help="walk-forward: candidates per sector (default 8)")
    bt.add_argument("--max-tickers", type=int, default=60, dest="max_tickers",
                    help="IC: universe sample size (default 60)")
    bt.set_defaults(func=cmd_backtest)

    st = sub.add_parser("status", help="quick terminal summary")
    st.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    os.chdir(REPO_ROOT)  # engine modules use paths relative to the repo root
    import utils.config  # noqa: F401 — load .env once (VAULT_PATH, ANTHROPIC_API_KEY, …)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
