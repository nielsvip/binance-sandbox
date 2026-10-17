"""STOP_RECLAIM_REENTRY predicate (vec_decisions/stop_reclaim.py) — USER 2026-10-16/17 design doc 14 §4.1."""
import types
from pathlib import Path

import vec_decisions.stop_reclaim as SR

ROOT = Path(__file__).resolve().parents[1]


def _cfg(**kw):
    d = dict(STOP_RECLAIM_REENTRY_ENABLED=True, STOP_RECLAIM_WINDOW_BARS=4, STOP_RECLAIM_MAX_SWEEP_PCT=1.5, STOP_RECLAIM_BUFFER_PCT=0.10,
             STOP_RECLAIM_REQUIRE_HTF=True, STOP_RECLAIM_SIZE_MULT=1.0, STOP_RECLAIM_EXCLUSIVE=True)
    d.update(kw)
    return types.SimpleNamespace(**d)


GOOD_L = dict(stop_level=100.0, bars_since=2, ext_since=99.0, close=100.5, high=100.6, low=99.5, wt1=-20.0, wt2=-25.0, wt_vel=2.0,
              dc4h_low=98.0, dc4h_high=110.0, wt1_4h=10.0, wt2_4h=5.0, ema50_4h=0.0)


def _fire(cfg, is_long=True, **over):
    a = dict(GOOD_L)
    a.update(over)
    return SR.fire(cfg, is_long, a["stop_level"], a["bars_since"], a["ext_since"], a["close"], a["high"], a["low"], a["wt1"], a["wt2"],
                   a["wt_vel"], a["dc4h_low"], a["dc4h_high"], a["wt1_4h"], a["wt2_4h"], a["ema50_4h"])


def test_all_six_conditions_fire_long():
    assert _fire(_cfg())


def test_each_condition_vetoes():
    c = _cfg()
    assert not _fire(_cfg(STOP_RECLAIM_REENTRY_ENABLED=False))
    assert not _fire(c, bars_since=0) and not _fire(c, bars_since=5)          # window
    assert not _fire(c, ext_since=98.0)                                        # sweep deeper than 1.5 %
    assert not _fire(c, close=100.05)                                          # reclaim buffer 0.10 %
    assert not _fire(c, close=100.5, high=102.0, low=99.5)                     # close in the lower half of the bar (0.4)
    assert not _fire(c, wt1=-30.0) and not _fire(c, wt_vel=0.0)                # momentum strictly turned
    assert not _fire(c, dc4h_low=101.0)                                        # 4h structure broken
    assert not _fire(c, wt1_4h=1.0, wt2_4h=5.0, ema50_4h=0.0)                  # 4h trend against
    assert _fire(c, wt1_4h=1.0, wt2_4h=5.0, ema50_4h=100.0)                    # ...unless close >= ema50_4h
    assert _fire(_cfg(STOP_RECLAIM_REQUIRE_HTF=False), dc4h_low=101.0, wt1_4h=1.0, wt2_4h=5.0)


def test_short_is_mirrored():
    s = dict(stop_level=100.0, bars_since=1, ext_since=101.0, close=99.5, high=100.5, low=99.4, wt1=20.0, wt2=25.0, wt_vel=-2.0,
             dc4h_low=90.0, dc4h_high=102.0, wt1_4h=-10.0, wt2_4h=-5.0, ema50_4h=0.0)
    assert SR.fire(_cfg(), False, *[s[k] for k in ("stop_level", "bars_since", "ext_since", "close", "high", "low", "wt1", "wt2", "wt_vel", "dc4h_low", "dc4h_high", "wt1_4h", "wt2_4h", "ema50_4h")])
    s["ext_since"] = 102.0
    assert not SR.fire(_cfg(), False, *[s[k] for k in ("stop_level", "bars_since", "ext_since", "close", "high", "low", "wt1", "wt2", "wt_vel", "dc4h_low", "dc4h_high", "wt1_4h", "wt2_4h", "ema50_4h")])


def test_armed_once_per_chain_and_exclusive_window():
    assert SR.armed("FIXED_STOP", "B12") and not SR.armed("FIXED_STOP", "STOP_RECLAIM_REENTRY") and not SR.armed("WT_CROSS_EXIT", "B12")
    c = _cfg()
    assert SR.exclusive_blocks(c, "FIXED_STOP", 3) and not SR.exclusive_blocks(c, "FIXED_STOP", 5)
    assert not SR.exclusive_blocks(c, "WT_CROSS_EXIT", 1)
    assert not SR.exclusive_blocks(_cfg(STOP_RECLAIM_EXCLUSIVE=False), "FIXED_STOP", 1)
    assert not SR.exclusive_blocks(_cfg(STOP_RECLAIM_REENTRY_ENABLED=False), "FIXED_STOP", 1)


def test_size_mult_only_on_reclaim_opens():
    c = _cfg(STOP_RECLAIM_SIZE_MULT=0.5)
    assert SR.size_mult(c, "VEC_EXACT_OPEN_STOP_RECLAIM_REENTRY") == 0.5 and SR.size_mult(c, "B12 |VEC_EXACT") == 1.0


def test_wired_engine_and_live():
    eng = (ROOT / "v12_quick_engine.py").read_text()
    assert eng.count("_srm.fire(") == 1 and "_srm.exclusive_blocks(" in eng and '"fixed_stop_level": float(_fx_lvl)' in eng
    assert "_sr_fired[0] != i" in eng
    assert "_srm.size_mult(_qq_cfg, reason)" in (ROOT / "ez_manage.py").read_text()
    assert "_srm.size_mult(_qq_cfg, reason)" in (ROOT / "tradier_manage.py").read_text()
