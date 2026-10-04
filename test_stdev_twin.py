"""test_stdev_twin.py — parity tests for vec_decisions/twin_stdev.py.

Mirrors cited live/vec formulas exactly:
  lookback ratio  lb_def/lb            — v12_quick_engine_fast_v2.py:9786-9787
  band ratio      clip(m*bm/2.5)       — v12_quick_engine_fast_v2.py:9791-9793
  boost gate      strict-inequality fav — ez_manage.py:27146 / tradier_manage.py:28612
  ladder OR-gate                       — tradier_manage.py:28577
  clip-max ladder map                  — tradier_manage.py:28583-28589
Standalone: no engine import (engines are heavyweight); cfg is a namespace
carrying the same field names/defaults as Config/TradierConfig/QuickConfig.
"""
import math
from types import SimpleNamespace

import numpy as np

from vec_decisions.twin_stdev import get, get_scalar


def _cfg(**kw):
    base = dict(MODE="tradier", STDEV_SLOPE_SIZING_ENABLED=True, BAND_SLOPE_SIZING_V2_ENABLED=True, BAND_SLOPE_SIZING_V2_TF="D", BAND_SLOPE_SIZING_V2_MIN=0.5, BAND_SLOPE_SIZING_V2_MAX=2.5, STDEV_SLOPE_SIZING_D_MAX=10.0, STDEV_SLOPE_SIZING_4H_MAX=4.0, STDEV_SLOPE_SIZING_1H_MAX=2.0, STDEV_SLOPE_SIZING_15M_MAX=1.5, STDEV_SLOPE_SIZING_MODE="slope_to_top", STDEV_BAND_MULTIPLIER=2.5, STDEV_SLOPE_LOOKBACK_D=180, STDEV_SLOPE_LOOKBACK_4H=180, STDEV_SLOPE_LOOKBACK_1H=168, STDEV_SLOPE_LOOKBACK_15M=96, STDEV_BULL_SLOPE_BOOST_ENABLED=False, STDEV_BULL_SLOPE_BOOST_MULT=1.5)
    base.update(kw)
    return SimpleNamespace(**base)


def _npz(n, slope=0.0, tf="D"):
    return {"lrL_slope_%s" % tf: np.full(n, float(slope), dtype=float), "lrL_pct_b_%s" % tf: np.full(n, 0.4, dtype=float)}


def test_inert_at_defaults_vec():
    out = get(_npz(32, slope=3.0), 32, True, _cfg())
    assert out.shape == (32,)
    assert bool(np.all(out == 1.0))


def test_inert_at_defaults_scalar():
    assert get_scalar({"lrL_slope_D": 3.0, "lrL_pct_b_D": 0.1}, True, _cfg()) == 1.0
    assert get_scalar({"lrL_slope_D": -3.0}, False, _cfg()) == 1.0


def test_gate_off_forces_inert_despite_extreme_switches():
    cfg = _cfg(STDEV_SLOPE_SIZING_ENABLED=False, BAND_SLOPE_SIZING_V2_ENABLED=False, STDEV_BAND_MULTIPLIER=9.9, STDEV_SLOPE_LOOKBACK_D=1, STDEV_BULL_SLOPE_BOOST_ENABLED=True, STDEV_BULL_SLOPE_BOOST_MULT=9.0)
    assert bool(np.all(get(_npz(16, slope=5.0), 16, True, cfg) == 1.0))
    assert get_scalar({"lrL_slope_D": 5.0}, True, cfg) == 1.0


def test_lookback_ratio_matches_fast_v2():
    assert bool(np.all(get(_npz(8), 8, True, _cfg(STDEV_SLOPE_LOOKBACK_D=90)) == 2.0))
    assert bool(np.all(get(_npz(8), 8, False, _cfg(STDEV_SLOPE_LOOKBACK_D=360)) == 0.5))
    assert get_scalar({}, True, _cfg(STDEV_SLOPE_LOOKBACK_D=60)) == 3.0


def test_lookback_tf_selects_window_only():
    cfg = _cfg(BAND_SLOPE_SIZING_V2_TF="15m", STDEV_SLOPE_LOOKBACK_D=1, STDEV_SLOPE_LOOKBACK_4H=1, STDEV_SLOPE_LOOKBACK_1H=1, STDEV_SLOPE_LOOKBACK_15M=96)
    assert bool(np.all(get(_npz(8, tf="15m"), 8, True, cfg) == 1.0))
    cfg2 = _cfg(BAND_SLOPE_SIZING_V2_TF="1h", STDEV_SLOPE_LOOKBACK_1H=84)
    assert bool(np.all(get(_npz(8, tf="1h"), 8, True, cfg2) == 2.0))


def test_band_ratio_matches_fast_v2():
    assert bool(np.all(get(_npz(8), 8, True, _cfg(STDEV_BAND_MULTIPLIER=5.0)) == 2.0))
    assert get_scalar({}, False, _cfg(STDEV_BAND_MULTIPLIER=1.25)) == 0.5


def test_full_mode_clip_matches_fast_v2():
    cfg = _cfg(STDEV_BAND_MULTIPLIER=5.0)
    base = np.full(8, 8.0)
    out = get(_npz(8), 8, True, cfg, base=base)
    assert bool(np.all(out == 10.0))
    out2 = get(_npz(8), 8, True, _cfg(STDEV_BAND_MULTIPLIER=0.5), base=np.full(8, 1.0))
    assert bool(np.all(out2 == 0.5))
    assert get_scalar({}, True, cfg, base=8.0) == 10.0


