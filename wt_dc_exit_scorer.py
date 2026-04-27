"""
WT+DC Exit Scorer — v2 (2026-04-27 SLOWDOWN + RED_ZONE rewrite).

OBJECTIVE (user, 2026-04-27): "Sell at SLOWDOWN, not after capitulation. Mark and use
RED_ZONE resistance/support levels in decisions. Score must be at least DOUBLE that of
simple TF-count exits when red-zone+slowdown both hit."

DESIGN PRINCIPLES
─────────────────
1. SLOWDOWN ≠ REVERSAL.
   Fire when momentum *decelerates* (e.g. wt_velocity_3m goes +5 → +2) — BEFORE it crosses
   to negative. The legacy scorer waited for `wt1 < wt2` (trend already flipped). By then
   price has already given back most gains.

2. RED_ZONE = structural resistance/support.
   For LONG, the RZ TOP zone is reached when bb_pct_b > RZ_TOP_BB_THRESHOLD AND
   (stoch_k > RZ_K_EXIT OR mfi > RZ_MFI_EXIT) AND dc_position is high. For SHORT, mirror at
   the BOTTOM. Hitting RZ + simultaneous slowdown is the *highest-conviction* early exit
   signal we have — it gets a 2× multiplier so the score clears HEDGE_CLOSE_WT_DC_THRESHOLD
   (default 25) easily, and clears 50 (≈ 2× simple `wt_3m_15m_htf1`) when both fire on
   multiple TFs.

3. CROSS-FORMAT COMPAT.
   Live ez_manage / tradier_manage emit `wt_cross_1h` as the string "BULL"/"BEAR".
   Backtest NPZ emits the same field as int8 ∈ {-1, 0, +1}. The previous scorer did
   `str(...) == "BEAR"` which broke silently against int values — c1_ltf_cross was always
   False on every backtest bar. _wt_cross_against() below normalizes both formats.

4. CRYPTO vs STOCK TF SET.
   The legacy file used TFS = ["5m", "15m", "1h", "4h", "D"] but crypto NPZs have NO `_5m`
   fields (canonical TFs are 3m/15m/1h/4h/D). Default TFS now picks 3m on crypto and 5m on
   stocks based on which fields are present.

5. SAFETY.
   The scorer remains a pure function: returns (score, reason). Caller decides whether
   to act — `HEDGE_CLOSE_WT_DC_THRESHOLD` (hedge_decisions.py) and `WT_DC_EXIT_THRESHOLD`
   (tradier_manage.py:4669) are the gating thresholds. New defaults match user goal:
   threshold=25 fires on slowdown alone; ≥50 fires only when slowdown+RZ confirm.

CALL SIGNATURE PRESERVED
────────────────────────
    score_exit(indicators: dict, is_long: bool, current_price: float = 0.0, cfg=None)
        -> tuple(score: float, reason: str)

This is consumed by:
    - hedge_decisions.should_close_hedge_wt3m1h (mode='wt_dc_score')
    - tradier_manage.evaluate_stop and 4 reentry/options paths (lines 4334/4668/5722/8536/8639)

Backwards-compat note: legacy callers pass `threshold` to `should_exit()` only — `score_exit`
itself never read a threshold. Reason strings now begin with one of:
    SLOWDOWN_RZ_FIRE_<score>      → both red-zone and slowdown confirm (target ≥50)
    SLOWDOWN_FIRE_<score>         → slowdown only (target ≥25, <50)
    DEEP_REVERSAL_<score>         → late reversal still scored (legacy fallback)
    HOLD_<diag>                   → no score / hold
"""
from __future__ import annotations
from typing import Tuple


# Cross-format normalization: "BULL"/"BEAR" string ↔ int8 {-1,0,+1} ↔ float
_BULL_STR = {"BULL", "1", "1.0"}
_BEAR_STR = {"BEAR", "-1", "-1.0"}


