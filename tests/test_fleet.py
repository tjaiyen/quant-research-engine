"""Strategy fleet: registry sanity, member-cache re-ranking, leaderboard reads,
note + dashboard rendering. No live signals, no network, no real member state."""
from __future__ import annotations

import sqlite3
import subprocess
import sys

from auto_trader import fleet


def _fixture_cache():
    def stock(t, arima, kalman, garch, mc, sharpe, veto=True, reason=None):
        return {"ticker": t, "rank": 0, "composite_score": 0.5,
                "passed_veto": veto, "veto_reason": reason,
                "signal_scores": {"arima": arima, "kalman": kalman,
                                  "garch": garch, "monte_carlo": mc,
                                  "sharpe": sharpe}}
    return {
        "generated_at": "2026-07-01T06:00:00Z", "_cached_at": "2026-07-01T06:00:00Z",
        "regime": {"label": "sideways", "confidence": 1.0,
                   "probabilities": {"sideways": 1.0, "bull": 0.0, "bear": 0.0},
                   "blended_weights": {"arima": 0.62, "sharpe": 0.38, "kalman": 0.0,
                                       "garch": 0.0, "monte_carlo": 0.0}},
        "sectors": {
            "Tech": [stock("AAA", 0.9, 0.1, 0.1, 0.1, 0.2),
                     stock("BBB", 0.2, 0.9, 0.9, 0.9, 0.9),
                     stock("CCC", 0.5, 0.5, 0.5, 0.5, 0.5, veto=False,
                           reason="EARNINGS_BLACKOUT")],
        },
        "summary": {},
    }


def test_registry_sane():
    ids = [m["id"] for m in fleet.FLEET]
    assert len(ids) == len(set(ids))                      # unique ids
    kinds = {m["id"]: m.get("kind") for m in fleet.FLEET}
    assert kinds["candidate"] == "flagship" and kinds["spy"] == "hold"
    valid = {"arima", "kalman", "garch", "monte_carlo", "sharpe", "momentum"}
    for m in fleet.FLEET:
        for k in (m.get("weights") or {}):
            assert k in valid, f"{m['id']} weights unknown signal {k}"
        if m.get("min_composite") is not None:
            # config self-validation requires > SIGNAL_EXIT_THRESHOLD (0.45)
            assert m["min_composite"] > 0.45


def test_rescore_math():
    row = {"ticker": "AAA", "signal_scores": {"arima": 0.8, "sharpe": 0.4}}
    assert fleet.rescore(row, {"arima": 1.0}) == 0.8
    assert abs(fleet.rescore(row, {"arima": 0.5, "sharpe": 0.5}) - 0.6) < 1e-9
    # momentum comes from the side map, not the cache
    assert fleet.rescore(row, {"momentum": 1.0}, {"AAA": 0.7}) == 0.7


def test_build_member_cache_reranks_per_weights(monkeypatch):
    shared = _fixture_cache()
    # ARIMA-only member: AAA (0.9) must outrank BBB (0.2)
    arima = next(m for m in fleet.FLEET if m["id"] == "arima")
    out = fleet.build_member_cache(shared, arima)
    tech = out["sectors"]["Tech"]
    assert tech[0]["ticker"] == "AAA" and tech[0]["rank"] == 1
    assert abs(tech[0]["composite_score"] - 0.9) < 1e-6
    # equal-weight member: BBB (mean 0.76) must outrank AAA (mean 0.28)
    equal = next(m for m in fleet.FLEET if m["id"] == "equal")
    out2 = fleet.build_member_cache(shared, equal)
    assert out2["sectors"]["Tech"][0]["ticker"] == "BBB"
    # veto flags preserved; vetoed stock never enters top_overall
    ccc = next(s for s in out2["sectors"]["Tech"] if s["ticker"] == "CCC")
    assert ccc["passed_veto"] is False and ccc["veto_reason"] == "EARNINGS_BLACKOUT"
    assert all(p["ticker"] != "CCC" for p in out2["summary"]["top_overall"])
    # the shared cache is NOT mutated
    assert shared["sectors"]["Tech"][0]["composite_score"] == 0.5


