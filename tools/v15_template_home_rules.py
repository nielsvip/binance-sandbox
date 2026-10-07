"""Home-tab rules for tools/v15_template_restructure_v2.py (AGENT B, 2026-09-30).
One switch (col A name) lives in exactly ONE tab of a template. MANUAL_HOME is the evidence-based placement of every switch that the
old templates broadcast into several tabs (74 universal gates were copied into all 12 tabs); every entry has a one-line reason
(code consumer in ez_manage/tradier_manage/v12_quick_engine/vec_decisions, BIBLE §28 tab meaning).
"""
RB, BC, CG = "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES"
ES, EV = "EXIT_STRUCTURAL", "EXIT_VELOCITY"
RW, RA = "REENTRY_WINDOWED", "REENTRY_ADAPTIVE"
AT, AR = "AUGMENT_TREND", "AUGMENT_RISK_SIZING"
RP, RS = "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER"
GR = "GLOBAL_RISK_GATES"
TABS = [RB, BC, CG, ES, EV, RW, RA, AT, AR, RP, RS, GR]
LIFE = {RB: "ENTRY", BC: "ENTRY", CG: "ENTRY", ES: "EXIT", EV: "EXIT", RW: "REENTRY", RA: "REENTRY", AT: "AUGMENT", AR: "AUGMENT", RP: "REDUCE", RS: "REDUCE", GR: "GLOBAL"}

