"""
WT+DC Exit Scorer — v2 EXPERIMENTAL (2026-04-27 SLOWDOWN + RED_ZONE rewrite).

⚠️ EXPERIMENTAL — DID NOT VALIDATE. NOT IMPORTED ANYWHERE LIVE.

This file is a rewrite attempt saved alongside the original `wt_dc_exit_scorer.py`
for inspection/iteration. The original wt_dc_exit_scorer.py remains the live production
file.

VALIDATION RESULT (2026-04-27, 12-sym crypto inf 2025-10-01 → 2026-04-27 via real
backtest_v8_engine.py):
    Baseline (legacy wt_dc_exit_scorer.py):  sharpe_pt=0.455 closes=187 WR=58.3%
    Comparison winner (wt_3m_15m_htf1):      sharpe_pt=0.544 closes=??  WR=76.4%
    THIS v2 (threshold=25):                  sharpe_pt=0.071 closes=435 WR=44.8%
    THIS v2 (threshold=50):                  sharpe_pt=0.071 closes=588 WR=47.6%

Verdict: scorer fires too aggressively in the V8_SKIP_PROCESS_POSITION + NOLOSS_HOLD
test regime. The slowdown signal closes profitable hedges too early; the synergy
multiplier (1.6×) is too easy to trip; thresholds 25/50 both produce 2-3× the close
rate of the baseline. WR drops from 76→44%.

DESIGN PRINCIPLES (still valid for next iteration)
──────────────────────────────────────────────────
1. SLOWDOWN ≠ REVERSAL. Fire when momentum *decelerates* before it crosses to
   negative. The original scorer used `wt1 < wt2` (already flipped) — too late.

2. RED_ZONE = structural resistance/support. For LONG, RZ TOP = bb_pct_b > 0.85
   AND (stoch_k > 80 OR mfi > 80) AND dc_position high. Mirror for SHORT.

3. CROSS-FORMAT COMPAT. Live emits wt_cross_<tf> as "BULL"/"BEAR" string; backtest
   NPZ emits int8 ∈ {-1, 0, +1}. The original scorer did `str(...) == "BEAR"` which
   silently broke against int values — the 5-of-5 STRICT_EXIT path NEVER fired in
   backtest because c1_ltf_cross was always False. (BUG IDENTIFIED.)

4. CRYPTO TFs = 3m/15m/1h/4h/D, NOT 5m. Original used TFS = ["5m", "15m", "1h", "4h", "D"]
   — crypto NPZs have no _5m fields. Velocity/accel/peak fields all come back zero.
   (BUG IDENTIFIED.)

WHY IT FAILED IN BACKTEST
─────────────────────────
The hedge-close path requires hedge_gain ≥ 0 to fire (HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN
gate in hedge_decisions.py). 99%+ of scorer calls land on losing hedges and get blocked
as NOLOSS_HOLD. The only events that affect Sharpe are the rare profitable-hedge closes,
where:
  - Original scorer's "PARTIAL_EXIT 4/5" effectively required ALL 4 of (4h_against,
    D_against, k_extreme, dc_extreme) — c1 was structurally unreachable. Strict.
  - V2's slowdown trigger fires on ANY 2-3 TFs decelerating + RZ on 1h. Loose.
The looser fire frequency closes profitable hedges sooner → smaller wins per close
→ pool Sharpe collapses despite higher gain_pct.

NEXT ITERATION IDEAS
────────────────────
1. Make slowdown require BOTH velocity decel AND wt-flip on ≥3 TFs (not 2).
2. Disable synergy multiplier entirely; use additive scoring with threshold 60+.
3. Use prior-bar wt_velocity (currently NPZ supplies it but scorer doesn't read) to
   measure ACTUAL deceleration vs current `against_acc > 0` proxy.
4. Test with HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN=False to see scorer impact on losing
   hedges — current test setup masks 99% of the scorer's behavior.
5. Ablate: try a pure RED_ZONE-only scorer (no slowdown), then pure slowdown-only,
   to identify which component is the actual bleeder.
"""
from __future__ import annotations
from typing import Tuple


_BULL_STR = {"BULL", "1", "1.0"}
_BEAR_STR = {"BEAR", "-1", "-1.0"}


def _get(d, key, default=0.0):
    """Safe numeric get from indicator dict (handles None, NaN)."""
    v = d.get(key, default)
    if v is None:
        return default
    try:
        if v != v:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _wt_cross_against(d: dict, tf: str, is_long: bool):
    """Returns True when wt_cross_<tf> indicates a cross AGAINST the position.

    Handles both live ("BULL"/"BEAR") and backtest (int8 {-1,0,+1}) formats.
    The legacy scorer's `str(...) == "BEAR"` broke silently against the int form.
    """
    raw = d.get(f"wt_cross_{tf}")
    if raw is None:
        return False
    try:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            v = float(raw)
            if v != v:
                return False
            if is_long and v <= -0.5:
                return True
            if (not is_long) and v >= 0.5:
                return True
            return False
    except (TypeError, ValueError):
        pass
    s = str(raw).strip().upper()
    if is_long and s in _BEAR_STR:
        return True
    if (not is_long) and s in _BULL_STR:
        return True
    return False


