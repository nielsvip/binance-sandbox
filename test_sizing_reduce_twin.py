"""Tests for vec_decisions/twin_sizing_reduce.py — stocks-live twins.

Covers inert-defaults, threshold boundaries, and formula agreement vs the
crypto-live / shared implementations (strategy_enhancements, momentum_watchdog,
ez_satoshit, position_evaluator). Run: python -m pytest test_sizing_reduce_twin.py -q
"""
import math

import pytest

import vec_decisions.twin_sizing_reduce as T


TRADIER = {
    "FIXED_QUANTITY_ENABLED": False,
    "ATR_ADAPTIVE_SIZING_ENABLED": False,
    "ATR_ADAPTIVE_STOP_TF": "1h",
    "ATR_ADAPTIVE_SIZING_TARGET_PCT": 1.5,
    "EMA_DIST_SIZING_ENABLED": False,
    "EMA_DIST_SIZING_MULT": 2.0,
    "E_1_WT_EXIT_USE_DELTA_ENABLED": False,
    "E_1_EXIT_DELTA_THR": 50.0,
    "HTF_AGAINST_FORCE_CLOSE_ENABLED": False,
    "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM": True,
    "HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H": True,
    "HTF_AGAINST_FORCE_CLOSE_CONFIRM_3M": True,
    "HTF_AGAINST_FORCE_CLOSE_CONFIRM_D": True,
    "NEWBORN_LOSS_KILL_ENABLED": False,
    "NEWBORN_LOSS_KILL_WINDOW_MIN": 30.0,
    "NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT": 0.0,
    "NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST": True,
    "NEWBORN_LOSS_KILL_VEL_TF": "",
    "BOTTOM_EXIT_HTF_WT_VETO_ENABLED": True,
    "WT_PERCENTILE_EXIT_ENABLED": False,
    "WT_PERCENTILE_EXIT_OB_D": 75.0,
    "WT_PERCENTILE_EXIT_OB_4H": 55.0,
    "WT_PERCENTILE_EXIT_OS_D": 10.0,
    "WT_PERCENTILE_EXIT_OS_4H": 25.0,
    "CYCLE_TP_TIERED_ENABLED": False,
    "CYCLE_TP_TIERED_LEVELS": [0.0015, 0.003, 0.005, 0.007, 0.010, 0.015, 0.020, 0.030],
    "CYCLE_TP_TIERED_FRAC": 0.25,
    "NOLOSS_MIN_PROFIT_PCT": 0.0,
    "PARTIAL_PROFIT_LOCK_ENABLED": False,
    "PARTIAL_PROFIT_LOCK_GAIN_PCT": 1.5,
    "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 1.75,
    "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT": 0.1,
    "PARTIAL_PROFIT_LOCK_FRAC": 0.5,
    "SATOSHIT_EXIT_ENABLED": False,
    "SATOSHIT_ENABLED": True,
    "SATOSHIT_EXIT_PARTIAL_PCT": 0.7,
    "PYRAMID_ENABLED": False,
    "PYRAMID_MIN_GAIN_PCT": 1.5,
    "PYRAMID_MIN_WT_VEL_1H": 2.0,
    "PYRAMID_MIN_DC_POS_15M": 0.7,
    "PYRAMID_MAX_DC_POS_15M_SHORT": 0.3,
    "PYRAMID_SIZE_MULT": 0.5,
    "MOMENTUM_SMA_WATCHDOG_ENABLED": False,
    "MOMENTUM_SMA_WATCHDOG_PCT": 1.0,
    "WATCHDOG_DC_FORCE_OPEN_ENABLED": True,
    "WATCHDOG_DC_TFS": ["15m", "1h", "4h", "D"],
    "WATCHDOG_DC_BASE_USD": 25.0,
    "WATCHDOG_DC_MAX_USD": 600.0,
    "WATCHDOG_DC_MULT_15M": 1.0,
    "WATCHDOG_DC_MULT_1H": 4.0,
    "WATCHDOG_DC_MULT_4H": 8.0,
    "WATCHDOG_DC_MULT_D": 16.0,
}


