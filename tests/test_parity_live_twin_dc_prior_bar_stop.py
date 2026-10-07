"""Parity lane B 2026-10-06: live DAYTRADE/TECHNICAL DC STOP levels use the prior-bar channel
(live_twins.dc_prior_bar.level_getter, wired in ez_manage.process_position) exactly like the vec
DC_PRIOR_BAR_CHANNEL block (stop level arr[i-1], target level arr[i]); inert at default (STOP TF OFF).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
from live_twins import dc_prior_bar as P
from vec_decisions import dc_channel_exits as X


def _vec_daytrade(px, i, is_long, stop_specs, tgt_specs, arrs, prior=True):
    """Exact copy of the v12 walk level dict construction (DAYTRADE DC block)."""
    dcpb = prior and i > 0
    lv = {}
    for sp in stop_specs:
        fld = sp["field_long"] if is_long else sp["field_short"]
        lv[fld] = float(arrs[fld][i - 1 if dcpb else i])
    for sp in tgt_specs:
        fld = sp["field_long"] if is_long else sp["field_short"]
        lv[fld] = float(arrs[fld][i])
    return X.daytrade_dc_exit(float(px), is_long, stop_specs, tgt_specs, lambda f: lv.get(f, 0.0))


def _series():
    low = np.array([10.0, 10.0, 9.9, 9.8, 9.8, 9.6, 9.6])
    high = np.array([11.0, 11.0, 11.0, 11.1, 11.2, 11.2, 11.3])
    px = np.array([10.5, 9.97, 9.85, 9.70, 11.15, 9.55, 11.29])
    return {"dc_low_15m": low, "dc_high_15m": high}, px


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("target", ["OFF", "15m"])
def test_daytrade_stop_matches_vec(is_long, target):
    arrs, px = _series()
    get = {"DAYTRADE_DC_STOP_TF": "15m", "DAYTRADE_DC_TARGET_TF": target}
    stop, tgt = X.resolve_daytrade_dc(lambda k, d: get.get(k, d))
    fired_any = False
    for i in range(1, len(px)):
        vec = _vec_daytrade(px[i], i, is_long, stop, tgt, arrs)
        ind = {"dc_low_15m": arrs["dc_low_15m"][i], "dc_high_15m": arrs["dc_high_15m"][i], "dc_low_15m_prev": arrs["dc_low_15m"][i - 1], "dc_high_15m_prev": arrs["dc_high_15m"][i - 1]}
        live = X.daytrade_dc_exit(float(px[i]), is_long, stop, tgt, P.level_getter(ind, is_long, stop, True))
        assert live == vec, (i, live, vec)
        fired_any |= vec[0]
    assert fired_any


@pytest.mark.parametrize("is_long", [True, False])
def test_technical_stop_matches_vec_mask(is_long):
    arrs, px = _series()
    buf = 0.0025
    stop_arr = arrs["dc_low_15m"] if is_long else arrs["dc_high_15m"]
    shifted = np.concatenate(([stop_arr[0]], stop_arr[:-1]))
    vec = (px <= shifted * (1 - buf)) if is_long else (px >= shifted * (1 + buf))
    stop, tgt = X.resolve_technical_dc(lambda k, d: {"TECHNICAL_DC_STOP_TF": "15m"}.get(k, d))
    for i in range(1, len(px)):
        ind = {"dc_low_15m": arrs["dc_low_15m"][i], "dc_high_15m": arrs["dc_high_15m"][i], "dc_low_15m_prev": arrs["dc_low_15m"][i - 1], "dc_high_15m_prev": arrs["dc_high_15m"][i - 1]}
        live = X.technical_dc_exit(float(px[i]), is_long, stop, tgt, P.level_getter(ind, is_long, stop, True))[0]
        assert live == bool(vec[i]), i


def test_current_channel_stop_is_structurally_dead_but_prior_fires():
    # the old live getter (current channel incl. the forming bar) can never see px <= dc_low*(1-buf)
    stop, _ = X.resolve_daytrade_dc(lambda k, d: {"DAYTRADE_DC_STOP_TF": "15m"}.get(k, d))
    ind = {"dc_low_15m": 9.70, "dc_low_15m_prev": 9.80}
    assert X.daytrade_dc_exit(9.70, True, stop, [], lambda f: ind.get(f, 0.0))[0] is False
    assert X.daytrade_dc_exit(9.70, True, stop, [], P.level_getter(ind, True, stop, True))[0] is True
    assert X.daytrade_dc_exit(9.70, True, stop, [], P.level_getter(ind, True, stop, False))[0] is False
    assert X.daytrade_dc_exit(9.70, True, stop, [], P.level_getter(ind, True, stop, "False"))[0] is False


def test_prev_missing_falls_back_to_current():
    stop, _ = X.resolve_daytrade_dc(lambda k, d: {"DAYTRADE_DC_STOP_TF": "15m"}.get(k, d))
    g = P.level_getter({"dc_low_15m": 9.7}, True, stop, True)
    assert g("dc_low_15m") == 9.7


def test_targets_stay_same_bar():
    _, tgt = X.resolve_daytrade_dc(lambda k, d: {"DAYTRADE_DC_TARGET_TF": "15m"}.get(k, d))
    stop, _ = X.resolve_daytrade_dc(lambda k, d: {"DAYTRADE_DC_STOP_TF": "15m"}.get(k, d))
    g = P.level_getter({"dc_high_15m": 11.0, "dc_high_15m_prev": 10.0, "dc_low_15m": 9.0, "dc_low_15m_prev": 9.5}, True, stop, True)
    assert g("dc_high_15m") == 11.0 and g("dc_low_15m") == 9.5


def test_default_inert():
    c = config_mod.Config()
    get = lambda k, d: getattr(c, k, d)
    stop, _ = X.resolve_daytrade_dc(get)
    tstop, _ = X.resolve_technical_dc(get)
    assert stop == [] and tstop == []
    assert c.DC_PRIOR_BAR_CHANNEL is True
    g = P.level_getter({"dc_low_15m": 9.7, "dc_low_15m_prev": 9.8, "dc_high_15m": 11.0}, True, stop, True)
    assert g("dc_low_15m") == 9.7 and g("dc_high_15m") == 11.0


def test_process_position_uses_getter():
    src = (Path(__file__).resolve().parents[1] / "ez_manage.py").read_text()
    assert "_dc_channel_exits.daytrade_dc_exit(current_price, _gx_is_long, _gx_stop, _gx_tgt, _gx_lvl)" in src
    assert "_dc_channel_exits.technical_dc_exit(current_price, _gx_is_long, _tx_stop, _tx_tgt, _tx_lvl)" in src
