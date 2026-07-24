"""Regression tests for the Tradier partial-profit-lock master switch."""

import logging
import logging.handlers


class _NoFileRotatingHandler(logging.NullHandler):
    """Accept RotatingFileHandler args without writing outside the test sandbox."""

    def __init__(self, *args, **kwargs):
        super().__init__()


# tradier_manage's import graph initializes file loggers. Replace only the file
# handler class during collection so this unit test remains read-only.
logging.handlers.RotatingFileHandler = _NoFileRotatingHandler

import tradier_manage as tm


def test_global_ppl_off_overrides_true_hourly_overlay(monkeypatch):
    """A stale active_config=True must not bypass the global emergency switch."""
    monkeypatch.setattr(tm.config, "PARTIAL_PROFIT_LOCK_ENABLED", False)
    monkeypatch.setattr(
        tm,
        "_load_tradier_per_sym_cfgs",
        lambda _path: {"AAPL_LONG": {"PARTIAL_PROFIT_LOCK_ENABLED": True}},
    )
    monkeypatch.setattr(tm, "_load_global_per_sym_cfgs", lambda: {})

    assert tm._cfg(
        "PARTIAL_PROFIT_LOCK_ENABLED", False, "trb", "AAPL", "LONG"
    ) is False


def test_global_ppl_on_still_allows_overlay_to_disable_symbol(monkeypatch):
    """The master switch does not remove the existing per-symbol opt-out."""
    monkeypatch.setattr(tm.config, "PARTIAL_PROFIT_LOCK_ENABLED", True)
    monkeypatch.setattr(
        tm,
        "_load_tradier_per_sym_cfgs",
        lambda _path: {"AAPL_LONG": {"PARTIAL_PROFIT_LOCK_ENABLED": False}},
    )
    monkeypatch.setattr(tm, "_load_global_per_sym_cfgs", lambda: {})

    assert tm._cfg(
        "PARTIAL_PROFIT_LOCK_ENABLED", True, "trb", "AAPL", "LONG"
    ) is False


def test_global_ppl_on_allows_true_overlay(monkeypatch):
    """PPL can still be deliberately enabled after the global switch is on."""
    monkeypatch.setattr(tm.config, "PARTIAL_PROFIT_LOCK_ENABLED", True)
    monkeypatch.setattr(
        tm,
        "_load_tradier_per_sym_cfgs",
        lambda _path: {"AAPL_LONG": {"PARTIAL_PROFIT_LOCK_ENABLED": True}},
    )
    monkeypatch.setattr(tm, "_load_global_per_sym_cfgs", lambda: {})

    assert tm._cfg(
        "PARTIAL_PROFIT_LOCK_ENABLED", False, "trb", "AAPL", "LONG"
    ) is True
