"""`track audit` must log a RECON_* system event (the dashboard banner reads
the latest one — a clean manual audit has to clear a stale RECON_DRIFT)."""
from __future__ import annotations

import argparse


def test_cmd_audit_logs_recon_event():
    from auto_trader.state.portfolio_db import get_system_events, initialize_db
    from cli.track import cmd_audit

    initialize_db()
    rc = cmd_audit(argparse.Namespace())
    events = [e for e in get_system_events(limit=10)
              if e["event_type"].startswith("RECON_")]
    assert events, "audit logged no RECON event"
    latest = events[0]
    assert latest["event_type"] == ("RECON_OK" if rc == 0 else "RECON_DRIFT")
    details = latest.get("details") or {}
    if isinstance(details, str):
        import json
        details = json.loads(details)
    assert details.get("source") == "track audit"
