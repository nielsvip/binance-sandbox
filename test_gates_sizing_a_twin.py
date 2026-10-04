"""Twin parity for GLOBAL_RISK_GATES + entry-gate batch A (19 switches).

Proves vec_decisions.twin_gates_sizing_a reproduces the v12 vector formulas
and tradier-live research-gate semantics exactly:
  inert-when-disabled + exact-threshold boundaries + randomized formula
  agreement vs independent oracles written from the cited source lines.
Self-contained: v12_quick_engine is not importable in this worktree
(missing vec_decisions siblings), so oracles re-state the source math.
"""
import random

import vec_decisions.twin_gates_sizing_a as T


def _get(d):
    return lambda k, default: d.get(k, default)


def test_disabled_is_inert():
    g = _get({})
    ind = {"rsi2_1h": 1, "connors_rsi_D": 1, "sma_200_1h": 100, "wt_velocity_4h": -9,
           "wt_velocity_1h": 0.1, "wt_velocity_1h_prev": 9, "wt_velocity": -9,
           "mfi_1h": 99, "k_3m": 1, "squeeze_on_15m": 0, "squeeze_on_15m_prev": 1,
           "rsi_1h": 99, "relative_volume_1h": 9, "adx_1h": 99, "dc_position_15m": 0.0,
           "wt1_3m": 99, "wt2_3m": -99}
    assert T.connors_fires(_get({"CONNORS_RSI_ENABLED": False}), ind, True) == (False, "")
    assert T.clenow_blocks(_get({"CLENOW_ENABLED": False}), ind, True, 50.0) == (False, "")
    assert T.confluence_blocks(_get({"CONFLUENCE_MODE_ENABLED": False}), ind, True, "3m") == (False, "")
    assert T.vel_exit_fires(_get({"VEL_EXIT_ENABLED": False}), ind, True) == (False, "")
    assert T.wt_vel_decay_fires(_get({"WT_VEL_DECAY_EXIT_ENABLED": False}), ind, True, "3m") == (False, "")
    assert T.rz_bottom_fires(_get({"RZ_ENTRY_ENABLED": False}), ind, True, "3m") == (False, "")
    assert T.tradier_mfi_long_blocks(_get({"TRADIER_MFI_ENTRY_LONG_ENABLED": False}), ind, True, True) == (False, "")
    assert T.tradier_mfi_long_blocks(_get({"TRADIER_MFI_ENTRY_LONG_ENABLED": True}), ind, True, False) == (False, "")
    assert T.tradier_mfi_long_blocks(_get({"TRADIER_MFI_ENTRY_LONG_ENABLED": True}), ind, False, True) == (False, "")
    assert T.tradier_mi_fires(g, ind, True, "5m") == (False, "")
    assert T.tradier_rsi_short_fires(g, ind, True, True) == (False, "")
    assert T.tradier_rsi_short_fires(g, ind, False, False) == (False, "")
    assert T.regime_adaptive_mult(g, 99.0) is None
    assert T.mtf_atr_trail_on(g) is False
    assert T.sba_veto_blocks(_get({"SBA_BOUNCE_ENABLED": False}), ind, True) == (False, "")
    assert T.kg_entry_allowed(g, ind, True) is None
    assert T.vel_exit_gate_blocks(True) is False
    assert T.vel_exit_gate_blocks(False) is True


def test_rsi2_connors_boundaries():
    assert T.rsi2_fires(_get({"RSI2_ENABLED": True}), {"rsi2_1h": 2.99}, True)[0] is True
    assert T.rsi2_fires(_get({"RSI2_ENABLED": True}), {"rsi2_1h": 3.0}, True)[0] is False
    assert T.rsi2_fires(_get({"RSI2_ENABLED": True}), {"rsi2_1h": 97.01}, False)[0] is True
    assert T.rsi2_fires(_get({"RSI2_ENABLED": True}), {"rsi2_1h": 97.0}, False)[0] is False
    assert T.rsi2_fires(_get({"RSI2_ENABLED": True}), {"rsi_1h": 2.0}, True)[0] is True
    assert T.connors_fires(_get({"CONNORS_RSI_ENABLED": True}), {"connors_rsi_D": 9.99}, True)[0] is True
    assert T.connors_fires(_get({"CONNORS_RSI_ENABLED": True}), {"connors_rsi_D": 10.0}, True)[0] is False
    assert T.connors_fires(_get({"CONNORS_RSI_ENABLED": True}), {"connors_rsi_D": 90.01}, False)[0] is True
    assert T.connors_fires(_get({"CONNORS_RSI_ENABLED": True}), {"connors_rsi_D": 90.0}, False)[0] is False