def make_get(over=None):
    cfg = dict(TRADIER)
    cfg.update(over or {})
    return lambda k, d: cfg.get(k, d)


# ---------- FIXED_QUANTITY ----------

def test_fixed_default_inert():
    assert T.fixed_quantity_active(make_get()) is False


def test_fixed_enabled_active():
    assert T.fixed_quantity_active(make_get({"FIXED_QUANTITY_ENABLED": True})) is True


# ---------- ATR_ADAPTIVE_SIZING ----------

def test_atr_disabled_inert():
    assert T.atr_adaptive_size_mult(make_get(), {"atr_1h": 2.0}, 100.0) == 1.0


def test_atr_formula():
    get = make_get({"ATR_ADAPTIVE_SIZING_ENABLED": True})
    assert T.atr_adaptive_size_mult(get, {"atr_1h": 2.0}, 100.0) == pytest.approx(0.75)


def test_atr_clip_high():
    get = make_get({"ATR_ADAPTIVE_SIZING_ENABLED": True})
    assert T.atr_adaptive_size_mult(get, {"atr_1h": 0.01}, 100.0) == pytest.approx(4.0)


def test_atr_clip_low():
    get = make_get({"ATR_ADAPTIVE_SIZING_ENABLED": True})
    assert T.atr_adaptive_size_mult(get, {"atr_1h": 20.0}, 100.0) == pytest.approx(0.25)


def test_atr_missing_data_inert():
    get = make_get({"ATR_ADAPTIVE_SIZING_ENABLED": True})
    assert T.atr_adaptive_size_mult(get, {}, 100.0) == 1.0
    assert T.atr_adaptive_size_mult(get, {"atr_1h": 2.0}, 0.0) == 1.0


def test_atr_tf_select():
    get = make_get({"ATR_ADAPTIVE_SIZING_ENABLED": True, "ATR_ADAPTIVE_STOP_TF": "4h"})
    assert T.atr_adaptive_size_mult(get, {"atr_4h": 3.0}, 100.0) == pytest.approx(0.5)


# ---------- EMA_DIST_SIZING ----------

def test_ema_disabled_inert():
    assert T.ema_dist_size_mult(make_get(), {"ema_20_1h": 100.0}, 101.0) == 1.0


def test_ema_vec_formula():
    get = make_get({"EMA_DIST_SIZING_ENABLED": True})
    assert T.ema_dist_size_mult(get, {"ema_20_1h": 100.0}, 101.0) == pytest.approx(1.02)
    assert T.ema_dist_size_mult(get, {"ema_20_1h": 100.0}, 99.0) == pytest.approx(1.02)


def test_ema_missing_inert():
    get = make_get({"EMA_DIST_SIZING_ENABLED": True})
    assert T.ema_dist_size_mult(get, {}, 101.0) == 1.0


# ---------- E_1 ----------

def test_e1_disabled_inert():
    fire, _ = T.e1_wt_delta_fires(make_get(), {"wt_composite_delta": -999.0}, True)
    assert fire is False


def test_e1_long_fires():
    get = make_get({"E_1_WT_EXIT_USE_DELTA_ENABLED": True})
    fire, reason = T.e1_wt_delta_fires(get, {"wt_composite_delta": -60.0}, True)
    assert fire is True and "E_1_WT_DELTA_EXIT" in reason


def test_e1_boundary_strict():
    get = make_get({"E_1_WT_EXIT_USE_DELTA_ENABLED": True})
    assert T.e1_wt_delta_fires(get, {"wt_composite_delta": -50.0}, True)[0] is False
    assert T.e1_wt_delta_fires(get, {"wt_composite_delta": -50.001}, True)[0] is True


def test_e1_short_fires():
    get = make_get({"E_1_WT_EXIT_USE_DELTA_ENABLED": True})
    assert T.e1_wt_delta_fires(get, {"wt_composite_delta": 60.0}, False)[0] is True
    assert T.e1_wt_delta_fires(get, {"wt_composite_delta": 10.0}, False)[0] is False


