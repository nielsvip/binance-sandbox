"""2026-10-13 LIVE-MONEY: crypto maker-first exits + taker backstop.

Locks in the four additive ez_manage.py fixes (no Config fields — the
test_no_config_or_quickconfig_fields contract stands; tunables ride
getattr-with-default like FIXED_STOP_VERIFY_TTL_SEC):
1. unreachable-class preflight refusals fail OPEN for FIXED_STOP exits only.
2. FIXED_STOP bypasses NEWBORN_PROTECT (hard stops are emergency closes).
3. stale dc channels never freeze absurd stop levels (entry fallback +
   exactly-one re-freeze, never a drift).
4. FIXED_STOP maker chase is short; webhook failure falls to the
   _fixed_stop_taker_backstop leg (maker-first order preserved).
"""
import asyncio

import pytest

import config as C
from ez_manage import (
    _fixed_stop_freeze_level,
    _fixed_stop_level_absurd,
    _fixed_stop_preflight_failopen,
    _fixed_stop_reason,
)


def test_reason_token():
    assert _fixed_stop_reason("FIXED_STOP_LONG_px1.0_lvl1.0_g-2.0") is True
    assert _fixed_stop_reason("fixED_stop_backstop_place") is True
    assert _fixed_stop_reason("WT_DC_EXIT") is False
    assert _fixed_stop_reason("") is False
    assert _fixed_stop_reason(None) is False


def test_preflight_failopen_unreachable_only():
    for code in ("BROKER_UNREACHABLE_OPEN_ORDERS", "BROKER_UNREACHABLE_ORDERS", "PREFLIGHT_TIMEOUT_OR_ERROR"):
        assert _fixed_stop_preflight_failopen("FIXED_STOP_LONG_px1_lvl1_g-2", code) is True, code
    for code in ("OPEN_ORDER_EXISTS", "LAST_ORDER_NOT_FINAL", "WIRE_UNCONFIRMED", "SOME_NEW_CODE"):
        assert _fixed_stop_preflight_failopen("FIXED_STOP_LONG_px1_lvl1_g-2", code) is False, code
    assert _fixed_stop_preflight_failopen("WT_DC_EXIT", "BROKER_UNREACHABLE_OPEN_ORDERS") is False
    assert _fixed_stop_preflight_failopen("VEC_DRIVEN_OPEN", "PREFLIGHT_TIMEOUT_OR_ERROR") is False


def test_freeze_fresh_channel():
    lvl, src = _fixed_stop_freeze_level(True, 100.0, 0.0, 100.5)
    assert src == "CHANNEL" and lvl == 99.75
    lvl, src = _fixed_stop_freeze_level(False, 0.0, 100.0, 99.5)
    assert src == "CHANNEL" and lvl == 100.25


def test_freeze_always_uses_channel_no_pct_rule():
    """USER 2026-10-13: "NO 5% OR ANY% EXISTS" — the stop is the dc_15m channel at entry, whatever its distance."""
    lvl, src = _fixed_stop_freeze_level(True, 50.0, 0.0, 100.0)
    assert src == "CHANNEL" and abs(lvl - 50.0 * 0.9975) < 1e-9
    lvl, src = _fixed_stop_freeze_level(False, 0.0, 200.0, 100.0)
    assert src == "CHANNEL" and abs(lvl - 200.0 * 1.0025) < 1e-9
    lvl, src = _fixed_stop_freeze_level(True, 0.0, 0.0, 100.0)
    assert src == "ENTRY_FALLBACK" and lvl == 99.75  # no channel at all -> entry


def test_freeze_nothing_to_anchor():
    assert _fixed_stop_freeze_level(True, 0.0, 0.0, 0.0) == (0.0, "ZERO")


def test_absurd_detector():
    assert _fixed_stop_level_absurd(True, 50.0, 100.0) is False  # distance never makes a level absurd (no % rule)
    assert _fixed_stop_level_absurd(False, 200.0, 100.0) is False
    assert _fixed_stop_level_absurd(True, 99.75, 100.0) is False
    assert _fixed_stop_level_absurd(False, 100.25, 100.0) is False
    assert _fixed_stop_level_absurd(True, 100.5, 100.0) is True
    assert _fixed_stop_level_absurd(False, 99.5, 100.0) is True
    assert _fixed_stop_level_absurd(True, 0.0, 100.0) is False
    assert _fixed_stop_level_absurd(True, 99.75, 0.0) is False


def test_no_fixed_stop_config_fields_still_holds():
    import dataclasses as dc
    names = [f.name for f in dc.fields(C.Config())]
    assert not [x for x in names if "FIXED_STOP" in x], names


def test_newborn_block_bypasses_fixed_stop_source():
    import pathlib
    src = pathlib.Path("ez_manage.py").read_text()
    i = src.find("_nb_emergency = (")
    assert i > 0
    block = src[i:i + 1200]
    assert '"FIXED_STOP" in _nb_reason_up' in block


def test_taker_backstop_leg_exists_and_guarded():
    import inspect
    import ez_manage as E
    fn = E.MultiAccountTradeManager._fixed_stop_taker_backstop
    src = inspect.getsource(fn)
    assert "ez_broker_moved" in src
    assert "reduceOnly" in src
    assert "_sanction_execution" in src
    assert "PYTEST_CURRENT_TEST" in src


def test_taker_backstop_skips_under_pytest():
    import os
    import ez_manage as E
    os.environ["PYTEST_CURRENT_TEST"] = "1"
    try:
        mgr = E.MultiAccountTradeManager.__new__(E.MultiAccountTradeManager)
        out = asyncio.run(mgr._fixed_stop_taker_backstop(position_key="t:SYM_LONG", account_key="t", symbol="SYM", position_side="LONG", side="SELL", quantity=1.0, current_price=10.0, baseline_amt=1.0, is_long=True, unique_id="u", reason="FIXED_STOP_LONG", action="CLOSE"))
        assert out == "SKIPPED_TAKER_BACKSTOP_SANDBOX", out
    finally:
        del os.environ["PYTEST_CURRENT_TEST"]