def test_clenow_boundaries():
    g = _get({"CLENOW_ENABLED": True})
    assert T.clenow_blocks(g, {"sma_200_1h": 100.0}, True, 100.0)[0] is True
    assert T.clenow_blocks(g, {"sma_200_1h": 100.0}, True, 100.01)[0] is False
    assert T.clenow_blocks(g, {"sma_200_1h": 100.0}, False, 100.0)[0] is True
    assert T.clenow_blocks(g, {"sma_200_1h": 100.0}, False, 99.99)[0] is False
    assert T.clenow_blocks(g, {"sma_200_1h": 0.0}, True, 50.0)[0] is False
    gg = _get({"CLENOW_ENABLED": True, "CLENOW_GATE_ENABLED": True})
    assert T.clenow_blocks(gg, {"sma_200_1h": 100.0}, True, 102.99)[0] is True
    assert T.clenow_blocks(gg, {"sma_200_1h": 100.0}, True, 103.01)[0] is False


def test_confluence_votes():
    g = _get({"CONFLUENCE_MODE_ENABLED": True})
    full = {"wt1_3m": 5, "wt2_3m": -5, "k_3m": 20, "mfi_1h": 50}
    assert T.confluence_blocks(g, full, True, "3m") == (False, "")
    two = {"wt1_3m": 5, "wt2_3m": -5, "k_3m": 20, "mfi_1h": 70}
    assert T.confluence_blocks(g, two, True, "3m") == (False, "")
    one = {"wt1_3m": 5, "wt2_3m": -5, "k_3m": 60, "mfi_1h": 70}
    assert T.confluence_blocks(g, one, True, "3m")[0] is True
    st = {"wt1_5m": -5, "wt2_5m": 5, "stoch_k_5m": 80, "mfi_1h": 50}
    assert T.confluence_blocks(g, st, False, "5m") == (False, "")


def test_vel_and_decay_boundaries():
    g = _get({})
    assert T.vel_exit_fires(g, {"wt_velocity_4h": -2.01}, True)[0] is True
    assert T.vel_exit_fires(g, {"wt_velocity_4h": -2.0}, True)[0] is False
    assert T.vel_exit_fires(g, {"wt_velocity_4h": 2.01}, False)[0] is True
    assert T.vel_exit_fires(g, {"wt_velocity_4h": 2.0}, False)[0] is False
    assert T.vel_exit_fires(_get({"VEC_VEL_EXIT_AS_TRIGGER": False}), {"wt_velocity_4h": -9}, True) == (False, "")
    gd = _get({"WT_VEL_DECAY_EXIT_ENABLED": True})
    fire_ind = {"wt_velocity_1h": 0.9, "wt_velocity_1h_prev": 2.1, "wt_velocity": 1.0}
    assert T.wt_vel_decay_fires(gd, fire_ind, True, "3m")[0] is True
    edge = {"wt_velocity_1h": 0.9, "wt_velocity_1h_prev": 2.0, "wt_velocity": 1.0}
    assert T.wt_vel_decay_fires(gd, edge, True, "3m")[0] is False
    no_prev = {"wt_velocity_1h": 0.1, "wt_velocity": -5}
    assert T.wt_vel_decay_fires(gd, no_prev, True, "3m")[0] is False
    s_ind = {"wt_velocity_1h": -0.9, "wt_velocity_1h_prev": -2.1, "wt_velocity": -1.0}
    assert T.wt_vel_decay_fires(gd, s_ind, False, "3m")[0] is True