def test_e1_missing_key_inert():
    get = make_get({"E_1_WT_EXIT_USE_DELTA_ENABLED": True})
    assert T.e1_wt_delta_fires(get, {}, True)[0] is False


def test_e1_agrees_with_position_evaluator():
    import position_evaluator as pe

    class Cfg:
        pass
    cfg = Cfg()
    for k, v in TRADIER.items():
        setattr(cfg, k, v)
    cfg.E_1_WT_EXIT_USE_DELTA_ENABLED = True
    cfg.WT_PERCENTILE_EXIT_ENABLED = False
    cfg.WT_EXHAUST_EXIT_ENABLED = False
    cfg.E_3_USE_WT_STRUCTURE_EXIT_MODE = 0
    for is_long, delta, want in [(True, -60.0, True), (True, -10.0, False), (False, 60.0, True), (False, 10.0, False)]:
        ind = {"wt_composite_delta": delta}
        code, _ = pe.evaluate_exit_gates_core(ind, is_long, cfg)
        get = make_get({"E_1_WT_EXIT_USE_DELTA_ENABLED": True})
        fire, _ = T.e1_wt_delta_fires(get, ind, is_long)
        assert fire is want
        assert (code == pe.EXIT_E1_WT_DELTA) is want


# ---------- HTF_AGAINST ----------

def _htf_ind(**kw):
    ind = {"wt1_1h": -10.0, "wt2_1h": 5.0, "wt1_15m": -8.0, "wt2_15m": 4.0,
           "wt1_4h": -6.0, "wt2_4h": 3.0, "wt1_5m": -9.0, "wt2_5m": 2.0,
           "wt1_D": -4.0, "wt2_D": 1.0}
    ind.update(kw)
    return ind


def test_htf_disabled_inert():
    assert T.htf_against_fires(make_get(), _htf_ind(), True)[0] is False


def test_htf_long_fires_all_confirms():
    get = make_get({"HTF_AGAINST_FORCE_CLOSE_ENABLED": True})
    fire, reason = T.htf_against_fires(get, _htf_ind(), True)
    assert fire is True and "HTF_AGAINST" in reason


def test_htf_15m_confirm_blocks():
    get = make_get({"HTF_AGAINST_FORCE_CLOSE_ENABLED": True})
    assert T.htf_against_fires(get, _htf_ind(wt1_15m=9.0, wt2_15m=2.0), True)[0] is False


def test_htf_no_data_inert():
    get = make_get({"HTF_AGAINST_FORCE_CLOSE_ENABLED": True})
    assert T.htf_against_fires(get, {}, True)[0] is False


def test_htf_short_mirror():
    get = make_get({"HTF_AGAINST_FORCE_CLOSE_ENABLED": True})
    ind = {"wt1_1h": 10.0, "wt2_1h": -5.0, "wt1_15m": 8.0, "wt2_15m": -4.0,
           "wt1_4h": 6.0, "wt2_4h": -3.0, "wt1_5m": 9.0, "wt2_5m": -2.0,
           "wt1_D": 4.0, "wt2_D": -1.0}
    assert T.htf_against_fires(get, ind, False)[0] is True


def test_htf_d_confirm_skipped_when_no_data():
    get = make_get({"HTF_AGAINST_FORCE_CLOSE_ENABLED": True})
    ind = _htf_ind()
    del ind["wt1_D"]
    del ind["wt2_D"]
    assert T.htf_against_fires(get, ind, True)[0] is True


# ---------- NEWBORN ----------

def _nlk_ind(**kw):
    ind = {"wt_velocity_5m": -1.5, "wt1_1h": -5.0, "wt2_1h": 3.0,
           "wt1_15m": -4.0, "wt2_15m": 2.0, "wt1_4h": -3.0, "wt2_4h": 1.0}
    ind.update(kw)
    return ind


