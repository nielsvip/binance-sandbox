"""Parity lane B 2026-10-06: live DC_EDGE / STDEV_SLOPE sizing multipliers (live_twins.sizing, applied in
ez_manage.calculate_final_order_quantity) == vec v12_quick_engine.compute_regime_sizing_mult legs; switch off = no change."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import v12_quick_engine as V
from live_twins import sizing as S


def _cfg(**kw):
    base = dict(FIXED_QUANTITY_ENABLED=False, REGIME_ADAPTIVE_ENABLED=False, REGIME_GATE_ENABLED=False, EMA_DIST_SIZING_ENABLED=False,
                ATR_ADAPTIVE_SIZING_ENABLED=False, DC_EDGE_SIZING_ENABLED=False, SLOPE_SIZING_LIVE_TWIN_ENABLED=False,
                STDEV_SLOPE_SIZING_ENABLED=False, STDEV_BREAKOUT_RETEST_SIZE_MULT=1.5, MODE="crypto")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("lo,hi", [(1.0, 3.0), (0.5, 1.5)])
def test_dc_edge_matches_vec(is_long, lo, hi):
    pos = np.array([0.0, 0.1, 0.5, 0.9, 1.0])
    n = len(pos)
    npz = {"close": np.full(n, 100.0), "dc_position_15m": pos}
    cfg = _cfg(DC_EDGE_SIZING_ENABLED=True, DC_EDGE_SIZING_MIN_MULT=lo, DC_EDGE_SIZING_MAX_MULT=hi)
    vec = V.compute_regime_sizing_mult(npz, n, is_long, cfg)
    get = lambda k, d: getattr(cfg, k, d)
    live = [S.dc_edge_mult(get, {"dc_position_15m": p}, is_long) for p in pos]
    assert np.allclose(live, vec)


@pytest.mark.parametrize("is_long", [True, False])
def test_stdev_slope_matches_vec(is_long):
    pb = np.array([-0.3, 0.0, 0.25, 0.5, 0.9, 1.4])
    n = len(pb)
    npz = {"close": np.full(n, 100.0), "lrL_pct_b_D": pb, "lrL_slope_D": np.full(n, 0.1)}
    cfg = _cfg(STDEV_SLOPE_SIZING_ENABLED=True)
    vec = V.compute_regime_sizing_mult(npz, n, is_long, cfg)
    get = lambda k, d: getattr(cfg, k, d)
    live = [S.stdev_slope_mult(get, {"lrL_pct_b_D": p, "lrL_slope_D": 0.1}, is_long) for p in pb]
    assert np.allclose(live, vec)
    assert max(live) > 1.0


def test_stdev_missing_channel_skips_like_vec():
    cfg = _cfg(STDEV_SLOPE_SIZING_ENABLED=True)
    npz = {"close": np.full(2, 100.0)}
    assert np.allclose(V.compute_regime_sizing_mult(npz, 2, True, cfg), 1.0)
    assert S.stdev_slope_mult(lambda k, d: getattr(cfg, k, d), {}, True) is None


def test_switches_off_inert():
    cfg = _cfg()
    get = lambda k, d: getattr(cfg, k, d)
    assert S.dc_edge_mult(get, {"dc_position_15m": 0.0}, True) is None
    assert S.stdev_slope_mult(get, {"lrL_pct_b_D": 0.0, "lrL_slope_D": 1.0}, True) is None
    assert S.stdev_slope_mult(lambda k, d: True, {"lrL_pct_b_D": 0.0, "lrL_slope_D": 1.0}, True) is None  # SLOPE_SIZING_LIVE_TWIN wins like vec elif