# name -> (tab, reason)
MANUAL_HOME = {
    "ADX_RANGING_THRESHOLD": (CG, "ez check_entry_alignment: entry ADX gate"),
    "AUGMENT_BULL_KILL_ENABLED": (AT, "v12 compute_augment_signals"),
    "BANDAID_OFF_LOSER_RECOVER_PCT": (GR, "ez process_position loser-recover band-aid; purpose unclear -> UNCLEAR"),
    "BAND_ARROW_ENABLED": (CG, "tradier band_arrow_score used in entry scoring"),
    "BB_BOUNCE_ENTRY_TF": (RA, "v12 compute_reentry_blocks (bounce re-entry TF)"),
    "BB_EXIT_AT_LOSS_TF": (ES, "v12 compute_exit_signals: BB structural exit at loss"),
    "BB_PROFIT_TAKE_TF": (ES, "v12 compute_exit_signals: BB structural profit take"),
    "BB_PULLBACK_GATE_FILTER_TF": (CG, "ez check_entry_alignment/_parity_filter_tf_gates + tradier/v12 entry gate"),
    "BTC_ACCEL_RAMP_REQUIRE_POSITIVE": (GR, "no code consumer; BTC ramp gate is portfolio-level"),
    "BTC_HARD_BLOCK_OTHER_ACCOUNTS": (GR, "no code consumer; portfolio-level"),
    "BTC_ROUND_BANDS_EACH_SIDE": (GR, "no code consumer; portfolio-level"),
    "BULL_HOLD_EXIT_DELAY_BARS": (ES, "v12 compute_exit_signals hold delay"),
    "COOLDOWN_LOCKS_FILTER_TF": (GR, "no code consumer; cooldown lock = global"),
    "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": (EV, "ez crypto_spike_fade_loop (spike-fade exit)"),
    "DC_MOMENT_STRONG_THRESHOLD": (BC, "no code consumer; DC momentum (breakout tab)"),
    "DELTA_HTF_GATE": (CG, "ez execute_trade_action HTF gate"),
    "DELTA_REENTRY_FILTER_ENABLED": (RA, "ez check_reentry_delta_tolerant"),
    "DELTA_REENTRY_Z_THRESHOLD": (RA, "ez check_reentry_delta_tolerant"),
    "DUP_GUARD_FILTER_TF": (GR, "no code consumer; duplicate-order guard = global"),
    "EMA_9_21_FILTER_ENABLED": (CG, "tradier should_enter_*, v12 compute_entry_signals"),
    "EMA_9_21_FILTER_MIN_TFS": (CG, "v12 compute_entry_signals"),
    "EMA_BLANKET_FILTER_ENABLED": (CG, "ez execute_now / tradier queue_trade_action entry gate"),
    "EMA_BLANKET_FILTER_FILTER_TF": (CG, "EMA blanket entry gate TF"),
    "EMA_BLANKET_FILTER_MIN_TFS": (CG, "ez execute_now / tradier queue_trade_action entry gate"),
    "EXECUTE_NOW_SINGLE_GATE_ENFORCE": (GR, "execute_now single gate = global"),
    "EZ_MANAGE_THROTTLER_RATE": (GR, "order throttler = global"),
    "FG_FEAR_THRESHOLD": (AR, "ez calculate_final_order_quantity (sizing)"),
    "FG_GREED_THRESHOLD": (AR, "ez calculate_final_order_quantity (sizing)"),
    "FUNDING_GATE_FILTER_TF": (CG, "funding gate = entry gate"),
    "FUNDING_GATE_LONG_MAX": (CG, "funding gate = entry gate (long side)"),
    "FUNDING_GATE_MTF_REQUIRED": (CG, "funding gate = entry gate"),
    "FUNDING_GATE_SHORT_MIN": (CG, "funding gate = entry gate (short side)"),
    "GOLDEN_RULE_BASE_USD": (GR, "ez _golden_rule_loop (global)"),
    "GR_FILTER_ALL_ENTRIES": (CG, "ez execute_now golden-rule entry filter"),
    "GR_FILTER_VEC_ENABLED": (CG, "golden-rule vec filter (entry)"),
    "GR_FILTER_VEC_MIN_TFS": (CG, "golden-rule vec filter (entry)"),
    "HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN": (RW, "ez/tradier evaluate_reentry + v12 simulate_one"),
    "HARDCODED_RALLY_REENTRY_ENABLED": (RW, "ez/tradier evaluate_reentry + v12 simulate_one"),
    "HARDCODED_RALLY_REENTRY_REQUIRE_WT": (RW, "ez/tradier evaluate_reentry + v12 simulate_one"),
    "HA_WICK_QUALITY_ENABLED": (CG, "no code consumer; HA wick entry quality"),
    "HA_WICK_QUALITY_SCORE": (CG, "no code consumer; HA wick entry quality"),
    "HA_WICK_QUALITY_TF": (CG, "no code consumer; HA wick entry quality"),
    "HLR_SMA_BAND_PCT": (RS, "no code consumer; HLR reduce band"),
    "HLR_TOP_MIN_TFS": (RS, "quick-reduce strong thresholds"),
    "HTF4_CONF": (CG, "ez/tradier _check_htf_confirmation (entry)"),
    "HTF_BULL_ENTRY_FILTER_ENABLED": (CG, "entry filter by name; no code consumer"),
    "HTF_BULL_ENTRY_FILTER_TF": (CG, "entry filter by name; no code consumer"),
    "HTF_DIRECTION_GATE_ENABLED": (CG, "tradier queue_trade_action + htf_direction_gate (entry)"),
    "HTF_GATE_BYPASS_RZ": (CG, "HTF entry gate bypass; no code consumer"),
    "HTF_GATE_D_MANDATORY": (CG, "htf_direction_gate: the ENTRY gate (USER: entry version, exit copy is damage)"),
    "HTF_GATE_MIN_CONFIRMATIONS": (CG, "htf_direction_gate: entry gate"),
    "HTF_TREND_VETO_BYPASS_ENABLED": (CG, "ez execute_trade_action: entry trend-veto bypass (USER: needs ENTRY version)"),
    "HTF_TREND_VETO_BYPASS_REASONS": (CG, "ez execute_trade_action: entry trend-veto bypass reasons (USER: ENTRY version)"),
    "KINDERGARTEN_EMA_GATE_ENABLED": (CG, "ez/tradier kindergarten entry gate"),
    "LEADERBOARD_FILTER": (CG, "tradier should_enter_*: entry leaderboard filter"),
    "LH_HL_FILTER_ENABLED": (CG, "tradier should_enter_*: entry LH/HL filter"),
    "LH_HL_FILTER_MODE": (CG, "tradier should_enter_*: entry LH/HL filter"),
    "LH_HL_FILTER_REQUIRE_BOTH": (CG, "tradier should_enter_*: entry LH/HL filter"),
    "LIVE_VEC_EMERGENCY_BRAKE_ENABLED": (GR, "execute_now emergency brake = global"),
    "LR_BAND_LADDER_STOCH_EXTREME": (RP, "tradier _ordinary_ladder_target (profit ladder)"),
    "LR_BAND_LADDER_TF_BOTTOM": (RP, "tradier band_ladder_mult (profit ladder)"),
    "LR_BAND_LADDER_TF_TOP": (RP, "tradier band_ladder_mult (profit ladder)"),
    "MANDATORY_REENTRY_WT_FILTER_MIN_TFS": (RW, "tradier process_position mandatory re-entry"),
    "MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY": (RW, "tradier process_position mandatory re-entry"),
    "MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP": (RW, "tradier process_position mandatory re-entry"),
    "MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO": (RW, "tradier process_position mandatory re-entry"),
    "MARKET_QUALITY_SCORE_ENABLED": (CG, "no code consumer; market-quality entry gate"),
    "MI_TF_AGREE_MIN": (RS, "vec mi_exit_signal (MI signal rater)"),
    "MOVER_THRESHOLD": (GR, "no code consumer (dead per memory)"),
    "MTF_FILTER_STRONG_BUY_QUICK_BYPASS": (CG, "ez execute_now MTF entry filter bypass"),
    "MTF_GR_MIN_IND": (CG, "ez execute_now MTF golden-rule entry filter"),
    "MTS_BOTTOM_BONUS_THRESHOLD": (CG, "no code consumer; MTS entry gate"),
    "MTS_BOTTOM_STRONG_THRESHOLD": (CG, "no code consumer; MTS entry gate"),
    "MTS_GATE_ENABLED": (CG, "no code consumer; MTS entry gate"),
    "NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT": (ES, "ez process_position newborn loss-kill exit"),
    "NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST": (ES, "ez process_position newborn loss-kill exit"),
    "NEW_POSITION_MAX_LOSS_THRESHOLD": (ES, "ez _check_loss_protection / process_position loss exit"),
    "OI_CONFIRM_ENABLED": (CG, "vec oi_confirm_entry_gate"),
    "OI_CONFIRM_MIN_CHANGE_PCT": (CG, "vec oi_confirm_entry_gate"),
    "OI_CONFIRM_MIN_PRICE_PCT": (CG, "vec oi_confirm_entry_gate"),
    "ULTIMATE_DC_4H_STOP_ENABLED": (ES, "no code consumer; DC 4h stop = structural exit"),
    "WT_15M_BOUNCE_BB_MIN": (RB, "tradier _shared_direct_entry_claim + v12 compute_entry_signals (15m bounce entry)"),
    "WT_DC_DC_POS_THRESHOLD_SHORT": (BC, "tradier process_position + v12 compute_entry_signals: WT_DC entry (breakout tab)"),
    "WT_DC_DC_POS_THRESHOLD_LONG": (BC, "WT_DC entry (breakout tab)"),
    "WT_DC_STOCH_THRESHOLD_LONG": (BC, "tradier process_position + v12 compute_entry_signals: WT_DC entry"),
    "WT_DC_STOCH_THRESHOLD_SHORT": (BC, "tradier process_position + v12 compute_entry_signals: WT_DC entry"),
    "WT_SIMPLE_GUARANTEE_ENABLED": (GR, "v12 entry+exit signals: guarantee flag spans both -> global"),
    # single-tab switches the user named as mis-filed
    "WT_MOMENTUM_EXIT_THRESHOLD": (EV, "EXIT switch filed in ENTRY_REVERSAL_BOUNCE (USER)"),
    "WT_ACCEL_EXIT_ENABLED": (EV, "EXIT switch filed in ENTRY_REVERSAL_BOUNCE"),
    "WT_DIV_EXIT_ENABLED": (EV, "EXIT switch filed in ENTRY_REVERSAL_BOUNCE (USER)"),
    "MANDATORY_REENTRY_WT_FILTER_TF_MODE": (RW, "re-entry switch filed in ENTRY_REVERSAL_BOUNCE"),
    "EMA_9_21_FILTER_FILTER_TF": (CG, "EMA 9/21 entry gate TF filed in ENTRY_REVERSAL_BOUNCE"),
}

