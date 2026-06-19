"""Sync the broker's source-of-truth positions back into the auto_trader DB.

C5: split detection uses ``current_price`` (not cost_basis) to compute the
new stop loss after a split is observed.

H5: positions present in the broker but absent from the local DB are
auto-created with ``status='ANOMALY'`` so they're tracked + alerted on.
"""
from __future__ import annotations

import logging

from auto_trader.broker.alpaca_client import (
    get_account_info,
    get_current_positions,
)
from auto_trader.config import STOP_LOSS_PCT
from auto_trader.state.portfolio_db import (
    close_position,
    get_all_positions,
    log_system_event,
    upsert_position,
)
from auto_trader.utils import now_iso, today_iso

logger = logging.getLogger(__name__)


def sync_portfolio_state() -> dict:
    """Full broker → DB reconciliation.

    Handles:
      * positions closed externally (DB has them, broker doesn't) → CLOSED
      * positions opened externally (broker has them, DB doesn't) → ANOMALY
      * suspected splits (share-count delta > 10%) → re-stop-loss off
        ``current_price`` (C5)

    Returns a summary dict with cash + portfolio_value + raw position list.
    """
    account = get_account_info()
    live_pos = get_current_positions()
    live_tickers = {p["ticker"] for p in live_pos}
    db_positions = get_all_positions()
    db_tickers = {p["ticker"] for p in db_positions}
    db_map = {p["ticker"]: p for p in db_positions}

    # Closed externally
    for ticker in db_tickers - live_tickers:
        logger.warning("%s: in DB but not broker — marking CLOSED", ticker)
        close_position(ticker)
        log_system_event("EXTERNAL_CLOSE", f"{ticker} closed externally", {})

    for pos in live_pos:
        ticker = pos["ticker"]
        db_rec = db_map.get(ticker)

        if db_rec:
            db_shares = float(db_rec["shares"])
            live_shares = float(pos["shares"])
            share_delta = abs(live_shares - db_shares) / max(db_shares, 1e-6)

            # C5: split detection — new stop from current_price
            updated_stop = float(db_rec.get("stop_loss_price") or 0.0)
            if share_delta > 0.10 and db_shares > 0:
                new_stop = float(pos["current_price"]) * (1 - STOP_LOSS_PCT)
                logger.warning(
                    "%s: share delta=%.1f%% > 10%% — possible split. "
                    "New stop=$%.2f (from current_price=$%.2f)",
                    ticker, share_delta * 100, new_stop, pos["current_price"],
                )
                log_system_event(
                    "SPLIT_DETECTED",
                    f"{ticker} split detected",
                    {
                        "old_shares": db_shares,
                        "new_shares": live_shares,
                        "new_stop": new_stop,
                        "current_price": pos["current_price"],
                    },
                )
                updated_stop = new_stop

            upsert_position(
                {
                    **db_rec,
                    "shares": live_shares,
                    "cost_basis": float(pos["cost_basis"]),
                    "total_cost": live_shares * float(pos["cost_basis"]),
                    "current_price": float(pos["current_price"]),
                    "stop_loss_price": updated_stop,
                }
            )

        else:
            # H5: auto-create as ANOMALY
            logger.warning(
                "%s: in broker but not DB — auto-creating ANOMALY record",
                ticker,
            )
            cost = float(pos["cost_basis"])
            upsert_position(
                {
                    "ticker": ticker,
                    "shares": float(pos["shares"]),
                    "cost_basis": cost,
                    "total_cost": float(pos["shares"]) * cost,
                    "current_price": float(pos["current_price"]),
                    "sector": "UNKNOWN",
                    "entry_date": today_iso(),
                    "entry_score": 0.0,
                    "last_score": 0.0,
                    "last_scored_at": now_iso(),
                    "stop_loss_price": cost * (1 - STOP_LOSS_PCT),
                    "target_allocation": 0.0,
                    "status": "ANOMALY",
                    "regime_at_entry": "unknown",
                }
            )
            log_system_event(
                "POSITION_ANOMALY",
                f"{ticker} auto-created as ANOMALY",
                pos,
            )
            try:
                from auto_trader.monitor.alert_engine import send_alert

                send_alert(
                    f"ANOMALY: {ticker} auto-created",
                    f"Position in broker but not DB. "
                    f"Shares={pos['shares']:.4f} @ ${pos['cost_basis']:.2f}. "
                    "Please verify and set the correct sector manually.",
                    force=True,
                )
            except Exception as exc:  # pragma: no cover - alerts optional
                logger.debug("Alert send failed (non-fatal): %s", exc)

    return {
        "cash": account["cash"],
        "portfolio_value": account["portfolio_value"],
        "n_positions": len(live_pos),
        "positions": live_pos,
        "trading_blocked": account["trading_blocked"],
    }


__all__ = ["sync_portfolio_state"]