def test_boundaries_fail_closed():
    for bad in (0, -5, 0.0):
        assert bool(np.all(get(_npz(4), 4, True, _cfg(STDEV_SLOPE_LOOKBACK_D=bad)) == 1.0))
        assert bool(np.all(get(_npz(4), 4, True, _cfg(STDEV_BAND_MULTIPLIER=bad)) == 1.0))
    assert get_scalar({}, True, _cfg(STDEV_SLOPE_LOOKBACK_D=0)) == 1.0


def test_boost_long_gates_on_strict_positive_slope():
    cfg = _cfg(STDEV_BULL_SLOPE_BOOST_ENABLED=True, STDEV_BULL_SLOPE_BOOST_MULT=1.5)
    assert bool(np.all(get(_npz(6, slope=0.5), 6, True, cfg) == 1.5))
    assert bool(np.all(get(_npz(6, slope=-0.5), 6, True, cfg) == 1.0))
    assert bool(np.all(get(_npz(6, slope=0.0), 6, True, cfg) == 1.0))
    assert get_scalar({"lrL_slope_D": 0.5}, True, cfg) == 1.5
    assert get_scalar({"lrL_slope_D": -0.5}, True, cfg) == 1.0


def test_boost_short_reversed():
    cfg = _cfg(STDEV_BULL_SLOPE_BOOST_ENABLED=True, STDEV_BULL_SLOPE_BOOST_MULT=2.0)
    assert bool(np.all(get(_npz(6, slope=-0.5), 6, False, cfg) == 2.0))
    assert bool(np.all(get(_npz(6, slope=0.5), 6, False, cfg) == 1.0))
    assert get_scalar({"lrL_slope_D": -0.5}, False, cfg) == 2.0


def test_boost_disabled_or_bad_mult_is_inert():
    assert bool(np.all(get(_npz(6, slope=9.0), 6, True, _cfg()) == 1.0))
    cfg = _cfg(STDEV_BULL_SLOPE_BOOST_ENABLED=True, STDEV_BULL_SLOPE_BOOST_MULT=0.0)
    assert bool(np.all(get(_npz(6, slope=9.0), 6, True, cfg) == 1.0))
    assert get_scalar({"lrL_slope_D": 9.0}, True, cfg) == 1.0


def test_boost_missing_slope_fails_closed():
    cfg = _cfg(STDEV_BULL_SLOPE_BOOST_ENABLED=True, STDEV_BULL_SLOPE_BOOST_MULT=1.5)
    assert bool(np.all(get({}, 6, True, cfg) == 1.0))
    assert get_scalar({}, True, cfg) == 1.0
    assert get_scalar(None, True, cfg) == 1.0


def test_scalar_vec_agreement_full_mode():
    cfg = _cfg(STDEV_SLOPE_LOOKBACK_D=90, STDEV_BAND_MULTIPLIER=5.0, STDEV_BULL_SLOPE_BOOST_ENABLED=True, STDEV_BULL_SLOPE_BOOST_MULT=1.5)
    n = 5
    npz = {"lrL_slope_D": np.array([0.5, -0.5, 0.0, 2.0, -2.0])}
    vec = get(npz, n, True, cfg, base=np.full(n, 2.0))
    for i in range(n):
        s = get_scalar({"lrL_slope_D": float(npz["lrL_slope_D"][i])}, True, cfg, base=2.0)
        assert vec[i] == s == min(max(2.0 * 2.0 * 2.0 * (1.5 if npz["lrL_slope_D"][i] > 0 else 1.0), 0.5), 10.0)


def test_live_formula_agreement_hand_computed():
    cfg = _cfg(BAND_SLOPE_SIZING_V2_TF="4h", STDEV_SLOPE_LOOKBACK_4H=60, STDEV_BAND_MULTIPLIER=3.75, STDEV_BULL_SLOPE_BOOST_ENABLED=True, STDEV_BULL_SLOPE_BOOST_MULT=1.5)
    lb_ratio = 180.0 / 60.0
    bm_ratio = 3.75 / 2.5
    want_fav = min(max(1.0 * lb_ratio * bm_ratio * 1.5, 0.5), 4.0)
    want_unfav = min(max(1.0 * lb_ratio * bm_ratio * 1.0, 0.5), 4.0)
    assert get_scalar({"lrL_slope_4h": 1.0}, True, cfg, base=1.0) == want_fav == 4.0
    assert get_scalar({"lrL_slope_4h": -1.0}, True, cfg, base=1.0) == want_unfav == 4.0
    vec = get({"lrL_slope_4h": np.array([1.0, -1.0])}, 2, True, cfg, base=np.ones(2))
    assert list(vec) == [want_fav, want_unfav]


def test_nan_inputs_fail_closed_finite():
    cfg = _cfg(STDEV_BULL_SLOPE_BOOST_ENABLED=True)
    out = get({"lrL_slope_D": np.array([math.nan, math.inf])}, 2, True, cfg)
    assert bool(np.all(np.isfinite(out))) and bool(np.all(out == 1.0))
    assert get_scalar({"lrL_slope_D": math.nan}, True, cfg) == 1.0
    assert get_scalar({}, True, _cfg(), base=math.nan) == 1.0