def test_nlk_disabled_inert():
    assert T.newborn_kill_fires(make_get(), _nlk_ind(), True, 5.0, -1.0, False)[0] is False


def test_nlk_hedge_excluded():
    get = make_get({"NEWBORN_LOSS_KILL_ENABLED": True})
    assert T.newborn_kill_fires(get, _nlk_ind(), True, 5.0, -1.0, True)[0] is False


def test_nlk_fires():
    get = make_get({"NEWBORN_LOSS_KILL_ENABLED": True})
    fire, reason = T.newborn_kill_fires(get, _nlk_ind(), True, 5.0, -0.2, False)
    assert fire is True and "NEWBORN_LOSS_KILL" in reason


def test_nlk_age_boundary():
    get = make_get({"NEWBORN_LOSS_KILL_ENABLED": True})
    assert T.newborn_kill_fires(get, _nlk_ind(), True, 30.0, -0.2, False)[0] is True
    assert T.newborn_kill_fires(get, _nlk_ind(), True, 30.01, -0.2, False)[0] is False
    assert T.newborn_kill_fires(get, _nlk_ind(), True, -1.0, -0.2, False)[0] is False


def test_nlk_gain_above_threshold_safe():
    get = make_get({"NEWBORN_LOSS_KILL_ENABLED": True})
    assert T.newborn_kill_fires(get, _nlk_ind(), True, 5.0, 0.01, False)[0] is False


def test_nlk_vel_with_blocks():
    get = make_get({"NEWBORN_LOSS_KILL_ENABLED": True})
    assert T.newborn_kill_fires(get, _nlk_ind(wt_velocity_5m=1.5), True, 5.0, -0.2, False)[0] is False


def test_nlk_htf_veto_blocks():
    get = make_get({"NEWBORN_LOSS_KILL_ENABLED": True})
    assert T.newborn_kill_fires(get, _nlk_ind(wt1_1h=9.0, wt2_1h=1.0), True, 5.0, -0.2, False)[0] is False


# ---------- WT_PERCENTILE ----------

def test_wtp_disabled_inert():
    ind = {"wt_percentile_D": 99.0, "wt_percentile_4h": 99.0, "wt1_15m": -5.0, "wt2_15m": 5.0}
    assert T.wt_percentile_fires(make_get(), ind, True)[0] is False


def test_wtp_long_fires():
    get = make_get({"WT_PERCENTILE_EXIT_ENABLED": True})
    ind = {"wt_percentile_D": 80.0, "wt_percentile_4h": 60.0, "wt1_15m": -5.0, "wt2_15m": 5.0}
    fire, reason = T.wt_percentile_fires(get, ind, True)
    assert fire is True and "WT_PERCENTILE" in reason


def test_wtp_boundary_strict():
    get = make_get({"WT_PERCENTILE_EXIT_ENABLED": True})
    ind = {"wt_percentile_D": 75.0, "wt_percentile_4h": 60.0, "wt1_15m": -5.0, "wt2_15m": 5.0}
    assert T.wt_percentile_fires(get, ind, True)[0] is False


def test_wtp_short_fires():
    get = make_get({"WT_PERCENTILE_EXIT_ENABLED": True})
    ind = {"wt_percentile_D": 5.0, "wt_percentile_4h": 20.0, "wt1_15m": 5.0, "wt2_15m": -5.0}
    assert T.wt_percentile_fires(get, ind, False)[0] is True


def test_wtp_15m_turn_required():
    get = make_get({"WT_PERCENTILE_EXIT_ENABLED": True})
    ind = {"wt_percentile_D": 80.0, "wt_percentile_4h": 60.0, "wt1_15m": 5.0, "wt2_15m": -5.0}
    assert T.wt_percentile_fires(get, ind, True)[0] is False


# ---------- CYCLE_TP ----------

def test_ctp_disabled_inert():
    assert T.cycle_tp_tier(make_get(), 5.0, set()) is None


