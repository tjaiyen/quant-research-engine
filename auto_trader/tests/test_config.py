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

    # Drop cached imports so we can observe a fresh load.
    for k in list(sys.modules):
        if k.startswith("auto_trader."):
            sys.modules.pop(k, None)

    monkeypatch.setenv("TRADING_MODE", "paper")
    import auto_trader.credentials as creds  # noqa: F401

    assert "auto_trader.config" not in sys.modules, (
        "H2 violation: credentials imported config (circular dep risk)"
    )
    assert creds.get_trading_mode() == "paper"


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