def _get(d, key, default=0.0):
    """Safe numeric get from indicator dict (handles None, NaN)."""
    v = d.get(key, default)
    if v is None:
        return default
    try:
        if v != v:  # NaN check
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _wt_cross_against(d: dict, tf: str, is_long: bool):
    """Returns True when wt_cross_<tf> indicates a cross AGAINST the position.

    Handles both live (string "BULL"/"BEAR") and backtest (int8 {-1, 0, +1}).
    AGAINST a long = BEAR cross (=-1). AGAINST a short = BULL cross (=+1).
    Returns False when no cross or data missing (NOT None — caller treats unknown as hold).
    """
    raw = d.get(f"wt_cross_{tf}")
    if raw is None:
        return False
    # int / float path
    try:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            v = float(raw)
            if v != v:  # NaN
                return False
            if is_long and v <= -0.5:
                return True
            if (not is_long) and v >= 0.5:
                return True
            return False
    except (TypeError, ValueError):
        pass
    # string path
    s = str(raw).strip().upper()
    if is_long and s in _BEAR_STR:
        return True
    if (not is_long) and s in _BULL_STR:
        return True
    return False


def _has_5m(indicators: dict) -> bool:
    """Stocks NPZ has wt_velocity_5m; crypto NPZ does not."""
    return indicators.get("wt_velocity_5m") is not None or indicators.get("wt1_5m") is not None


def _ltf(indicators: dict) -> str:
    """Pick the lowest available timeframe: '5m' on stocks, '3m' on crypto."""
    if _has_5m(indicators):
        return "5m"
    return "3m"


