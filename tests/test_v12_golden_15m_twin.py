"""GOLDEN_RULE 15m-fallback twin (USER 2026-10-11).

NPZs never carry 3m/5m WT by policy (speed) — the GR trigger falls back to
15m cross-state (just as valid, less noise). Backtest-only twin: no live
callers (verified: ez_manage/tradier_manage never import lane_vec_wave5).
"""
import numpy as np
import pytest

import v12_quick_engine as V
from vec_decisions import lane_vec_wave5 as W


def _cfg(**kw):
    c = V.QuickConfig()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_wt_pair_prefers_5m_contract():
    n = 50
    npz = {"wt1_5m": np.full(n, 60.0), "wt2_5m": np.full(n, 50.0),
           "wt1_15m": np.full(n, 10.0), "wt2_15m": np.full(n, 20.0)}
    w1, w2 = W._gr_wt_pair(npz, n)
    assert float(w1[0]) == 60.0  # 5m still wins when a caller supplies it


def test_wt_pair_15m_fallback():
    n = 50
    npz = {"wt1_15m": np.full(n, 10.0), "wt2_15m": np.full(n, 20.0)}
    pair = W._gr_wt_pair(npz, n)
    assert pair is not None  # was None before the fallback: twin dead on real NPZ
    assert float(pair[0][0]) == 10.0


def test_wt_pair_none_when_no_wt():
    assert W._gr_wt_pair({"close": np.ones(10)}, 10) is None  # still fail-open


def test_scale_synthetic_trigger_long():
    n = 120
    close = np.full(n, 100.0)
    close[60:] = 110.0  # breakout over flat 15m DC high
    npz = {"wt1_15m": np.full(n, 60.0), "wt2_15m": np.full(n, 40.0),
           "close": close, "dc_high_15m": np.full(n, 105.0),
           "dc_low_15m": np.full(n, 95.0), "bb_upper_15m": np.full(n, 200.0),
           "bb_lower_15m": np.full(n, 1.0), "dc_high_1h": np.full(n, 105.0),
           "dc_low_1h": np.full(n, 1.0), "bb_upper_1h": np.full(n, 500.0),
           "bb_lower_1h": np.full(n, 1.0), "dc_high_4h": np.full(n, 500.0),
           "dc_low_4h": np.full(n, 1.0), "bb_upper_4h": np.full(n, 500.0),
           "bb_lower_4h": np.full(n, 1.0), "dc_high_D": np.full(n, 500.0),
           "dc_low_D": np.full(n, 1.0), "bb_upper_D": np.full(n, 500.0),
           "bb_lower_D": np.full(n, 1.0)}
    out = W.golden_rule_base_scale(npz, n, True, _cfg(GOLDEN_RULE_BASE_USD=5.0))
    assert out is not None
    assert bool(np.any(out[60:] != 1.0))  # trigger bars scaled
    assert bool(np.all(out[:60] == 1.0))  # quiet bars untouched


def test_scale_base_zero_none():
    n = 30
    npz = {"wt1_15m": np.full(n, 60.0), "wt2_15m": np.full(n, 40.0), "close": np.full(n, 100.0)}
    assert W.golden_rule_base_scale(npz, n, True, _cfg(GOLDEN_RULE_BASE_USD=0.0)) is None


def test_scale_real_npz_measures():
    z = np.load("backtest_v8/indicators/ENAUSDC.npz", allow_pickle=True)
    npz = {k: z[k] for k in z.files}
    n = len(npz["close"])
    out = W.golden_rule_base_scale(npz, n, True, _cfg(GOLDEN_RULE_BASE_USD=5.0))
    assert out is not None  # 15m fallback: measurable on real frozen NPZ
    assert np.all(np.isfinite(out)) and bool(np.all(out >= 1.0))