def test_ctp_below_gate_none():
    get = make_get({"CYCLE_TP_TIERED_ENABLED": True})
    assert T.cycle_tp_tier(get, 0.04, set()) is None


def test_ctp_first_tier():
    get = make_get({"CYCLE_TP_TIERED_ENABLED": True})
    hit = T.cycle_tp_tier(get, 0.20, set())
    assert hit == (0, 0.0015, 0.25)


def test_ctp_skips_fired():
    get = make_get({"CYCLE_TP_TIERED_ENABLED": True})
    assert T.cycle_tp_tier(get, 0.60, {0, 1}) == (2, 0.005, 0.25)
    assert T.cycle_tp_tier(get, 0.40, {0, 1}) is None


# ---------- PPL ----------

def test_ppl_disabled_inert():
    out = T.ppl_step(make_get(), 5.0, 105.0, 100.0, 10.0, 1.0, {}, True)
    assert out["action"] == "none"


def test_ppl_account_gate():
    get = make_get({"PARTIAL_PROFIT_LOCK_ENABLED": True})
    out = T.ppl_step(get, 5.0, 105.0, 100.0, 10.0, 1.0, {}, False)
    assert out["action"] == "none"


def test_ppl_phase1_reduce():
    get = make_get({"PARTIAL_PROFIT_LOCK_ENABLED": True})
    out = T.ppl_step(get, 2.0, 102.0, 100.0, 10.0, 1.0, {}, True)
    assert out["action"] == "reduce" and out["qty"] == pytest.approx(5.0)
    assert out["arm"]["first_exit_price"] == pytest.approx(102.0)


def test_ppl_phase1_whole_share_block():
    get = make_get({"PARTIAL_PROFIT_LOCK_ENABLED": True})
    out = T.ppl_step(get, 2.0, 102.0, 100.0, 1.5, 1.0, {}, True)
    assert out["action"] == "none"


def test_ppl_phase2_upgrade_at_arm():
    get = make_get({"PARTIAL_PROFIT_LOCK_ENABLED": True})
    st = {"fired": True, "first_exit_price": 102.0, "stop_level": 100.1, "stop_upgraded": False}
    out = T.ppl_step(get, 1.80, 103.0, 100.0, 5.0, 1.0, st, True)
    assert out["action"] == "upgrade" and out["state"]["stop_level"] == pytest.approx(102.0)
    out2 = T.ppl_step(get, 1.74, 103.0, 100.0, 5.0, 1.0, st, True)
    assert out2["action"] == "check_stop"


def test_ppl_be_stop_and_hit():
    assert T.ppl_be_stop(True, 100.0, 0.1) == pytest.approx(100.1)
    assert T.ppl_be_stop(False, 100.0, 0.1) == pytest.approx(99.9)
    assert T.ppl_stop_hit(True, 100.05, 100.1) is True
    assert T.ppl_stop_hit(True, 100.15, 100.1) is False
    assert T.ppl_stop_hit(False, 99.95, 99.9) is True
    assert T.ppl_stop_hit(False, 99.85, 99.9) is False


# ---------- SATOSHIT ----------

def _sat_ind_long():
    return {"k_5m": 75.0, "k_5m_prev": 85.0, "d_5m": 78.0, "k_1m": 50.0,
            "k_1m_prev": 50.0, "d_1m": 50.0, "mfi_5m": 60.0, "mfi_5m_prev": 65.0}


def test_sat_disabled_inert():
    assert T.satoshit_fires(make_get(), _sat_ind_long(), True, 1.0, True)[0] is False


def test_sat_account_gate():
    get = make_get({"SATOSHIT_EXIT_ENABLED": True})
    assert T.satoshit_fires(get, _sat_ind_long(), True, 1.0, False)[0] is False


def test_sat_long_fires():
    get = make_get({"SATOSHIT_EXIT_ENABLED": True})
    fire, reason, frac = T.satoshit_fires(get, _sat_ind_long(), True, 1.0, True)
    assert fire is True and frac == pytest.approx(0.7) and "SATOSHIT" in reason