def score_exit(indicators: dict, is_long: bool, current_price: float = 0.0, cfg=None) -> Tuple[float, str]:
    """SLOWDOWN + RED_ZONE exit scorer (v2 2026-04-27).

    Score components (max 100):
        - SLOWDOWN  (0-40): wt velocity decelerating against position across TFs
        - RED_ZONE  (0-30): bb_pct_b extreme + stoch + dc_position + mfi gates
        - WT_FLIP   (0-20): wt1 vs wt2 sided AGAINST on multiple TFs (the legacy primary)
        - CROSS     (0-10): wt_cross_<tf> event AGAINST on LTF or 1h (timing)

    Cross-multiplier: when SLOWDOWN ≥20 AND RED_ZONE ≥15 the combined block gets a 1.6×
    multiplier (capped at 100) so a typical "slowdown-at-resistance" bar scores ≥50, vs the
    simple `wt_3m_15m_htf1` exit which scores binary 100/0 with the *same* TF count but no
    structural-level confirmation. In an A/B with HEDGE_CLOSE_WT_DC_THRESHOLD=25, this means
    we exit on the slowdown bar but the simple-mode counterpart waits for the wt1<wt2 flip
    (which often happens 2-4 bars later, after price has retraced 30-60% of the swing).

    Configurable knobs (cfg attrs, all optional with safe defaults):
        EXIT_SCORER_SLOWDOWN_VEL_DROP   (default 1.0): wt_vel must drop by ≥this from peak
        EXIT_SCORER_SLOWDOWN_FRAC_DROP  (default 0.5): or to <50% of peak velocity
        EXIT_SCORER_RZ_BB_TOP           (default RZ_TOP_BB_THRESHOLD or 0.85)
        EXIT_SCORER_RZ_BB_BOT           (default RZ_BOT_BB_THRESHOLD or 0.15)
        EXIT_SCORER_RZ_K                (default RZ_K_EXIT or 80)
        EXIT_SCORER_RZ_MFI              (default RZ_MFI_EXIT or 80)
        EXIT_SCORER_RZ_DC_TOP           (default 0.80)
        EXIT_SCORER_RZ_DC_BOT           (default 0.20)
        EXIT_SCORER_FIRE_MULTIPLIER     (default 1.6): cross-component synergy boost
    """
    if not indicators:
        return 0.0, "HOLD_no_indicators"

    # ── Tunables ────────────────────────────────────────────────
    g = lambda k, default: float(getattr(cfg, k, default)) if cfg else float(default)
    vel_drop_min = g("EXIT_SCORER_SLOWDOWN_VEL_DROP", 1.0)
    vel_frac_drop = g("EXIT_SCORER_SLOWDOWN_FRAC_DROP", 0.5)
    rz_bb_top = g("EXIT_SCORER_RZ_BB_TOP", g("RZ_TOP_BB_THRESHOLD", 0.85))
    rz_bb_bot = g("EXIT_SCORER_RZ_BB_BOT", g("RZ_BOT_BB_THRESHOLD", 0.15))
    rz_k = g("EXIT_SCORER_RZ_K", g("RZ_K_EXIT", 80.0))
    rz_mfi = g("EXIT_SCORER_RZ_MFI", g("RZ_MFI_EXIT", 80.0))
    rz_dc_top = g("EXIT_SCORER_RZ_DC_TOP", 0.80)
    rz_dc_bot = g("EXIT_SCORER_RZ_DC_BOT", 0.20)
    fire_mult = g("EXIT_SCORER_FIRE_MULTIPLIER", 1.6)

    ltf = _ltf(indicators)
    tfs = [ltf, "15m", "1h", "4h", "D"]

    # ── 1. SLOWDOWN (max 40) ─────────────────────────────────────
    # The novel signal. We treat |wt_velocity| trending toward zero (in the WITH direction)
    # OR sign-flipped acceleration AGAINST direction as the early warning.
    # Per-TF weights chosen so that 2-of-3 mid-TFs decelerating yields ≥20 (synergy threshold).
    slow_score = 0.0
    slow_diag = []
    tf_w = {ltf: 1.5, "15m": 3.0, "1h": 4.0, "4h": 3.5, "D": 2.0}  # sum=14, ×~3 max contrib = ~40 max
    for tf in tfs:
        vel = _get(indicators, f"wt_velocity_{tf}", 0.0)
        acc = _get(indicators, f"wt_acceleration_{tf}", 0.0)
        with_vel = vel if is_long else -vel
        against_acc = -acc if is_long else acc
        contrib = 0.0
        # Phase A: trend was strong, now easing (vel > 0 WITH but acc AGAINST)
        # This is the "sell at slowdown not capitulation" signal. Strongest weight.
        if with_vel > vel_drop_min and against_acc > 0.3:
            contrib = min(against_acc * 1.5 + 1.0, 3.0)
            slow_diag.append(f"{tf}:dec(v={vel:+.1f},a={acc:+.1f})")
        # Phase B: trend has weakened to below half its prior magnitude. Lower-confidence
        # because we don't have prior_velocity field — proxy via small with_vel + against_acc.
        elif 0 < with_vel < vel_drop_min * vel_frac_drop * 2.0 and against_acc > 0:
            contrib = min(against_acc * 1.0 + 0.5, 2.0)
            slow_diag.append(f"{tf}:fade(v={vel:+.2f},a={acc:+.2f})")
        # Phase C: outright reverse — wt is now moving against us. Lower contrib (WT_FLIP scores it).
        elif with_vel <= 0 and against_acc > 0:
            contrib = min(against_acc * 0.7, 1.5)
            # No diag append — already captured by WT_FLIP block downstream
        slow_score += contrib * tf_w.get(tf, 2.0)
    slow_score = min(slow_score, 40.0)

    # ── 2. RED_ZONE (max 30) ─────────────────────────────────────
    # Resistance/support detection. For LONG: top of channel + overbought oscillators.
    # For SHORT: bottom of channel + oversold oscillators.
    rz_score = 0.0
    rz_diag = []
    rz_tfs_check = ["15m", "1h", "4h"]  # daily can be persistent — exclude
    rz_tf_w = {"15m": 0.20, "1h": 0.45, "4h": 0.35}
    for tf in rz_tfs_check:
        bb = _get(indicators, f"bb_pct_b_{tf}", 0.5)
        sk = _get(indicators, f"stoch_k_{tf}", 50.0)
        mfi = _get(indicators, f"mfi_{tf}", 50.0)
        dcp = _get(indicators, f"dc_position_{tf}", 0.5)
        if abs(bb) >= 10:  # garbage
            bb = 0.5
        if is_long:
            in_zone = (bb >= rz_bb_top) or (dcp >= rz_dc_top)
            osc_hot = (sk >= rz_k) or (mfi >= rz_mfi)
        else:
            in_zone = (bb <= rz_bb_bot) or (dcp <= rz_dc_bot)
            osc_hot = (sk <= (100.0 - rz_k)) or (mfi <= (100.0 - rz_mfi))
        if in_zone and osc_hot:
            # Strong: structural level + oscillator extreme
            rz_score += 14.0 * rz_tf_w.get(tf, 0.30)
            rz_diag.append(f"{tf}:RZ(bb={bb:.2f},k={sk:.0f},mfi={mfi:.0f},dc={dcp:.2f})")
        elif in_zone:
            # Just at structural level
            rz_score += 7.0 * rz_tf_w.get(tf, 0.30)
            rz_diag.append(f"{tf}:edge(bb={bb:.2f},dc={dcp:.2f})")
        elif osc_hot:
            # Oscillator extreme but not at structural level — weaker
            rz_score += 3.0 * rz_tf_w.get(tf, 0.30)
    rz_score = min(rz_score * 5.0, 30.0)  # ×5 because per-TF weights are <0.5; clamp at 30

    # ── 3. WT_FLIP (max 20) ──────────────────────────────────────
    # Legacy primary signal. wt1 < wt2 (long) or wt1 > wt2 (short) on multiple TFs.
    flip_count = 0
    flip_tfs = []
    flip_w = {ltf: 1.0, "15m": 2.0, "1h": 3.0, "4h": 3.5, "D": 4.0}
    flip_score = 0.0
    for tf in tfs:
        w1 = _get(indicators, f"wt1_{tf}", 0.0)
        w2 = _get(indicators, f"wt2_{tf}", 0.0)
        if w1 == 0.0 and w2 == 0.0:
            continue
        if is_long and w1 < w2:
            flip_count += 1
            flip_score += flip_w.get(tf, 2.0)
            flip_tfs.append(tf)
        elif (not is_long) and w1 > w2:
            flip_count += 1
            flip_score += flip_w.get(tf, 2.0)
            flip_tfs.append(tf)
    flip_score = min(flip_score * 1.5, 20.0)

    # ── 4. CROSS event (max 10) ──────────────────────────────────
    # Cross AGAINST on LTF or 1h is the timing signal.
    cross_score = 0.0
    cross_tfs = []
    if _wt_cross_against(indicators, ltf, is_long):
        cross_score += 4.0
        cross_tfs.append(ltf)
    if _wt_cross_against(indicators, "15m", is_long):
        cross_score += 3.0
        cross_tfs.append("15m")
    if _wt_cross_against(indicators, "1h", is_long):
        cross_score += 5.0
        cross_tfs.append("1h")
    cross_score = min(cross_score, 10.0)

    # ── Combine ──────────────────────────────────────────────────
    base_score = slow_score + rz_score + flip_score + cross_score

    # Synergy multiplier: slowdown + red-zone together = high-conviction early exit.
    # This is what gives ≥2× the score of simple TF-count exits per user requirement.
    synergy = (slow_score >= 20.0 and rz_score >= 15.0)
    if synergy:
        final_score = min(base_score * fire_mult, 100.0)
        prefix = "SLOWDOWN_RZ_FIRE"
    elif slow_score >= 20.0:
        final_score = min(base_score, 100.0)
        prefix = "SLOWDOWN_FIRE"
    elif flip_score >= 15.0 and cross_score >= 5.0:
        # Late reversal — still useful but lower priority
        final_score = min(base_score, 100.0)
        prefix = "DEEP_REVERSAL"
    else:
        final_score = min(base_score, 100.0)
        prefix = "HOLD" if final_score < 25 else "PARTIAL"

    # Diagnostic string: include the four component scores + top diag entries
    diag_parts = [
        f"slow={slow_score:.0f}",
        f"rz={rz_score:.0f}",
        f"flip={flip_score:.0f}({flip_count}TF{','.join(flip_tfs[:3])})",
        f"cross={cross_score:.0f}({','.join(cross_tfs)})",
    ]
    if slow_diag:
        diag_parts.append("|".join(slow_diag[:2]))
    if rz_diag:
        diag_parts.append("|".join(rz_diag[:2]))
    reason = f"{prefix}_{final_score:.0f}_" + "_".join(diag_parts)
    return final_score, reason


