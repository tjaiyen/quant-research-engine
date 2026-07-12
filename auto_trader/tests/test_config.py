"""Phase K — config invariants (Gate 1)."""
from __future__ import annotations

import importlib

import pytest


def test_gate1_config_self_validates():
    """Gate 1: config.py runs ``_validate_config()`` at import time without error."""
    # Importing the module is the test — _validate_config runs on import.
    cfg = importlib.import_module("auto_trader.config")
    # And expose the assertions used in spec Gate 1 explicitly:
    assert cfg.SIGNAL_EXIT_THRESHOLD < cfg.MIN_COMPOSITE_TO_BUY
    assert 0 < cfg.MAX_MONTHLY_DEPLOYMENT_PCT <= 1.0
    assert cfg.SELL_TIME_IN_FORCE == "day"
    assert cfg.BUY_TIME_IN_FORCE == "opg"
    assert cfg.POSITION_SIZING_MODE in ("score_weight", "equal", "score_vol")
    assert cfg.SCREENER_CACHE_MAX_AGE_HOURS == 10  # C7


def test_expected_signal_keys_match_screener():
    """Internal: the trader's expected signal keys must match screener's."""
    from auto_trader.config import EXPECTED_SIGNAL_KEYS
    from screener.config import EXPECTED_SIGNAL_KEYS as SCREENER_KEYS

    assert EXPECTED_SIGNAL_KEYS == SCREENER_KEYS


def test_gate2_credentials_no_circular_imports(monkeypatch):
    """Gate 2: importing credentials must NOT pull in auto_trader.config."""
    import sys

    # Drop cached imports so we can observe a fresh load — but RESTORE the
    # originals afterwards: leaving fresh module instances in sys.modules
    # breaks any later test that monkeypatches a module object it imported
    # at collection time (the tests/test_fleet.py order-dependency, 2026-07-11).
    saved = {k: v for k, v in sys.modules.items() if k.startswith("auto_trader")}
    for k in saved:
        sys.modules.pop(k, None)
    try:
        monkeypatch.setenv("TRADING_MODE", "paper")
        import auto_trader.credentials as creds  # noqa: F401

        assert "auto_trader.config" not in sys.modules, (
            "H2 violation: credentials imported config (circular dep risk)"
        )
        assert creds.get_trading_mode() == "paper"
    finally:
        for k in list(sys.modules):
            if k.startswith("auto_trader"):
                sys.modules.pop(k, None)
        sys.modules.update(saved)
        # Re-bind parent-package ATTRIBUTES too: pytest's string monkeypatch
        # (`setattr("auto_trader.credentials.X", …)`) resolves via getattr on
        # the package, not sys.modules — a stale attribute left pointing at
        # the fresh module made later halt-flag tests patch a dead object
        # and WRITE THE REAL auto_trader/.halt (2026-07-12).
        for k, m in saved.items():
            parent, _, child = k.rpartition(".")
            if parent and parent in sys.modules:
                setattr(sys.modules[parent], child, m)


def test_paper_start_path_resolves():
    """PAPER_START_PATH should be a Path; HALT_FLAG_PATH likewise."""
    from auto_trader.credentials import HALT_FLAG_PATH, PAPER_START_PATH

    assert PAPER_START_PATH.name == ".paper_start_date"
    assert HALT_FLAG_PATH.name == ".halt"


def test_halt_flag_round_trip():
    from auto_trader.credentials import (
        clear_halt,
        is_halted,
        set_halt,
    )

    clear_halt()
    assert not is_halted()
    set_halt("test")
    assert is_halted()
    clear_halt()
    assert not is_halted()