def test_sat_mfi_prev_missing_honest_inert():
    get = make_get({"SATOSHIT_EXIT_ENABLED": True})
    ind = _sat_ind_long()
    del ind["mfi_5m_prev"]
    assert T.satoshit_fires(get, ind, True, 1.0, True)[0] is False


def test_sat_agrees_with_live_cross_logic():
    import ez_satoshit as S
    live_ind = {"stoch_k_3m": 75.0, "stoch_k_3m_prev": 85.0, "stoch_d_3m": 78.0,
                "stoch_k_1m": 50.0, "stoch_k_1m_prev": 50.0, "stoch_d_1m": 50.0,
                "mfi_3m": 60.0, "mfi_3m_prev": 65.0}
    live_fire, _ = S.satoshit_exit_check(live_ind, True, object())
    get = make_get({"SATOSHIT_EXIT_ENABLED": True})
    twin_fire, _, _ = T.satoshit_fires(get, _sat_ind_long(), True, 1.0, True)
    assert live_fire is True and twin_fire is True
    live_ind["mfi_3m"] = 70.0
    live_fire2, _ = S.satoshit_exit_check(live_ind, True, object())
    ind2 = _sat_ind_long()
    ind2["mfi_5m"] = 70.0
    twin_fire2, _, _ = T.satoshit_fires(get, ind2, True, 1.0, True)
    assert live_fire2 is False and twin_fire2 is False


# ---------- PYRAMID ----------

def test_pyr_disabled_inert():
    ind = {"wt_velocity_1h": 9.0, "dc_position_15m": 0.95}
    assert T.pyramid_fires(make_get(), ind, True, 9.0)[0] is False


def test_pyr_gain_gate():
    get = make_get({"PYRAMID_ENABLED": True})
    ind = {"wt_velocity_1h": 9.0, "dc_position_15m": 0.95}
    assert T.pyramid_fires(get, ind, True, 1.49)[0] is False
    assert T.pyramid_fires(get, ind, True, 1.5)[0] is True


def test_pyr_long_fires():
    get = make_get({"PYRAMID_ENABLED": True})
    fire, reason, mult = T.pyramid_fires(get, {"wt_velocity_1h": 3.0, "dc_position_15m": 0.8}, True, 2.0)
    assert fire is True and mult == pytest.approx(0.5) and "PYRAMID_LONG" in reason


def test_pyr_agrees_with_shared_predicate():
    import strategy_enhancements as SE
    get = make_get({"PYRAMID_ENABLED": True})
    cases = [(2.0, 3.0, 0.8, True, True), (2.0, 1.0, 0.8, True, False),
             (2.0, 3.0, 0.5, True, False), (1.0, 3.0, 0.8, True, False),
             (2.0, -3.0, 0.2, False, True), (2.0, -3.0, 0.5, False, False),
             (2.0, 3.0, 0.2, False, False)]
    for gain, vel, dc, is_long, want in cases:
        ref = SE._pyramid_fires(gain, vel, dc, is_long, 1.5, 2.0, 0.7, 0.3)
        fire, _, _ = T.pyramid_fires(get, {"wt_velocity_1h": vel, "dc_position_15m": dc}, is_long, gain)
        assert ref is want and fire is want


# ---------- WATCHDOG ----------

def test_wd_disabled_inert():
    ind = {"sma_200_15m": 100.0, "wt1_5m": 9.0, "wt2_5m": -9.0, "dc_high_1h": 50.0}
    assert T.watchdog_open(make_get(), ind, True, 105.0) is None


def test_wd_no_price_inert():
    get = make_get({"MOMENTUM_SMA_WATCHDOG_ENABLED": True})
    assert T.watchdog_open(get, {"sma_200_15m": 100.0}, True, 0.0) is None