def test_fh_boundaries():
    assert T.fh_enabled(_get({"FH_MOMENTUM_ENABLED": True, "TRADIER_FH_MOMENTUM_ENABLED": False}), False) is True
    assert T.fh_enabled(_get({"FH_MOMENTUM_ENABLED": True, "TRADIER_FH_MOMENTUM_ENABLED": False}), True) is False
    g = _get({})
    ind = {"dc_position_15m": 0.2, "mfi_1h": 60}
    assert T.fh_momentum_fires(g, ind, True, 810, 100.6, 100.0)[0] is True
    assert T.fh_momentum_fires(g, ind, True, 809, 100.6, 100.0)[0] is False
    assert T.fh_momentum_fires(g, ind, True, 870, 100.6, 100.0)[0] is True
    assert T.fh_momentum_fires(g, ind, True, 871, 100.6, 100.0)[0] is False
    assert T.fh_momentum_fires(g, ind, True, 840, 100.49, 100.0)[0] is False
    assert T.fh_momentum_fires(g, {"dc_position_15m": 0.34, "mfi_1h": 60}, True, 840, 100.6, 100.0)[0] is False
    assert T.fh_momentum_fires(g, {"dc_position_15m": 0.2, "mfi_1h": 54}, True, 840, 100.6, 100.0)[0] is False
    sind = {"dc_position_15m": 0.8, "mfi_1h": 40}
    assert T.fh_momentum_fires(g, sind, False, 840, 99.4, 100.0)[0] is True


def test_rz_squeeze_mfi_mi_rsi():
    g = _get({})
    assert T.rz_bottom_fires(g, {"k_3m": 9.9, "mfi_1h": 49.9}, True, "3m")[0] is True
    assert T.rz_bottom_fires(g, {"k_3m": 10.0, "mfi_1h": 49.9}, True, "3m")[0] is False
    assert T.rz_bottom_fires(g, {"k_3m": 9.9, "mfi_1h": 50.0}, True, "3m")[0] is False
    assert T.rz_bottom_fires(g, {"k_5m": 90.1, "mfi_1h": 50.1}, False, "5m")[0] is True
    assert T.rz_bottom_fires(g, {"k_5m": 90.0, "mfi_1h": 50.1}, False, "5m")[0] is False
    assert T.squeeze_fires({"squeeze_on_15m": 0, "k_3m": 49}, True, "3m", prev_sq=1)[0] is True
    assert T.squeeze_fires({"squeeze_on_15m": 0, "k_3m": 50}, True, "3m", prev_sq=1)[0] is False
    assert T.squeeze_fires({"squeeze_on_15m": 0, "k_3m": 49}, True, "3m", prev_sq=0)[0] is False
    assert T.squeeze_fires({"squeeze_on_15m": 1, "k_3m": 49}, True, "3m", prev_sq=1)[0] is False
    assert T.squeeze_fires({"squeeze_on": 0, "k_5m": 51}, False, "5m", prev_sq=True)[0] is True
    gm = _get({"TRADIER_MFI_ENTRY_LONG_ENABLED": True})
    assert T.tradier_mfi_long_blocks(gm, {"mfi_1h": 60.0}, True, True)[0] is True
    assert T.tradier_mfi_long_blocks(gm, {"mfi_1h": 59.9}, True, True)[0] is False
    gi = _get({"TRADIER_MI_ENTRY_ENABLED_TRADIER": True})
    mi3 = {"mfi_1h": 31, "mfi_1h_prev": 29, "k_5m": 51, "k_5m_prev": 50, "rsi_1h": 31}
    assert T.tradier_mi_fires(gi, mi3, True, "5m")[0] is True
    mi2 = {"mfi_1h": 31, "mfi_1h_prev": 29, "k_5m": 49, "k_5m_prev": 50, "rsi_1h": 31}
    assert T.tradier_mi_fires(gi, mi2, True, "5m")[0] is False
    mis = {"mfi_1h": 69, "mfi_1h_prev": 71, "k_5m": 49, "k_5m_prev": 50, "rsi_1h": 69}
    assert T.tradier_mi_fires(gi, mis, False, "5m")[0] is True
    gr = _get({})
    assert T.tradier_rsi_short_fires(gr, {"rsi_1h": 70.1, "relative_volume_1h": 2.4}, False, True)[0] is True
    assert T.tradier_rsi_short_fires(gr, {"rsi_1h": 70.0, "relative_volume_1h": 2.4}, False, True)[0] is False
    assert T.tradier_rsi_short_fires(gr, {"rsi_1h": 70.1, "relative_volume_1h": 2.39}, False, True)[0] is False
    assert T.tradier_rsi_short_fires(_get({"TRADIER_RSI_ENTRY_SHORT_TRADIER": 101}), {"rsi_1h": 99, "relative_volume_1h": 9}, False, True) == (False, "")


