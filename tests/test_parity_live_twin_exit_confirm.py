"""Parity lane B 2026-10-06: live exit_confirm (EXIT_TOP_FADE_FILTER_TF / CANDLE_PATTERN_STOPS_FILTER_TF) == vec
generic_filter_tf 'wt_top_fade' / 'bar_pattern_against' (v12 _xc_ok gate on the exit_sig close block); inert when OFF."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_twins import exit_confirm as XC
from vec_decisions import generic_filter_tf as GFT


def _safe(npz, key, n, default=0.0):
    v = npz.get(key)
    if v is None or len(v) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(v, dtype=float)


@pytest.mark.parametrize("is_long", [True, False])
def test_wt_top_fade_matches_vec(is_long):
    w1 = np.array([0.0, 70.0, 65.0, 75.0, -70.0, -65.0, -75.0, 30.0, 61.0])
    close = np.ones(len(w1))
    vec = GFT._cond("wt_top_fade", {"wt1_15m": w1}, len(w1), "15m", is_long, close, _safe)
    get = lambda k, d=None: "15m" if k == "EXIT_TOP_FADE_FILTER_TF" else d
    live = []
    for i in range(len(w1)):
        prev = w1[i - 1] if i > 0 else w1[0]
        live.append(XC.exit_confirm({"wt1_15m": w1[i], "wt1_15m_prev": prev}, is_long, get)[0])
    assert live == [bool(x) for x in vec]
    assert any(live) and not all(live)


@pytest.mark.parametrize("is_long", [True, False])
def test_bar_pattern_against_matches_vec(is_long):
    names = list(XC.BAR_PATTERN_CODES)
    codes = np.array([XC.BAR_PATTERN_CODES[n] for n in names], dtype=float)
    vec = GFT._cond("bar_pattern_against", {"bar_pattern_1h": codes}, len(codes), "1h", is_long, np.ones(len(codes)), _safe)
    get = lambda k, d=None: "1h" if k == "CANDLE_PATTERN_STOPS_FILTER_TF" else d
    live = [XC.exit_confirm({"bar_pattern_1h": n}, is_long, get)[0] for n in names]
    assert live == [bool(x) for x in vec]
    # missing key -> fail-open on both sides
    assert XC.exit_confirm({}, is_long, get)[0] is True
    assert bool(GFT._cond("bar_pattern_against", {}, 1, "1h", is_long, np.ones(1), _safe)[0]) is True


def test_off_inert():
    get = lambda k, d=None: "OFF"
    assert XC.exit_confirm({"wt1_15m": 10.0, "bar_pattern_15m": "hammer"}, True, get) == (True, "")
    assert XC.any_active(get) is False


def test_and_of_both():
    get = lambda k, d=None: {"EXIT_TOP_FADE_FILTER_TF": "15m", "CANDLE_PATTERN_STOPS_FILTER_TF": "15m"}.get(k, d)
    assert XC.exit_confirm({"wt1_15m": 70.0, "wt1_15m_prev": 80.0, "bar_pattern_15m": "shooting_star"}, True, get)[0] is True
    assert XC.exit_confirm({"wt1_15m": 70.0, "wt1_15m_prev": 80.0, "bar_pattern_15m": "hammer"}, True, get)[0] is False
