"""Valuation model (v2). Absolute + peer-relative blend.

v1 used only absolute equity-market bands (forward P/E 12/28, etc.).
v2 adds peer context — sector-relative median where we have ≥3 sector peers,
watchlist-relative percentile as a fallback, absolute as last resort.

Final score = 0.5 · absolute + 0.5 · peer (or 100% absolute if no peer data).
UI footers carry the method tag so the user always sees how it was scored.

Upgrade paths still open:
- Historical multiple vs ticker's own 10Y median.
- DDM / DCF scenarios for quality names.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class ValuationSignals:
    pe: float | None
    forward_pe: float | None
    ps: float | None
    pb: float | None
    peg: float | None
    score: float | None       # 0..1 (1 = attractively cheap); None = no data
    bucket: str               # 'attractive', 'fair', 'expensive', 'no_data'
    coverage: float           # 0..1 — fraction of weights covered by real data
    absolute_score: float | None  # band-based score component
    peer_score: float | None      # peer-relative score component (None if no peers)
    peer_type: str            # 'sector' | 'watchlist' | 'absolute'
    peer_group_size: int      # number of peers used (0 for absolute-only)
    sector: str | None


# (cheap_threshold, expensive_threshold, weight)
_BANDS = {
    # Under cheap -> score 1.0, over expensive -> 0.0, linear in between.
    "forward_pe": (12.0, 28.0, 0.40),
    "pe":         (12.0, 30.0, 0.20),
    "peg":        (1.0,  2.5,  0.20),
    "ps":         (1.5,  6.0,  0.10),
    "pb":         (1.2,  5.0,  0.10),
}


def _score_metric(value: float | None, cheap: float, expensive: float) -> float | None:
    if value is None or value <= 0:  # negative P/E = losses — flag as 'no signal'
        return None
    if value <= cheap:
        return 1.0
    if value >= expensive:
        return 0.0
    return 1.0 - (value - cheap) / (expensive - cheap)


def _bucket(score: float | None) -> str:
    if score is None:
        return "no_data"
    if score >= 0.65:
        return "attractive"
    if score >= 0.35:
        return "fair"
    return "expensive"


@dataclass(frozen=True)
class PeerContext:
    """Precomputed peer statistics for valuation scoring.

    - sector_medians: {sector_name: {metric: median_value}} for sectors with ≥3 members
    - watchlist_values: {metric: sorted list of values} for percentile ranking
    """
    sector_medians: dict[str, dict[str, float]]
    watchlist_values: dict[str, list[float]]


_SECTOR_MIN = 3       # min sector peers to use sector-relative
_WATCHLIST_MIN = 4    # min total watchlist values to use percentile rank


def build_peer_context(snapshots: dict[str, dict]) -> PeerContext:
    """Construct PeerContext from a dict of {ticker: fundamentals_snapshot}."""
    metrics = ("forward_pe", "pe", "peg", "ps", "pb")
    watchlist_values: dict[str, list[float]] = {m: [] for m in metrics}
    by_sector: dict[str, dict[str, list[float]]] = {}

    for _, snap in snapshots.items():
        if not snap:
            continue
        sector = snap.get("sector")
        for m in metrics:
            v = snap.get(m)
            if v is None or v <= 0:
                continue
            watchlist_values[m].append(float(v))
            if sector:
                by_sector.setdefault(sector, {m: [] for m in metrics})
                by_sector[sector][m].append(float(v))

    for m in metrics:
        watchlist_values[m].sort()

    sector_medians: dict[str, dict[str, float]] = {}
    for sector, metric_vals in by_sector.items():
        # Require ≥SECTOR_MIN members overall. Count by max list length.
        if max(len(v) for v in metric_vals.values()) < _SECTOR_MIN:
            continue
        medians_here: dict[str, float] = {}
        for m, vals in metric_vals.items():
            if len(vals) >= _SECTOR_MIN:
                medians_here[m] = float(median(vals))
        if medians_here:
            sector_medians[sector] = medians_here

    return PeerContext(sector_medians=sector_medians, watchlist_values=watchlist_values)


def _score_vs_median(value: float, peer_median: float) -> float:
    """Score a metric where lower = cheaper, using peer median as the center.

    value == median -> 0.5; value == 0.5*median -> 1.0; value == 2*median -> 0.0.
    Values are clipped to [0, 1].
    """
    if peer_median <= 0 or value <= 0:
        return 0.5
    ratio = value / peer_median
    # Linear in log-ratio: log2(0.5)=-1 -> 1.0 ; log2(2.0)=+1 -> 0.0
    import math
    log_ratio = math.log2(ratio)
    score = 0.5 - 0.5 * log_ratio
    return max(0.0, min(1.0, score))


def _score_vs_percentile(value: float, sorted_values: list[float]) -> float:
    """Score by percentile rank. Lower percentile = cheaper = higher score."""
    if not sorted_values:
        return 0.5
    # Rank: fraction of values >= this one (lower value -> higher score)
    n = len(sorted_values)
    # Simple rank: count of values strictly greater than `value` + 0.5 * ties.
    # Efficient enough at watchlist scale.
    greater = sum(1 for v in sorted_values if v > value)
    equal = sum(1 for v in sorted_values if v == value)
    rank = (greater + 0.5 * equal) / n
    return max(0.0, min(1.0, rank))


def _peer_score(
    metric_values: dict[str, float | None],
    peer_context: PeerContext | None,
    sector: str | None,
) -> tuple[float | None, str, int]:
    """Return (peer_score, peer_type, peer_group_size)."""
    if peer_context is None:
        return (None, "absolute", 0)

    # Try sector-relative first.
    if sector and sector in peer_context.sector_medians:
        medians = peer_context.sector_medians[sector]
        weighted_sum = 0.0
        weight_present = 0.0
        for metric, (_, _, weight) in _BANDS.items():
            v = metric_values.get(metric)
            if v is None or v <= 0 or metric not in medians:
                continue
            s = _score_vs_median(v, medians[metric])
            weighted_sum += s * weight
            weight_present += weight
        if weight_present > 0:
            # Peer group size ≈ median of list lengths in the sector.
            group_size = max(
                len(peer_context.watchlist_values.get(m, [])) for m in medians
            )
            return (weighted_sum / weight_present, "sector", group_size)

    # Watchlist-relative fallback.
    weighted_sum = 0.0
    weight_present = 0.0
    used_n = 0
    for metric, (_, _, weight) in _BANDS.items():
        v = metric_values.get(metric)
        sorted_peers = peer_context.watchlist_values.get(metric, [])
        if v is None or v <= 0 or len(sorted_peers) < _WATCHLIST_MIN:
            continue
        s = _score_vs_percentile(v, sorted_peers)
        weighted_sum += s * weight
        weight_present += weight
        used_n = max(used_n, len(sorted_peers))
    if weight_present > 0:
        return (weighted_sum / weight_present, "watchlist", used_n)

    return (None, "absolute", 0)


def compute_valuation(
    snapshot: dict | None,
    peer_context: PeerContext | None = None,
) -> ValuationSignals:
    """Score a fundamentals snapshot. Returns no_data if empty/ETF.

    When peer_context is provided, blends 50% absolute-band + 50% peer-relative.
    Peer-relative prefers sector (≥3 peers), falls back to watchlist percentile,
    and defers to absolute when neither has coverage.
    """
    empty = ValuationSignals(
        pe=None, forward_pe=None, ps=None, pb=None, peg=None,
        score=None, bucket="no_data", coverage=0.0,
        absolute_score=None, peer_score=None,
        peer_type="absolute", peer_group_size=0, sector=None,
    )
    if not snapshot:
        return empty

    metric_values = {
        "forward_pe": snapshot.get("forward_pe"),
        "pe":         snapshot.get("pe"),
        "peg":        snapshot.get("peg"),
        "ps":         snapshot.get("ps"),
        "pb":         snapshot.get("pb"),
    }
    sector = snapshot.get("sector")

    # ---- Absolute-band score (v1 logic, kept) ----
    weighted_sum = 0.0
    weight_present = 0.0
    for name, value in metric_values.items():
        cheap, expensive, weight = _BANDS[name]
        sub = _score_metric(value, cheap, expensive)
        if sub is None:
            continue
        weighted_sum += sub * weight
        weight_present += weight

    if weight_present == 0:
        return ValuationSignals(
            pe=metric_values["pe"], forward_pe=metric_values["forward_pe"],
            ps=metric_values["ps"], pb=metric_values["pb"], peg=metric_values["peg"],
            score=None, bucket="no_data", coverage=0.0,
            absolute_score=None, peer_score=None,
            peer_type="absolute", peer_group_size=0, sector=sector,
        )

    abs_score = weighted_sum / weight_present
    coverage = weight_present / sum(w for _, _, w in _BANDS.values())

    # ---- Peer-relative score ----
    peer_score, peer_type, peer_n = _peer_score(metric_values, peer_context, sector)

    if peer_score is not None:
        final_score = 0.5 * abs_score + 0.5 * peer_score
    else:
        final_score = abs_score

    return ValuationSignals(
        pe=metric_values["pe"], forward_pe=metric_values["forward_pe"],
        ps=metric_values["ps"], pb=metric_values["pb"], peg=metric_values["peg"],
        score=final_score, bucket=_bucket(final_score), coverage=coverage,
        absolute_score=abs_score, peer_score=peer_score,
        peer_type=peer_type, peer_group_size=peer_n, sector=sector,
    )


def valuation_explanation(v: ValuationSignals) -> str:
    """Short string like 'Fwd P/E 22 · P/S 5.4 · Val attractive (vs sector)'."""
    bits = []
    if v.forward_pe is not None:
        bits.append(f"FwdPE {v.forward_pe:.1f}")
    elif v.pe is not None:
        bits.append(f"PE {v.pe:.1f}")
    if v.peg is not None:
        bits.append(f"PEG {v.peg:.1f}")
    if v.ps is not None:
        bits.append(f"PS {v.ps:.1f}")
    if not bits:
        return "No fundamentals"
    if v.bucket != "no_data":
        method = (
            " (vs sector)" if v.peer_type == "sector"
            else " (vs watchlist)" if v.peer_type == "watchlist"
            else " (vs bands)"
        )
        bits.append(f"Val {v.bucket}{method}")
    return " · ".join(bits)