import re

_TOK = lambda n: set(t for t in re.split(r"[^A-Z0-9]+", n.upper()) if t)


def side_of(name: str):
    """'LONG' / 'SHORT' when the NAME carries exactly one side token (whole underscore token), else None."""
    t = _TOK(name)
    if "LONG" in t and "SHORT" not in t:
        return "LONG"
    if "SHORT" in t and "LONG" not in t:
        return "SHORT"
    return None


def strong_lifecycle(name: str):
    """Only unambiguous name tokens; None = no strong claim."""
    t = _TOK(name)
    if "REENTRY" in t:
        return "REENTRY"
    if t & {"AUGMENT", "AUGMENTATION", "PYRAMID"}:
        return "AUGMENT"
    if "ENTRY" in t and not (t & {"EXIT", "STOP", "TRAIL"}):
        return None
    if t & {"EXIT", "TRAIL", "STOPLOSS"} or ("STOP" in t and "ENTRY" not in t) or "TAKE" in t and "PROFIT" in t:
        return "EXIT"
    if t & {"REDUCE", "REDUCTION", "BREAKEVEN", "GIVEBACK"} or ("PROFIT" in t and "LOCK" in t) or ("PARTIAL" in t and "PROFIT" in t):
        return "REDUCE"
    return None


def default_tab_for(life: str, name: str) -> str:
    t = _TOK(name)
    if life == "EXIT":
        return EV if t & {"WT", "VELOCITY", "SPIKE", "FADE", "MOMENTUM", "ACCEL", "DIV", "CROSS", "EXHAUST"} else ES
    if life == "REENTRY":
        return RW if t & {"WINDOW", "COOLDOWN", "MANDATORY", "HARDCODED", "WINDOWED"} else RA
    if life == "AUGMENT":
        return AR if t & {"SIZE", "MULT", "SIZING", "USD", "GATE", "GAIN"} else AT
    if life == "REDUCE":
        return RP if t & {"PROFIT", "LOCK", "BREAKEVEN", "PARTIAL", "GIVEBACK"} else RS
    return CG
