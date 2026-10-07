"""Parity lane B 2026-10-06: live fresh-OPEN FILTER_TF gates (live_twins.parity_open_gates.entry_filter_tf_veto,
called from ez_manage.execute_now) == vec predicates (filter_tf_gates.mom3_entry_gate, generic_filter_tf 'dc_break' /
'dc_retest_hold'), True and False cases, inert at default.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
from live_twins import parity_open_gates as G
from vec_decisions import filter_tf_gates as FTG
from vec_decisions import generic_filter_tf as GFT


def _safe(npz, key, n, default=0.0):
    v = npz.get(key)
    if v is None or len(v) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(v, dtype=float)


def _getter(d):
    return lambda k, default=None: d.get(k, default)


def _ind(npz, i):
    return {k: float(v[i]) for k, v in npz.items()}


def test_config_defaults_inert():
    c = config_mod.Config()
    get = lambda k, d=None: getattr(c, k, d)
    ind = {"close_3bar_15m": 100.0, "dc_high_15m_prev": 999.0, "dc_low_15m_prev": 0.001, "low_15m": 50.0, "high_15m": 50.0}
    for is_long in (True, False):
        assert G.entry_filter_tf_veto(ind, is_long, 100.0, get) == (False, "")
    assert str(c.MOM3_FILTER_TF).upper() == "OFF" and str(c.DC_BREAK_FILTER_TF).upper() == "OFF"
    assert c.BREAKOUT_RETEST_ARMED_ENABLED is False


@pytest.mark.parametrize("is_long", [True, False])
def test_mom3_matches_vec(is_long):
    close = np.array([100.0, 98.0, 101.5, 99.0, 100.0, 103.0])
    c3 = np.array([100.0, 100.0, 100.0, 100.0, 0.0, 100.0])
    npz = {"close_3bar_1h": c3}
    cfg = SimpleNamespace(MOM3_FILTER_TF="1h", MOM3_LONG_THRESHOLD=-1.0, MOM3_SHORT_THRESHOLD=1.0)
    vec = FTG.mom3_entry_gate(npz, len(close), is_long, cfg, close, _safe)
    get = _getter(vars(cfg))
    live_allow = [not G.entry_filter_tf_veto({"close_3bar_1h": c3[i]}, is_long, close[i], get)[0] for i in range(len(close))]
    assert live_allow == [bool(x) for x in vec]
    assert any(live_allow) and not all(live_allow)


@pytest.mark.parametrize("is_long", [True, False])
def test_dc_break_matches_vec(is_long):
    close = np.array([10.0, 10.6, 9.4, 10.0, 11.0])
    npz = {"dc_high_15m_prev": np.array([10.5, 10.5, 10.5, 0.0, 10.5]), "dc_low_15m_prev": np.array([9.5, 9.5, 9.5, 0.0, 9.5])}
    vec = GFT._cond("dc_break", npz, len(close), "15m", is_long, close, _safe)
    get = _getter({"DC_BREAK_FILTER_TF": "15m"})
    live_allow = [not G.entry_filter_tf_veto(_ind(npz, i), is_long, close[i], get)[0] for i in range(len(close))]
    assert live_allow == [bool(x) for x in vec]
    assert any(live_allow) and not all(live_allow)


@pytest.mark.parametrize("is_long", [True, False])
def test_breakout_retest_matches_vec_and_needs_armed(is_long):
    close = np.array([10.6, 10.6, 10.4, 9.4, 9.6, 10.0])
    npz = {
        "dc_high_1h_prev": np.array([10.5, 10.5, 10.5, 10.5, 10.5, 0.0]),
        "dc_low_1h_prev": np.array([9.5, 9.5, 9.5, 9.5, 9.5, 0.0]),
        "low_1h": np.array([10.4, 10.55, 10.3, 9.3, 9.6, 9.0]),
        "high_1h": np.array([10.7, 10.7, 10.6, 9.6, 9.4, 11.0]),
    }
    vec = GFT._cond("dc_retest_hold", npz, len(close), "1h", is_long, close, _safe)
    armed = _getter({"BREAKOUT_RETEST_ARMED_ENABLED": True, "BREAKOUT_RETEST_FILTER_TF": "1h"})
    live_allow = [not G.entry_filter_tf_veto(_ind(npz, i), is_long, close[i], armed)[0] for i in range(len(close))]
    assert live_allow == [bool(x) for x in vec]
    assert any(live_allow) and not all(live_allow)
    unarmed = _getter({"BREAKOUT_RETEST_ARMED_ENABLED": False, "BREAKOUT_RETEST_FILTER_TF": "1h"})
    assert not any(G.entry_filter_tf_veto(_ind(npz, i), is_long, close[i], unarmed)[0] for i in range(len(close)))


def test_missing_data_fails_open():
    get = _getter({"MOM3_FILTER_TF": "1h", "DC_BREAK_FILTER_TF": "4h", "BREAKOUT_RETEST_ARMED_ENABLED": True, "BREAKOUT_RETEST_FILTER_TF": "1h"})
    assert G.entry_filter_tf_veto({}, True, 10.0, get) == (False, "")
    assert G.entry_filter_tf_veto({}, False, 10.0, get) == (False, "")


def test_parity_mask_none_string_inert():
    get = _getter({"MOM3_FILTER_TF": "None", "DC_BREAK_FILTER_TF": "None"})
    assert G.entry_filter_tf_veto({"close_3bar_15m": 1.0, "dc_high_15m_prev": 1e9}, True, 10.0, get) == (False, "")


def test_execute_now_calls_live_twin():
    src = (Path(__file__).resolve().parents[1] / "ez_manage.py").read_text()
    assert "_lpog.entry_filter_tf_veto(_lpog_ind, _lpog_long, _lpog_px, _lpog_get)" in src
    assert "_lpog.strict_open_veto(_lpog_ind, _lpog_long, _lpog_px, _lpog_get)" in src
    assert 'return f"BLOCKED_PARITY_FILTER_TF_{_lpog_why}"' in src
