"""Crypto reentry skip fix — ensure moderate K (55/45) does NOT block reentry via extreme WT2 gate.

Before fix: REENTRY_STOCH_K_MAX_LONG=40 pushed k=55 into EXTREME requiring wt3&wt15.
After fix: 80 threshold keeps k=55 in SAFE requiring only 1 WT. Same for SHORT.
Covers guaranteed_price_cross confirmation + position_evaluator B14/B16 loosening.
"""
import config
from vec_decisions.guaranteed_price_cross_reentry import reentry_confirmation_gate, check_guaranteed_price_cross_reentry
from position_evaluator import evaluate_reentry_core


def _base_ind(k15=55.0, wt1_3m=10.0, wt2_3m=-10.0, wt1_15m=-5.0, wt2_15m=5.0, k15_prev=50.0, k1h=55.0):
    # Use realistic WT values away from 0 so fallback `or 50` doesn't mask them (0 is treated as missing -> 50)
    return {
        "wt1_3m": wt1_3m, "wt2_3m": wt2_3m,
        "wt1_15m": wt1_15m, "wt2_15m": wt2_15m,
        "wt1_1h": 10.0, "wt2_1h": -10.0,
        "stoch_k_15m": k15, "stoch_k_15m_prev": k15_prev,
        "stoch_k_1h": k1h,
        "wt1_5m": wt1_3m, "wt2_5m": wt2_3m,
        "ha_4h": "neutral", "dc_basis_4h": 0, "basis_4h": 0,
        "high_3m": 101, "high_3m_prev": 100, "low_3m": 99, "low_3m_prev": 98,
    }


def test_config_thresholds_relaxed():
    assert config.Config.REENTRY_STOCH_K_MAX_LONG == 80.0, "LONG threshold must be 80 (not 40)"
    assert config.Config.REENTRY_STOCH_K_MIN_SHORT == 20.0, "SHORT threshold must be 20 (not 60)"
    assert config.Config.RECENT_REDUCTION_GUARD_WINDOW_S == 300.0
    assert config.Config.EZ_REENTRY_PRICE_CROSS_PCT == 0.001
    assert config.Config.REENTRY_BAR_TURN_ENABLED is True


def test_moderate_k_long_single_wt_passes():
    # LONG with k15m=55 (moderate) — SAFE path, single WT (3m bullish) should pass even if 15m against
    ind = _base_ind(k15=55.0, wt1_3m=10.0, wt2_3m=-10.0, wt1_15m=-5.0, wt2_15m=5.0, k15_prev=52.0)
    ok, reason = reentry_confirmation_gate(ind, is_long=True, cfg=config.Config, current_price=100.0, exit_price=99.0)
    # With old 40 threshold this would be EXTREME and FAIL (needs wt3&wt15). Now SAFE → single WT passes
    assert ok is True, f"moderate LONG should pass with single WT, got {reason}"
    # Also direct guaranteed cross: price must cross +0.10% (99*1.001=99.099). Use 99.15 to cross.
    ind2 = _base_ind(k15=55.0, wt1_3m=10.0, wt2_3m=-10.0, wt1_15m=-5.0, wt2_15m=5.0, k15_prev=52.0)
    ok2, _ = check_guaranteed_price_cross_reentry(config.Config, ind2, current_price=99.15, exit_price=99.0, is_long=True, elapsed_s=400)
    # 99.15 is 0.15% above exit — above 0.10% cross, WT gate decides. Should fire via single WT.
    assert ok2 is True, "guaranteed cross should fire on moderate k with single WT"


def test_moderate_k_short_single_wt_passes():
    ind = _base_ind(k15=45.0, wt1_3m=-10.0, wt2_3m=10.0, wt1_15m=5.0, wt2_15m=-5.0, k15_prev=48.0)
    # For SHORT, wt1_3m<wt2_3m is the favorable direction; set wt bearish
    ind["wt1_1h"] = -10.0; ind["wt2_1h"] = 10.0
    ok, reason = reentry_confirmation_gate(ind, is_long=False, cfg=config.Config, current_price=99.0, exit_price=100.0)
    assert ok is True, f"moderate SHORT should pass with single WT, got {reason}"


def test_extreme_k_still_requires_two_wt():
    # Truly overbought LONG k15m=85 should still be EXTREME requiring both wt3&wt15
    ind_single = _base_ind(k15=85.0, wt1_3m=10.0, wt2_3m=-10.0, wt1_15m=-5.0, wt2_15m=5.0, k15_prev=80.0, k1h=60.0)
    ind_single["high_3m"] = 0; ind_single["high_3m_prev"] = 0; ind_single["low_3m"] = 0; ind_single["low_3m_prev"] = 0
    ok_single_nobar, _ = reentry_confirmation_gate(ind_single, is_long=True, cfg=config.Config, current_price=99.05, exit_price=99.0)
    # With only wt3m true, extreme should fail without bar_turn
    assert ok_single_nobar is False, "extreme k should require 2 WT without bar rescue"
    ind_both = _base_ind(k15=85.0, wt1_3m=10.0, wt2_3m=-10.0, wt1_15m=10.0, wt2_15m=-10.0, k15_prev=80.0, k1h=60.0)
    # k bounce: k>kp 85>80 true, both WT true => should pass even without bar
    ind_both["high_3m"] = 0; ind_both["high_3m_prev"] = 0; ind_both["low_3m"] = 0; ind_both["low_3m_prev"] = 0
    ok_both, _ = reentry_confirmation_gate(ind_both, is_long=True, cfg=config.Config, current_price=99.05, exit_price=99.0)
    assert ok_both is True


def test_b14_ha_trend_looser_k():
    # B14 LONG now allows k3m<70 (was 60). Test k=65 with 3 HA green should fire.
    i = {"ha_3m": "green", "ha_15m": "green", "ha_1h": "green", "stoch_k_3m": 65, "stoch_k_15m": 30, "stoch_k_1h": 50,
         "wt1_3m": 0, "wt2_3m": 0, "wt1_15m": 0, "wt2_15m": 0, "wt_velocity_3m": 0, "wt_velocity_15m": 0, "wt_velocity_1h": 0,
         "dc_high_4h": 0, "dc_low_4h": 0, "dc_high_1h": 0, "dc_low_1h": 0, "dc_high_15m": 0, "dc_low_15m": 0}
    sig = evaluate_reentry_core(i, is_long=True, current_price=100.0, config=config.Config, re_qty_base=0.01)
    assert sig is not None and "B14" in sig.reason, f"B14 should fire at k=65, got {sig}"