def test_regime_and_mtf():
    g = _get({"REGIME_ADAPTIVE_ENABLED": True})
    assert T.regime_adaptive_mult(g, 30.0) == 1.5
    assert T.regime_adaptive_mult(g, 29.99) == 0.5
    assert T.regime_adaptive_mult(g, 15.0) == 0.5
    assert T.regime_adaptive_mult(g, 14.99) == 0.5
    assert T.regime_adaptive_mult(g, 20.0) == 0.5
    assert T.regime_adaptive_mult(_get({"REGIME_ADAPTIVE_ENABLED": True, "REGIME_TRENDING_POSITION_SIZE_MULT": 2.0}), 99.0) == 2.0
    full = _get({"MTF_EXIT_USE_COMPOUND": True, "MTF_ATR_TRAIL_ENABLED": True, "MTF_ATR_TRAIL_ENABLED_TRADIER": True})
    assert T.mtf_atr_trail_on(full) is True
    assert T.mtf_atr_trail_on(_get({"MTF_EXIT_USE_COMPOUND": True, "MTF_ATR_TRAIL_ENABLED": True})) is False
    assert T.mtf_atr_trail_on(_get({"MTF_EXIT_USE_COMPOUND": True, "MTF_ATR_TRAIL_ENABLED_TRADIER": True})) is False
    assert T.mtf_atr_trail_on(_get({"MTF_ATR_TRAIL_ENABLED": True, "MTF_ATR_TRAIL_ENABLED_TRADIER": True})) is False


def test_sba_hand_computed():
    g = _get({})
    base = {"adx_1h": 20, "bb_width_4h": 8, "bb_width_1h": 6, "dc_width_4h": 10,
            "k_15m": 50, "ha_1h": "green", "ha_4h": "green", "mfi_4h": 50, "mfi_1h": 55}
    score, why = T.sba_bounce_score(g, base, True)
    assert abs(score - 3.0) < 1e-9, (score, why)
    assert T.sba_veto_blocks(_get({}), base, True)[0] is True
    strong = dict(base, wt_signal_15m="BUY", wt_signal_1h="BUY")
    score2, _ = T.sba_bounce_score(g, strong, True)
    assert abs(score2 - 4.5) < 1e-9, score2
    assert T.sba_veto_blocks(_get({}), strong, True) == (False, "")
    assert T.sba_bounce_score(g, dict(base, adx_1h=30), True) == (0.0, "SBA_DEAL_ADX")
    assert T.sba_bounce_score(g, dict(base, bb_width_1h=15), True) == (0.0, "SBA_DEAL_BB1H")
    assert T.sba_bounce_score(g, dict(base, k_15m=80), True) == (0.0, "SBA_DEAL_OVERBOUGHT")
    assert T.sba_bounce_score(g, dict(base, k_15m=20), False)[1] == "SBA_DEAL_OVERSOLD"
    assert T.sba_bounce_score(g, dict(base, ha_1h="red", ha_4h="red"), True)[1] == "SBA_DEAL_HTF_DOWN"
    assert T.sba_bounce_score(g, dict(base, mfi_4h=20, mfi_1h=25), True)[1] == "SBA_DEAL_MFI_DRY"
    assert T.sba_veto_blocks(_get({"SBA_MIN_SCORE": 3.0}), base, True) == (False, "")


def test_kg_modes():
    g_on = _get({"EMA_9_21_FILTER_ENABLED": True, "KINDERGARTEN_CUMULATIVE_MODE": True,
                 "EMA_9_21_FILTER_TFS": "1h,D", "KINDERGARTEN_CUMULATIVE_MIN_TFS": 2})
    ind = {"ema_9_above_21_1h": 1, "ema_9_above_21_D": 1}
    assert T.kg_entry_allowed(g_on, ind, True) is True
    assert T.kg_entry_allowed(g_on, ind, False) is False
    one = {"ema_9_above_21_1h": 1, "ema_9_above_21_D": 0}
    assert T.kg_entry_allowed(g_on, one, True) is False
    g_leg = _get({"EMA_9_21_FILTER_ENABLED": True, "KINDERGARTEN_CUMULATIVE_MODE": False})
    assert T.kg_entry_allowed(g_leg, {"ema_9_above_21_1h": 0}, True) is False
    assert T.kg_entry_allowed(g_leg, {"ema_9_above_21_1h": 1}, True) is True
    assert T.kg_entry_allowed(g_leg, {"ema_9_above_21_1h": 1}, False) is False
    assert T.kg_entry_allowed(g_leg, {"ema_9_above_21_1h": 0}, False) is True
    assert T.kg_entry_allowed(g_leg, {}, True) is True
    g_strict = _get({"KINDERGARTEN_EMA_GATE_ENABLED": True, "EMA_9_21_FILTER_TFS": "1h,D",
                     "KINDERGARTEN_STRICT_TFS": "D", "KINDERGARTEN_CUMULATIVE_MIN_TFS": 1})
    assert T.kg_entry_allowed(g_strict, one, True) is False
    assert T.kg_entry_allowed(_get({"KINDERGARTEN_EMA_GATE_ENABLED": True}), {}, True) is True


