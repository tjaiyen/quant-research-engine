"""Daily monitor — sync, stop-loss scan, signal rescore, snapshot.

L3: HMM staleness check via ``screener.regime.hmm_trainer.should_retrain``.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Iterable

from auto_trader.config import LOG_DIR, LOG_LEVEL
from auto_trader.utils import setup_logging

logger = logging.getLogger(__name__)


def run_daily_monitor() -> dict:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    setup_logging(f"{LOG_DIR}daily_{today}.log", LOG_LEVEL)

    from auto_trader.broker.alpaca_client import get_account_info
    from auto_trader.broker.portfolio_state import sync_portfolio_state
    from auto_trader.monitor.alert_engine import send_alert
    from auto_trader.monitor.position_tracker import update_all_position_prices
    from auto_trader.monitor.signal_refresher import refresh_signals
    from auto_trader.risk.drawdown_circuit import is_halted as drawdown_halted
    from auto_trader.risk.risk_report import generate_risk_snapshot
    from auto_trader.risk.stop_loss_monitor import scan_stop_losses
    from auto_trader.state.portfolio_db import (
        compute_realized_pnl_ytd,
        get_all_positions,
        get_peak_portfolio_value,
        initialize_db,
        log_portfolio_snapshot,
        log_system_event,
    )

    initialize_db()

    # Refresh the HELD positions' prices (+SPY) before marking so the daily
    # mark-to-market reflects the latest close — else P&L only moves when the
    # weekly universe seed runs. Lean (~held + SPY, not the 220 universe);
    # best-effort so an offline/yfinance hiccup never blocks the monitor.
    try:
        from tasks.refresh_prices import main as refresh_prices
        held = sorted({p["ticker"] for p in get_all_positions()})
        if held:
            refresh_prices([*held, "SPY"])   # symbols are positional args
            logger.info("daily: refreshed prices for %d held tickers + SPY", len(held))
    except Exception as exc:
        logger.warning("daily price refresh skipped (%s)", exc)

    state = sync_portfolio_state()
    account = get_account_info()

    prices = {p["ticker"]: p["current_price"] for p in state["positions"]}
    update_all_position_prices(prices)

    if drawdown_halted(account["portfolio_value"]):
        send_alert(
            "DRAWDOWN CIRCUIT ACTIVE",
            f"Portfolio ${account['portfolio_value']:,.2f}. Buys halted.",
            "DRAWDOWN_HALT",
        )

    stop_hits = scan_stop_losses(prices)
    for hit in stop_hits:
        send_alert(
            f"STOP LOSS: {hit['ticker']}",
            f"${hit['current_price']:.2f} hit stop ${hit['stop_price']:.2f}. "
            f"Loss: {hit['loss_pct']:.1%}.",
            "STOP_LOSS",
        )

    # L3: HMM staleness check
    try:
        from screener.regime.hmm_trainer import should_retrain

        if should_retrain():
            logger.warning("HMM model is stale — retrain recommended before next cycle")
            send_alert(
                "HMM Model Stale",
                "Run scripts/pre_run_screener.py to retrain. "
                "Stale model may produce inaccurate regime signals.",
                "OTHER",
                force=True,
            )
    except Exception as exc:
        logger.debug("HMM staleness check skipped (%s)", exc)

    # Pull current regime for the rescore step
    try:
        from screener.regime.hmm_predictor import get_regime

        regime_data = get_regime()
    except FileNotFoundError:
        logger.warning("HMM model not found — run pre_run_screener.py first")
        regime_data = {
            "regime": "unknown", "confidence": 0.0,
            "blended_weights": {}, "stable": False, "probabilities": {},
        }

    decay_alerts = refresh_signals(regime_data)
    for alert in decay_alerts:
        send_alert(
            f"SIGNAL {alert['type']}: {alert['ticker']}",
            f"Score: {alert['score']:.3f} | Δ={alert.get('delta', 0):.3f}",
            "SIGNAL_EXIT" if alert["type"] == "EXIT_SIGNAL" else "SCORE_DECAY",
        )

    risk = generate_risk_snapshot(account["portfolio_value"], account["cash"])

    spy_price = None
    try:
        import yfinance as yf

        spy_price = float(yf.Ticker("SPY").fast_info["last_price"])
    except Exception:
        pass
    if not spy_price:
        # Fall back to the latest cached SPY close so the benchmark line + the
        # scorecard's paper-vs-SPY don't silently go None when the live fetch fails.
        try:
            from utils.db import fetch_prices

            df = fetch_prices("SPY")
            if df is not None and not df.empty and "adj_close" in df.columns:
                spy_price = float(df["adj_close"].iloc[-1])
        except Exception:
            pass

    # Peak must include today's value, else a cold snapshots table (peak=0)
    # yields an absurd drawdown like (0-10000)/1 = -10000 (=-1,000,000%).
    peak = max(get_peak_portfolio_value(), account["portfolio_value"])
    drawdown = (peak - account["portfolio_value"]) / peak if peak > 0 else 0.0

    snapshot = {
        "total_value": account["portfolio_value"],
        "cash": account["cash"],
        "invested_value": account["portfolio_value"] - account["cash"],
        "unrealized_pnl": sum(p.get("unrealized_pnl", 0) for p in state["positions"]),
        "realized_pnl_ytd": compute_realized_pnl_ytd(),
        "n_positions": state["n_positions"],
        "regime": regime_data.get("regime"),
        "benchmark_value": spy_price,
        "drawdown_from_peak": drawdown,
    }
    log_portfolio_snapshot(snapshot)

    log_system_event(
        "DAILY_MONITOR",
        "Complete",
        {
            "stop_hits": len(stop_hits),
            "decay_alerts": len(decay_alerts),
            "circuit_active": risk["circuit_breaker"],
        },
    )
    logger.info(
        "Daily monitor complete | Portfolio: $%.2f | Stops: %d | Decay: %d",
        account["portfolio_value"], len(stop_hits), len(decay_alerts),
    )
    return {
        "portfolio": snapshot,
        "stop_hits": stop_hits,
        "decay_alerts": decay_alerts,
        "regime": regime_data,
        "risk": risk,
    }


def main(argv: Iterable[str] | None = None) -> int:
    try:
        run_daily_monitor()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.exception("Daily monitor failed: %s", exc)
        try:
            from auto_trader.monitor.alert_engine import send_alert

            send_alert("DAILY MONITOR FAILED", str(exc), force=True)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