def test_wd_dc_breakout_largest_wins():
    get = make_get({"MOMENTUM_SMA_WATCHDOG_ENABLED": True})
    ind = {"dc_high_15m": 90.0, "dc_high_1h": 95.0, "dc_high_4h": 500.0, "dc_high_D": 600.0,
           "sma_200_15m": 0.0}
    out = T.watchdog_open(get, ind, True, 100.0)
    assert out["trigger"] == "DC_1h_BREAKOUT" and out["usd"] == pytest.approx(100.0)
    ind["dc_high_4h"] = 99.0
    out = T.watchdog_open(get, ind, True, 100.0)
    assert out["trigger"] == "DC_4h_BREAKOUT" and out["usd"] == pytest.approx(200.0)


def test_wd_dc_cap():
    get = make_get({"MOMENTUM_SMA_WATCHDOG_ENABLED": True, "WATCHDOG_DC_MAX_USD": 100.0})
    ind = {"dc_high_D": 90.0, "sma_200_15m": 0.0}
    out = T.watchdog_open(get, ind, True, 100.0)
    assert out["trigger"] == "DC_D_BREAKOUT" and out["usd"] == pytest.approx(100.0)


def test_wd_req1_sma_cross():
    get = make_get({"MOMENTUM_SMA_WATCHDOG_ENABLED": True})
    ind = {"sma_200_15m": 100.0, "wt1_5m": 5.0, "wt2_5m": -5.0}
    out = T.watchdog_open(get, ind, True, 101.01)
    assert out["trigger"] == "SMA15M_WT5M" and out["usd"] == pytest.approx(25.0)
    assert T.watchdog_open(get, ind, True, 100.99) is None
    ind2 = {"sma_200_15m": 100.0, "wt1_5m": -5.0, "wt2_5m": 5.0}
    out2 = T.watchdog_open(get, ind2, False, 98.99)
    assert out2["trigger"] == "SMA15M_WT5M"


def test_wd_req1_agrees_with_shared_predicate():
    import vec_decisions.momentum_watchdog as W
    get = make_get({"MOMENTUM_SMA_WATCHDOG_ENABLED": True})
    for is_long, px, sma, w1, w2, want in [(True, 102.0, 100.0, 5.0, -5.0, True),
                                           (True, 100.5, 100.0, 5.0, -5.0, False),
                                           (True, 102.0, 100.0, -5.0, 5.0, False),
                                           (False, 98.0, 100.0, -5.0, 5.0, True),
                                           (False, 99.5, 100.0, -5.0, 5.0, False)]:
        ref = W._momentum_watchdog_fires(px, sma, w1, w2, 0.0, 0.0, 0.0, 0.0,
                                         0.0, 0.0, 0.0, 0.0, 0.01, is_long, False)
        ind = {"sma_200_15m": sma, "wt1_5m": w1, "wt2_5m": w2}
        out = T.watchdog_open(get, ind, is_long, px)
        assert ref is want and (out is not None) is want
        if want:
            assert out["trigger"] == "SMA15M_WT5M"


def test_twin_fail_open_on_garbage():
    bad = lambda k, d: (_ for _ in ()).throw(RuntimeError("cfg down"))
    assert T.fixed_quantity_active(bad) is False
    assert T.atr_adaptive_size_mult(bad, None, float("nan")) == 1.0
    assert T.ema_dist_size_mult(bad, None, float("nan")) == 1.0
    assert T.e1_wt_delta_fires(bad, None, True) == (False, "")
    assert T.htf_against_fires(bad, None, True) == (False, "")
    assert T.newborn_kill_fires(bad, None, True, float("nan"), float("nan"), False) == (False, "")
    assert T.wt_percentile_fires(bad, None, True) == (False, "")
    assert T.cycle_tp_tier(bad, float("nan"), None) is None
    assert T.ppl_step(bad, 0.0, 0.0, 0.0, 0.0, 1.0, None, True)["action"] == "none"
    assert T.satoshit_fires(bad, None, True, 0.0, True) == (False, "", 0.0)
    assert T.pyramid_fires(bad, None, True, 0.0) == (False, "", 0.0)
    assert T.watchdog_open(bad, None, True, 100.0) is None