def _has_5m(indicators: dict) -> bool:
    return indicators.get("wt_velocity_5m") is not None or indicators.get("wt1_5m") is not None


def _ltf(indicators: dict) -> str:
    return "5m" if _has_5m(indicators) else "3m"


def score_exit(indicators: dict, is_long: bool, current_price: float = 0.0, cfg=None) -> Tuple[float, str]:
    """SLOWDOWN + RED_ZONE exit scorer (v2 2026-04-27, EXPERIMENTAL — failed validation).

    Score components (max 100):
        SLOWDOWN  (0-40): wt velocity decelerating against position across TFs
        RED_ZONE  (0-30): bb_pct_b extreme + stoch + dc_position + mfi gates
        WT_FLIP   (0-20): wt1 vs wt2 sided AGAINST on multiple TFs
        CROSS     (0-10): wt_cross_<tf> event AGAINST on LTF or 1h

    Synergy multiplier 1.6× when SLOWDOWN ≥20 AND RED_ZONE ≥15 → fires synergy bar
    earlier than simple TF-count modes.
    """
    if not indicators:
        return 0.0, "HOLD_no_indicators"

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

    # 1. SLOWDOWN (max 40)
    slow_score = 0.0
    slow_diag = []
    tf_w = {ltf: 1.5, "15m": 3.0, "1h": 4.0, "4h": 3.5, "D": 2.0}
    for tf in tfs:
        vel = _get(indicators, f"wt_velocity_{tf}", 0.0)
        acc = _get(indicators, f"wt_acceleration_{tf}", 0.0)
        with_vel = vel if is_long else -vel
        against_acc = -acc if is_long else acc
        contrib = 0.0
        if with_vel > vel_drop_min and against_acc > 0.3:
            contrib = min(against_acc * 1.5 + 1.0, 3.0)
            slow_diag.append(f"{tf}:dec(v={vel:+.1f},a={acc:+.1f})")
        elif 0 < with_vel < vel_drop_min * vel_frac_drop * 2.0 and against_acc > 0:
            contrib = min(against_acc * 1.0 + 0.5, 2.0)
            slow_diag.append(f"{tf}:fade(v={vel:+.2f},a={acc:+.2f})")
        elif with_vel <= 0 and against_acc > 0:
            contrib = min(against_acc * 0.7, 1.5)
        slow_score += contrib * tf_w.get(tf, 2.0)
    slow_score = min(slow_score, 40.0)

    # 2. RED_ZONE (max 30)
    rz_score = 0.0
    rz_diag = []
    rz_tfs_check = ["15m", "1h", "4h"]
    rz_tf_w = {"15m": 0.20, "1h": 0.45, "4h": 0.35}
    for tf in rz_tfs_check:
        bb = _get(indicators, f"bb_pct_b_{tf}", 0.5)
        sk = _get(indicators, f"stoch_k_{tf}", 50.0)
        mfi = _get(indicators, f"mfi_{tf}", 50.0)
        dcp = _get(indicators, f"dc_position_{tf}", 0.5)
        if abs(bb) >= 10:
            bb = 0.5
        if is_long:
            in_zone = (bb >= rz_bb_top) or (dcp >= rz_dc_top)
            osc_hot = (sk >= rz_k) or (mfi >= rz_mfi)
        else:
            in_zone = (bb <= rz_bb_bot) or (dcp <= rz_dc_bot)
            osc_hot = (sk <= (100.0 - rz_k)) or (mfi <= (100.0 - rz_mfi))
        if in_zone and osc_hot:
            rz_score += 14.0 * rz_tf_w.get(tf, 0.30)
            rz_diag.append(f"{tf}:RZ(bb={bb:.2f},k={sk:.0f},mfi={mfi:.0f},dc={dcp:.2f})")
        elif in_zone:
            rz_score += 7.0 * rz_tf_w.get(tf, 0.30)
            rz_diag.append(f"{tf}:edge(bb={bb:.2f},dc={dcp:.2f})")
        elif osc_hot:
            rz_score += 3.0 * rz_tf_w.get(tf, 0.30)
    rz_score = min(rz_score * 5.0, 30.0)

    # 3. WT_FLIP (max 20)
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

    # 4. CROSS event (max 10)
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

    base_score = slow_score + rz_score + flip_score + cross_score
    synergy = (slow_score >= 20.0 and rz_score >= 15.0)
    if synergy:
        final_score = min(base_score * fire_mult, 100.0)
        prefix = "SLOWDOWN_RZ_FIRE"
    elif slow_score >= 20.0:
        final_score = min(base_score, 100.0)
        prefix = "SLOWDOWN_FIRE"
    elif flip_score >= 15.0 and cross_score >= 5.0:
        final_score = min(base_score, 100.0)
        prefix = "DEEP_REVERSAL"
    else:
        final_score = min(base_score, 100.0)
        prefix = "HOLD" if final_score < 25 else "PARTIAL"

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
    sc, reason = score_exit(indicators, is_long, current_price, cfg=cfg)
    return (sc >= threshold, sc, reason)
