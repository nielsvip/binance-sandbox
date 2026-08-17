"""Regression tests for the Tradier partial-profit-lock master switch."""

import json
import logging
import logging.handlers
import os


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


def test_guarded_backtest_override_beats_per_symbol_overlay(
    monkeypatch, tmp_path
):
    override = tmp_path / "cell.json"
    override.write_text(
        json.dumps({"WT_3M_FORCE_OPEN_ENABLED": False})
    )
    monkeypatch.setenv("V8_SWEEP_MODE", "1")
    monkeypatch.setenv("V8_BACKTEST_OVERRIDE_PRECEDENCE", "1")
    monkeypatch.setenv("V8_OVERRIDE_FILE", str(override))
    monkeypatch.setattr(tm, "_v8_sweep_override_cache", {})
    monkeypatch.setattr(tm, "_v8_sweep_override_cache_path", "")
    monkeypatch.setattr(
        tm,
        "_load_tradier_per_sym_cfgs",
        lambda _path: {"MU_LONG": {"WT_3M_FORCE_OPEN_ENABLED": True}},
    )
    monkeypatch.setattr(tm, "_load_global_per_sym_cfgs", lambda: {})

    assert tm._cfg(
        "WT_3M_FORCE_OPEN_ENABLED", True, "trb", "MU", "LONG"
    ) is False


def test_empty_guarded_override_preserves_accepted_baseline_overlay(
    monkeypatch, tmp_path
):
    override = tmp_path / "baseline.json"
    override.write_text("{}")
    monkeypatch.setenv("V8_SWEEP_MODE", "1")
    monkeypatch.setenv("V8_BACKTEST_OVERRIDE_PRECEDENCE", "1")
    monkeypatch.setenv("V8_OVERRIDE_FILE", str(override))
    monkeypatch.setattr(tm, "_v8_sweep_override_cache", {})
    monkeypatch.setattr(tm, "_v8_sweep_override_cache_path", "")
    monkeypatch.setattr(
        tm,
        "_load_tradier_per_sym_cfgs",
        lambda _path: {"MU_LONG": {"WT_3M_FORCE_OPEN_ENABLED": True}},
    )
    monkeypatch.setattr(tm, "_load_global_per_sym_cfgs", lambda: {})

    assert tm._cfg(
        "WT_3M_FORCE_OPEN_ENABLED", False, "trb", "MU", "LONG"
    ) is True


def test_per_sym_overlay_cache_is_scoped_to_account_path(monkeypatch, tmp_path):
    """TRB and TRC files must not share an mtime-only cache entry."""
    trb_path = tmp_path / "trb.json"
    trc_path = tmp_path / "trc.json"
    payload = {"wsharpe": 1.0, "overrides": {"LONG_ENABLED": True}}
    trb_path.write_text(json.dumps({"AAA_LONG": payload}))
    trc_path.write_text(json.dumps({"AAA_LONG": {"wsharpe": -1.0, "overrides": {"LONG_ENABLED": False}}}))
    same_mtime = 1_700_000_000
    os.utime(trb_path, (same_mtime, same_mtime))
    os.utime(trc_path, (same_mtime, same_mtime))

    monkeypatch.setattr(tm, "_tradier_per_sym_cfgs_cache", {})
    monkeypatch.setattr(tm, "_tradier_per_sym_raw_cache", {})
    monkeypatch.setattr(tm, "_global_per_sym_cfgs_path", tmp_path / "missing-baseline.json")

    trb = tm._load_tradier_per_sym_cfgs(trb_path)
    trc = tm._load_tradier_per_sym_cfgs(trc_path)
    assert trb["AAA_LONG"]["LONG_ENABLED"] is True
    assert trc["AAA_LONG"]["LONG_ENABLED"] is False