def _oracle_fh(d, ind, is_long, now_min, px, do):
    move = (px - do) / do * 100.0 if do > 0 else 0.0
    mins = now_min - 810.0
    ok = (0 <= mins <= d.get("TRADIER_FH_MOMENTUM_WINDOW_MINUTES", 60))
    ok = ok and ((move >= d.get("TRADIER_FH_MOMENTUM_MIN_MOVE_PCT", 0.5)) if is_long else (move <= -d.get("TRADIER_FH_MOMENTUM_MIN_MOVE_PCT", 0.5)))
    if d.get("TRADIER_FH_MOMENTUM_DC_CONFIRM", True):
        mx = d.get("TRADIER_FH_MOMENTUM_DC_MAX_LONG", 0.33)
        dc = ind.get("dc_position_15m", 0.5)
        ok = ok and ((dc <= mx) if is_long else (dc >= 1 - mx))
    if d.get("TRADIER_FH_MOMENTUM_MFI_CONFIRM", True):
        mm = d.get("TRADIER_FH_MOMENTUM_MFI_MIN", 55.0)
        mfi = ind.get("mfi_1h", 50)
        ok = ok and ((mfi >= mm) if is_long else (mfi <= 100 - mm))
    return ok


def test_formula_agreement_grid():
    random.seed(11)
    for _ in range(400):
        is_long = random.random() < 0.5
        ind = {"rsi2_1h": random.uniform(0, 100), "connors_rsi_D": random.uniform(0, 100),
               "sma_200_1h": random.uniform(0, 200), "wt_velocity_4h": random.uniform(-9, 9),
               "wt_velocity_1h": random.uniform(-9, 9), "wt_velocity_1h_prev": random.uniform(-9, 9),
               "wt_velocity": random.uniform(-9, 9), "mfi_1h": random.uniform(0, 100),
               "mfi_1h_prev": random.uniform(0, 100), "k_3m": random.uniform(0, 100),
               "k_5m": random.uniform(0, 100), "k_5m_prev": random.uniform(0, 100),
               "rsi_1h": random.uniform(0, 100), "relative_volume_1h": random.uniform(0, 5),
               "adx_1h": random.uniform(0, 60), "dc_position_15m": random.random(),
               "wt1_3m": random.uniform(-99, 99), "wt2_3m": random.uniform(-99, 99),
               "squeeze_on_15m": random.randint(0, 1)}
        d = {"RSI2_ENABLED": True, "CONNORS_RSI_ENABLED": True, "CLENOW_ENABLED": True,
             "CONFLUENCE_MODE_ENABLED": True, "WT_VEL_DECAY_EXIT_ENABLED": True,
             "RZ_ENTRY_ENABLED": True, "TRADIER_MFI_ENTRY_LONG_ENABLED": True,
             "TRADIER_MI_ENTRY_ENABLED_TRADIER": True, "REGIME_ADAPTIVE_ENABLED": True}
        g = _get(d)
        px = random.uniform(50, 150)
        thr3 = 3.0
        assert T.rsi2_fires(g, ind, is_long)[0] == ((ind["rsi2_1h"] < thr3) if is_long else (ind["rsi2_1h"] > 100 - thr3))
        assert T.connors_fires(g, ind, is_long)[0] == ((ind["connors_rsi_D"] < 10.0) if is_long else (ind["connors_rsi_D"] > 90.0))
        sma = ind["sma_200_1h"]
        exp_cl = (sma > 0) and ((px <= sma) if is_long else (px >= sma))
        assert T.clenow_blocks(g, ind, is_long, px)[0] == exp_cl
        agree = (1 if ((ind["wt1_3m"] > ind["wt2_3m"]) if is_long else (ind["wt1_3m"] < ind["wt2_3m"])) else 0)
        agree += 1 if ((ind["k_3m"] < 40) if is_long else (ind["k_3m"] > 60)) else 0
        agree += 1 if ((ind["mfi_1h"] < 60) if is_long else (ind["mfi_1h"] > 40)) else 0
        assert T.confluence_blocks(g, ind, is_long, "3m")[0] == (agree < 2)
        v4 = ind["wt_velocity_4h"]
        assert T.vel_exit_fires(g, ind, is_long)[0] == ((v4 < -2.0) if is_long else (v4 > 2.0))
        v1, vp, vb = ind["wt_velocity_1h"], ind["wt_velocity_1h_prev"], ind["wt_velocity"]
        exp_dc = ((vp > 2.0) and (v1 < 1.0) and (vb < vp * 0.5)) if is_long else ((vp < -2.0) and (v1 > -1.0) and (vb > vp * 0.5))
        assert T.wt_vel_decay_fires(g, ind, is_long, "3m")[0] == exp_dc
        k5 = ind["k_5m"]
        exp_rz = ((k5 < 10.0) and (ind["mfi_1h"] < 50.0)) if is_long else ((k5 > 90.0) and (ind["mfi_1h"] > 50.0))
        assert T.rz_bottom_fires(g, ind, is_long, "5m")[0] == exp_rz
        assert T.tradier_mfi_long_blocks(g, ind, is_long, True)[0] == (is_long and ind["mfi_1h"] >= 60.0)
        mfi, mfip, kp = ind["mfi_1h"], ind["mfi_1h_prev"], ind["k_5m_prev"]
        if is_long:
            votes = int(mfip < 30 and mfi > mfip) + int(k5 > kp) + int(ind["rsi_1h"] > 30)
        else:
            votes = int(mfip > 70 and mfi < mfip) + int(k5 < kp) + int(ind["rsi_1h"] < 70)
        assert T.tradier_mi_fires(g, ind, is_long, "5m")[0] == (votes >= 3)
        assert T.tradier_rsi_short_fires(g, ind, is_long, True)[0] == ((not is_long) and ind["rsi_1h"] > 70.0 and ind["relative_volume_1h"] >= 2.4)
        adx = ind["adx_1h"]
        assert T.regime_adaptive_mult(g, adx) == (1.5 if adx >= 30.0 else 0.5)
        now_min = random.uniform(700, 950)
        do = random.uniform(50, 150)
        assert T.fh_momentum_fires(g, ind, is_long, now_min, px, do)[0] == _oracle_fh(d, ind, is_long, now_min, px, do)


