#!/usr/bin/env python3
"""USER 2026-10-20: "make sure v15_fleet_scheduler always updates any npz it dispatches to the latest 15m
bar - if it can't skip temporarily and dispatch next one that can - no more backtests can run on old npz's".

The hole these tests close, measured from S1's /tmp/v15_dispatch.log that day: 612 dispatches logged
`npz-degraded ... DEGRADED-STALE-TAIL(allowed by V15_SCHED_RUN_STALE=1)` and launched anyway, the worst
nameable one SAP_LONG at 14:21:11Z on a source NPZ 74 bars / 18.5 h behind a 0.85 h limit — while the fleet
scheduler's own cron had V15_SCHED_RUN_STALE=0.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import v15_fleet_scheduler as sched  # noqa: E402


def test_run_stale_needs_both_keys(monkeypatch):
    monkeypatch.delenv("V15_SCHED_RUN_STALE", raising=False)
    monkeypatch.delenv("V15_SCHED_RUN_STALE_ACK", raising=False)
    assert sched._run_stale_allowed() is False
    monkeypatch.setenv("V15_SCHED_RUN_STALE", "1")
    assert sched._run_stale_allowed() is False, "RUN_STALE=1 alone must no longer defeat the freshness law"
    monkeypatch.setenv("V15_SCHED_RUN_STALE_ACK", "wrong")
    assert sched._run_stale_allowed() is False
    monkeypatch.setenv("V15_SCHED_RUN_STALE_ACK", "i-accept-old-npz")
    assert sched._run_stale_allowed() is True


def test_run_stale_ack_alone_is_not_enough(monkeypatch):
    monkeypatch.delenv("V15_SCHED_RUN_STALE", raising=False)
    monkeypatch.setenv("V15_SCHED_RUN_STALE_ACK", "i-accept-old-npz")
    assert sched._run_stale_allowed() is False


class _Cutoffs:
    BAR = 900

    @staticmethod
    def is_crypto_symbol(symbol):
        return symbol.endswith(("USDT", "USDC"))

    @staticmethod
    def last_closed_bar(now=None):
        return 1792506600

    @staticmethod
    def last_rth_close_bar(now=None):
        return 1792506600


def _patch_source(monkeypatch, last_ts):
    monkeypatch.setitem(sys.modules, "npz_cutoffs", _Cutoffs)
    monkeypatch.setitem(sys.modules, "npz_bar_refresh", type("M", (), {"npz_last_ts": staticmethod(lambda d, s: last_ts)}))


def test_a_source_within_the_jit_reach_proceeds(monkeypatch):
    _patch_source(monkeypatch, 1792506600 - 2 * 900)
    monkeypatch.delenv("V15_SCHED_SRC_MAX_BARS_BEHIND", raising=False)
    ok, why = sched._s1_source_tail_ok("BTCUSDC")
    assert ok is True and "2 bars behind" in why


def test_the_sap_case_is_skipped(monkeypatch):
    """74 bars / 18.5 h behind: a tail append cannot close that, so the slot must go to another symbol."""
    _patch_source(monkeypatch, 1792506600 - 74 * 900)
    monkeypatch.delenv("V15_SCHED_SRC_MAX_BARS_BEHIND", raising=False)
    ok, why = sched._s1_source_tail_ok("SAP")
    assert ok is False and "74 bars" in why and "18.5h" in why


def test_the_bar_limit_is_tunable(monkeypatch):
    _patch_source(monkeypatch, 1792506600 - 20 * 900)
    monkeypatch.setenv("V15_SCHED_SRC_MAX_BARS_BEHIND", "8")
    assert sched._s1_source_tail_ok("SAP")[0] is False
    monkeypatch.setenv("V15_SCHED_SRC_MAX_BARS_BEHIND", "24")
    assert sched._s1_source_tail_ok("SAP")[0] is True


def test_source_check_fails_open_on_any_error(monkeypatch):
    """The dispatch job verifies the bytes again before launching, so an unreadable probe must not idle the
    fleet — but it must say so."""
    _patch_source(monkeypatch, None)
    assert sched._s1_source_tail_ok("SAP") == (True, "no-npz-ts")
    monkeypatch.setitem(sys.modules, "npz_bar_refresh", type("M", (), {"npz_last_ts": staticmethod(lambda d, s: (_ for _ in ()).throw(RuntimeError("boom")))}))
    ok, why = sched._s1_source_tail_ok("SAP")
    assert ok is True and "unavailable" in why


def test_a_missing_cutoff_fails_open(monkeypatch):
    class _NoCut(_Cutoffs):
        @staticmethod
        def last_rth_close_bar(now=None):
            return 0

    monkeypatch.setitem(sys.modules, "npz_cutoffs", _NoCut)
    monkeypatch.setitem(sys.modules, "npz_bar_refresh", type("M", (), {"npz_last_ts": staticmethod(lambda d, s: 1792506600)}))
    assert sched._s1_source_tail_ok("SAP") == (True, "no-cutoff")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