def test_momentum_member_uses_side_scores(monkeypatch):
    shared = _fixture_cache()
    monkeypatch.setattr(fleet, "_momentum_scores",
                        lambda tickers: {t: 0.95 for t in tickers})
    asm = next(m for m in fleet.FLEET if m["id"] == "asm")
    out = fleet.build_member_cache(shared, asm)
    aaa = next(s for s in out["sectors"]["Tech"] if s["ticker"] == "AAA")
    # 0.5*0.9 + 0.3*0.2 + 0.2*0.95 = 0.70
    assert abs(aaa["composite_score"] - 0.70) < 1e-6


def test_min_composite_env_override():
    out = subprocess.run(
        [sys.executable, "-c",
         "import os; os.environ['MIN_COMPOSITE_TO_BUY']='0.47'; "
         "import auto_trader.config as c; print(c.MIN_COMPOSITE_TO_BUY)"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "0.47"


def _mini_db(path, snaps):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE portfolio_snapshots (snapshot_date TEXT PRIMARY KEY,"
                 "total_value REAL, benchmark_value REAL, n_positions INTEGER)")
    for d, v, b, n in snaps:
        conn.execute("INSERT INTO portfolio_snapshots VALUES (?,?,?,?)", (d, v, b, n))
    conn.commit()
    conn.close()


def test_fleet_reads_leaderboard(tmp_path, monkeypatch):
    from render import build
    fake = [
        {"id": "winner", "label": "Winner", "kind": "strategy"},
        {"id": "loser", "label": "Loser", "kind": "strategy"},
        {"id": "pending", "label": "Pending", "kind": "strategy"},
    ]
    monkeypatch.setattr(fleet, "FLEET", fake)
    monkeypatch.setattr(fleet, "FLEET_DIR", tmp_path)
    (tmp_path / "winner").mkdir()
    (tmp_path / "loser").mkdir()
    _mini_db(tmp_path / "winner" / "portfolio.db",
             [("2026-07-01", 10000, 100, 16), ("2026-07-08", 10500, 101, 16)])
    _mini_db(tmp_path / "loser" / "portfolio.db",
             [("2026-07-01", 10000, 100, 16), ("2026-07-08", 9800, 101, 16)])
    rows = build.fleet_reads()
    assert [r["id"] for r in rows] == ["winner", "loser", "pending"]  # ranked, pending last
    w = rows[0]
    assert abs(w["ret_pct"] - 5.0) < 1e-6
    assert abs(w["spy_pct"] - 1.0) < 1e-6
    assert abs(w["excess_pct"] - 4.0) < 1e-6
    assert rows[2]["value"] is None                        # pending member


def test_fleet_note_and_section_render():
    from render import html, notes
    rows = [{"id": "candidate", "label": "ARIMA+Sharpe (live)", "kind": "flagship",
             "value": 10121.23, "pnl": 121.23, "ret_pct": 1.2, "spy_pct": 1.6,
             "excess_pct": -0.4, "n_positions": 16},
            {"id": "spy", "label": "SPY buy-hold (control)", "kind": "hold",
             "value": None, "pnl": None, "ret_pct": None, "spy_pct": None,
             "excess_pct": None, "n_positions": None}]
    md = notes.fleet_note({"as_of": "x", "rows": rows})
    assert "Strategy fleet" in md and "LIVE" in md and "control" in md
    assert "type: tracker-fleet" in md
    assert "seeds at the next monthly" in notes.fleet_note({"rows": []})
    out = html._fleet_section(rows)
    assert "Strategy fleet" in out and "LIVE" in out and "CONTROL" in out
    assert "pending" in out                                # not-yet-seeded member
    assert html._fleet_section([]) == ""                   # no fleet → no card
    empty = html._fleet_section([{"id": "x", "label": "X", "kind": "strategy",
                                  "value": None, "pnl": None, "ret_pct": None,
                                  "spy_pct": None, "excess_pct": None,
                                  "n_positions": None}])
    assert "No member books yet" in empty


def test_fleet_glossary_term_defined():
    from render import glossary
    assert glossary.has("fleet")


# ── Phase 25: tournament variants ────────────────────────────────────────────

def test_registry_has_tournament_variants():
    ids = {m["id"] for m in fleet.FLEET}
    assert {"inverse", "sharpe", "top5", "random20"} <= ids
    assert len(ids) == 10
    inv = next(m for m in fleet.FLEET if m["id"] == "inverse")
    assert inv.get("invert") is True and inv["group"] == "tournament"
    r20 = next(m for m in fleet.FLEET if m["id"] == "random20")
    assert r20.get("random_n") == 20 and r20["group"] == "control"


def test_inverse_flips_ranking_but_respects_veto():
    shared = _fixture_cache()
    inv = next(m for m in fleet.FLEET if m["id"] == "inverse")
    out = fleet.build_member_cache(shared, inv)
    tech = out["sectors"]["Tech"]
    # default blend ranks BBB above AAA (sideways blend favours sharpe);
    # inverted, the LOWER-blend stock must rank first among all
    scores = {s["ticker"]: s["composite_score"] for s in tech}
    assert scores["AAA"] + scores["BBB"] != 0  # sanity
    ranked = [s["ticker"] for s in tech]
    # whoever ranks first must have the LOWEST un-inverted blend
    blend = fleet._default_weights(shared)
    raw = {s["ticker"]: fleet.rescore(s, blend)
           for s in _fixture_cache()["sectors"]["Tech"]}
    assert raw[ranked[0]] <= raw[ranked[-1]]
    # synthetic band clears the buy floor (a naive 1−score choked the first
    # seed to 2 buys — every score must be floor-compatible AND rank-inverted)
    assert all(0.60 <= s["composite_score"] <= 0.75 for s in tech)
    # vetoed stock still never in top_overall
    assert all(p["ticker"] != "CCC" for p in out["summary"]["top_overall"])


def test_random20_deterministic_and_veto_safe():
    shared = _fixture_cache()
    r20 = next(m for m in fleet.FLEET if m["id"] == "random20")
    out1 = fleet.build_member_cache(shared, r20)
    out2 = fleet.build_member_cache(shared, r20)
    s1 = {s["ticker"]: s["composite_score"] for s in out1["sectors"]["Tech"]}
    s2 = {s["ticker"]: s["composite_score"] for s in out2["sectors"]["Tech"]}
    assert s1 == s2                                    # month-seeded, idempotent
    assert s1["CCC"] == 0.0                            # vetoed → never selected
    chosen = [t for t, v in s1.items() if v > 0]
    assert set(chosen) <= {"AAA", "BBB"}               # only veto-passers
    # a different month reshuffles (different generated_at → different seed OK)
    shared2 = _fixture_cache()
    shared2["generated_at"] = "2026-08-01T06:00:00Z"
    out3 = fleet.build_member_cache(shared2, r20)      # must not raise
    assert out3["_fleet_member"] == "random20"


def test_member_env_merges_extras():
    top5 = next(m for m in fleet.FLEET if m["id"] == "top5")
    env = fleet.member_env(top5)
    assert env["TOP_N_PER_SECTOR"] == "5"
    assert "fleet/top5" in env["TRADER_DB_PATH"].replace("\\", "/")


def test_top_n_env_override():
    out = subprocess.run(
        [sys.executable, "-c",
         "import os; os.environ['TOP_N_PER_SECTOR']='5'; "
         "import auto_trader.config as c; print(c.TOP_N_PER_SECTOR)"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "5"


def test_tourney_badge_renders():
    from render import html
    rows = [{"id": "inverse", "label": "Worst-ranked (inverse)", "kind": "strategy",
             "group": "tournament", "value": 10000.0, "pnl": 0.0, "ret_pct": 0.0,
             "spy_pct": 0.0, "excess_pct": 0.0, "n_positions": 20},
            {"id": "random20", "label": "Random 20", "kind": "strategy",
             "group": "control", "value": 10000.0, "pnl": 0.0, "ret_pct": 0.0,
             "spy_pct": 0.0, "excess_pct": 0.0, "n_positions": 20}]
    out = html._fleet_section(rows)
    assert "TOURNEY" in out and "CONTROL" in out