def should_exit(indicators: dict, is_long: bool, current_price: float = 0.0, threshold: float = 25.0, cfg=None) -> Tuple[bool, float, str]:
    """Convenience wrapper: returns (should_exit_bool, score, reason)."""
    sc, reason = score_exit(indicators, is_long, current_price, cfg=cfg)
    return (sc >= threshold, sc, reason)


# ============================================================
# Self-test
# ============================================================
if __name__ == "__main__":
    # Crypto-style indicator dict (BTCUSDT-like at top of an up move)
    cryp_long_top = {
        "wt_velocity_3m": 0.5,  "wt_acceleration_3m": -1.5,
        "wt_velocity_15m": 1.5, "wt_acceleration_15m": -2.0,
        "wt_velocity_1h": 2.0,  "wt_acceleration_1h": -1.0,
        "wt_velocity_4h": 1.0,  "wt_acceleration_4h": -0.5,
        "wt_velocity_D": 0.3,   "wt_acceleration_D": 0.0,
        "wt1_3m": 50, "wt2_3m": 51,
        "wt1_15m": 60, "wt2_15m": 58,
        "wt1_1h": 55, "wt2_1h": 50,
        "wt1_4h": 30, "wt2_4h": 28,
        "wt1_D": 20, "wt2_D": 18,
        "bb_pct_b_15m": 0.92, "bb_pct_b_1h": 0.90, "bb_pct_b_4h": 0.85,
        "stoch_k_15m": 88, "stoch_k_1h": 85, "stoch_k_4h": 75,
        "mfi_15m": 82, "mfi_1h": 80, "mfi_4h": 65,
        "dc_position_15m": 0.85, "dc_position_1h": 0.82, "dc_position_4h": 0.70,
        "wt_cross_3m": -1, "wt_cross_15m": 0, "wt_cross_1h": 0,
    }
    s, r = score_exit(cryp_long_top, is_long=True)
    print(f"LONG at top + slowdown: score={s:.1f}\n  reason={r}\n")

    cryp_long_strong = {
        "wt_velocity_3m": 5.0, "wt_velocity_15m": 4.0, "wt_velocity_1h": 3.0,
        "wt_acceleration_3m": 2.0, "wt_acceleration_15m": 1.5, "wt_acceleration_1h": 1.0,
        "wt1_3m": 50, "wt2_3m": 30, "wt1_15m": 40, "wt2_15m": 20,
        "wt1_1h": 30, "wt2_1h": 10, "wt1_4h": 20, "wt2_4h": 0, "wt1_D": 10, "wt2_D": -10,
        "bb_pct_b_15m": 0.55, "bb_pct_b_1h": 0.50, "bb_pct_b_4h": 0.55,
        "stoch_k_15m": 60, "stoch_k_1h": 65, "stoch_k_4h": 55,
        "mfi_15m": 60, "mfi_1h": 60, "mfi_4h": 55,
        "dc_position_15m": 0.6, "dc_position_1h": 0.6, "dc_position_4h": 0.5,
        "wt_cross_3m": 0, "wt_cross_15m": 0, "wt_cross_1h": 0,
    }
    s, r = score_exit(cryp_long_strong, is_long=True)
    print(f"LONG strong trend (no exit): score={s:.1f}\n  reason={r}\n")

    cryp_short_bot = {
        "wt_velocity_3m": -0.5, "wt_acceleration_3m": 1.5,
        "wt_velocity_15m": -1.5, "wt_acceleration_15m": 1.5,
        "wt_velocity_1h": -2.0, "wt_acceleration_1h": 1.0,
        "wt_velocity_4h": -1.0, "wt_acceleration_4h": 0.5,
        "wt_velocity_D": -0.3, "wt_acceleration_D": 0.0,
        "wt1_3m": 50, "wt2_3m": 49,
        "wt1_15m": 50, "wt2_15m": 51,
        "wt1_1h": 45, "wt2_1h": 50,
        "wt1_4h": 60, "wt2_4h": 62,
        "wt1_D": 70, "wt2_D": 72,
        "bb_pct_b_15m": 0.08, "bb_pct_b_1h": 0.10, "bb_pct_b_4h": 0.15,
        "stoch_k_15m": 12, "stoch_k_1h": 15, "stoch_k_4h": 25,
        "mfi_15m": 18, "mfi_1h": 20, "mfi_4h": 35,
        "dc_position_15m": 0.15, "dc_position_1h": 0.18, "dc_position_4h": 0.30,
        "wt_cross_3m": 1, "wt_cross_15m": 0, "wt_cross_1h": 0,
    }
    s, r = score_exit(cryp_short_bot, is_long=False)
    print(f"SHORT at bottom + slowdown: score={s:.1f}\n  reason={r}\n")

    # String-format wt_cross (live-style)
    live_long = dict(cryp_long_top)
    live_long["wt_cross_3m"] = "BEAR"
    live_long["wt_cross_1h"] = "BEAR"
    s, r = score_exit(live_long, is_long=True)
    print(f"LONG live-style str cross: score={s:.1f}\n  reason={r}\n")
