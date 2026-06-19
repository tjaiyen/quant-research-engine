"""Regime-adjusted veto gate.

Applied BEFORE composite-score computation. Vetoed stocks bypass the
weighted score entirely and are excluded from per-sector ranking. The
thresholds tighten in bear regime, loosen in bull regime — the actual
values live in ``screener.config.VETO_THRESHOLDS``.
"""
from __future__ import annotations

from screener.config import VETO_THRESHOLDS


def apply_veto(garch_vol: float, mc_loss_prob: float, regime: str) -> dict:
    """Decide whether a stock survives the regime-adjusted veto.

    Args:
        garch_vol: annualized vol from the GARCH signal's metadata.
        mc_loss_prob: tail-loss probability from the Monte Carlo signal.
        regime: ``"bull" | "sideways" | "bear"`` — picks which threshold row.

    Returns:
        A dict with keys ``passed`` (bool), ``veto_reason`` (str|None),
        ``garch_vol``, ``mc_loss_prob``, and ``thresholds_applied``.
    """
    thresholds = VETO_THRESHOLDS[regime]
    vol_veto = garch_vol > thresholds["garch_vol"]
    tail_veto = mc_loss_prob > thresholds["mc_loss_prob"]

    if vol_veto and tail_veto:
        reason = "VETO_BOTH"
    elif vol_veto:
        reason = "VETO_VOL"
    elif tail_veto:
        reason = "VETO_TAIL"
    else:
        reason = None

    return {
        "passed": reason is None,
        "veto_reason": reason,
        "garch_vol": float(garch_vol),
        "mc_loss_prob": float(mc_loss_prob),
        "thresholds_applied": dict(thresholds),
    }


__all__ = ["apply_veto"]