def test_sba_score_agreement_spot():
    random.seed(5)
    g = _get({})
    for _ in range(60):
        ind = {"adx_1h": random.uniform(0, 24), "bb_width_4h": random.uniform(0, 15),
               "bb_width_1h": random.uniform(0, 13), "dc_width_4h": random.uniform(0, 17),
               "k_15m": random.uniform(30, 70), "mfi_4h": 50, "mfi_1h": 50}
        ind.update({"bb_width_1h": random.uniform(0, 8), "dc_width_1h": random.uniform(0, 6),
                    "adx_4h": random.uniform(0, 24), "sma_200_1h": 100.0, "current_price": 101.0,
                    "relative_volume_1h": 0.9, "relative_volume_15m": 1.0,
                    "mfi_15m": 50, "rsi_15m": 50, "rsi_1h": 52, "rsi_4h": 48,
                    "k_3m": 50, "k_1h": 50, "rsi_2_1h": 50})
        q_exp = 0.0
        q_exp += 1.5 if (ind["bb_width_1h"] < 6.5 and ind["bb_width_4h"] < 10.0) else (0.5 if (ind["bb_width_1h"] < 9.0 and ind["bb_width_4h"] < 13.0) else 0.0)
        q_exp += 0.5 if (ind["dc_width_1h"] < 7.0 and ind["dc_width_4h"] < 12.0) else 0.0
        q_exp += 1.0 if (ind["adx_1h"] < 20 and ind["adx_4h"] < 25) else (0.5 if ind["adx_1h"] < 25 else 0.0)
        q_exp += 1.0
        q_exp += 1.0
        q_exp += 0.5
        score, _ = T.sba_bounce_score(g, ind, True)
        assert abs(score - q_exp) < 1e-9, (score, q_exp, ind)
