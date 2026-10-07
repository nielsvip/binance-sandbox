"""check_exit_candidates_crypto__wt_cross_exit — WT_CROSS_EXIT (1h WT flip against
direction, with 15m confirm + 3m veto) predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
14188-14219:

  GATE (caller-applied state): not hard_exit_reason, not is_hedge, not _in_grace_period,
       WT_CROSS_EXIT_ENABLED (default True), position.gain > 0.015,
       age >= WT_CROSS_EXIT_MIN_AGE_MINUTES (default 2.0),
       pnl_ok: (losers_ok and gain<0) or (winners_ok and gain>=0)
  INDICATOR FIRE (this module):
       have_1h = wt1_1h!=0 or wt2_1h!=0   (else no fire)
       LONG : 1h_flipped = wt1_1h < wt2_1h ; 15m_confirm = (wt1_15m<wt2_15m) if have_15m else True
       SHORT: 1h_flipped = wt1_1h > wt2_1h ; 15m_confirm = (wt1_15m>wt2_15m) if have_15m else True
       require_15m = WT_CROSS_EXIT_REQUIRE_15M_CONFIRM (default True)
       fire_base = 1h_flipped AND (not require_15m OR 15m_confirm)
       3m_veto: have_3m AND 3m WT still WITH position AND age < WT_CROSS_EXIT_3M_VETO_MAX_AGE (30)
                -> HOLD (suppress). Else fire.

This is the pure-indicator + state(age) WT cross. The 3m-veto age gate (age <
3m_veto_max_age) is a STATE seam supplied by the caller. The gain>0.015 / pnl_ok /
min_age gates are also state, layered by the caller.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar +
vec share the pure core _wt_cross_exit_fires() — CANNOT drift. The 3m veto is folded
into the core as an explicit `age_lt_veto` boolean argument so the decision stays pure.

NPZ / indicator fields read:
  - wt1_1h / wt2_1h, wt1_15m / wt2_15m, wt1_3m / wt2_3m   — all present in NPZ
  - position age (minutes) — state seam (3m veto)

Config:
  WT_CROSS_EXIT_ENABLED            (default True)
  WT_CROSS_EXIT_REQUIRE_15M_CONFIRM(default True)
  WT_CROSS_EXIT_3M_VETO_MAX_AGE    (default 30.0 minutes)
"""
from typing import Tuple
import numpy as np


def _wt_cross_exit_fires(w1_1h, w2_1h, w1_15m, w2_15m, w1_3m, w2_3m, is_long: bool,
                         require_15m: bool, age_lt_3m_veto: bool) -> bool:
    """PURE per-bar fire test. Mirrors live 14199-14217 EXACTLY.
    age_lt_3m_veto: True when position age < WT_CROSS_EXIT_3M_VETO_MAX_AGE (caller computes)."""
    have_1h = (w1_1h != 0) or (w2_1h != 0)
    if not have_1h:
        return False
    have_15m = (w1_15m != 0) or (w2_15m != 0)
    if is_long:
        flipped_1h = w1_1h < w2_1h
        confirm_15m = (w1_15m < w2_15m) if have_15m else True
    else:
        flipped_1h = w1_1h > w2_1h
        confirm_15m = (w1_15m > w2_15m) if have_15m else True
    if not (flipped_1h and (not require_15m or confirm_15m)):
        return False
    have_3m = (w1_3m != 0) or (w2_3m != 0)
    if is_long:
        m3_with_pos = have_3m and (w1_3m > w2_3m)
    else:
        m3_with_pos = have_3m and (w1_3m < w2_3m)
    if m3_with_pos and age_lt_3m_veto:
        return False
    return True


def check_wt_cross_exit(config, indicators: dict, is_long: bool, pos_age_min: float) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). Fire from shared core. The gain /
    pnl_ok / min_age caller gates are NOT applied here (the live `if` applies them
    before this block); only the indicator fire + 3m-veto-age are computed."""
    if not bool(getattr(config, "WT_CROSS_EXIT_ENABLED", True)):
        return False, ""
    require_15m = bool(getattr(config, "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True))
    veto_age = float(getattr(config, "WT_CROSS_EXIT_3M_VETO_MAX_AGE", 30.0))
    g = lambda k: float(indicators.get(k, 0) or 0)
    w1_1h, w2_1h = g("wt1_1h"), g("wt2_1h")
    w1_15m, w2_15m = g("wt1_15m"), g("wt2_15m")
    w1_3m, w2_3m = g("wt1_3m"), g("wt2_3m")
    if not _wt_cross_exit_fires(w1_1h, w2_1h, w1_15m, w2_15m, w1_3m, w2_3m, is_long,
                                require_15m, pos_age_min < veto_age):
        return False, ""
    return True, f"WT_CROSS_EXIT_1h_{'bear' if is_long else 'bull'}_wt1={w1_1h:.1f}_wt2={w2_1h:.1f}"


def check_wt_cross_exit_vec(config, w1_1h_a, w2_1h_a, w1_15m_a, w2_15m_a, w1_3m_a, w2_3m_a,
                            is_long, pos_age_min_arr) -> np.ndarray:
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as scalar.
    pos_age_min_arr is the per-bar position age (state seam) used for the 3m veto."""
    a1 = np.asarray(w1_1h_a, dtype=float); b1 = np.asarray(w2_1h_a, dtype=float)
    a15 = np.asarray(w1_15m_a, dtype=float); b15 = np.asarray(w2_15m_a, dtype=float)
    a3 = np.asarray(w1_3m_a, dtype=float); b3 = np.asarray(w2_3m_a, dtype=float)
    n = len(a1)
    if not bool(getattr(config, "WT_CROSS_EXIT_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    require_15m = bool(getattr(config, "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True))
    veto_age = float(getattr(config, "WT_CROSS_EXIT_3M_VETO_MAX_AGE", 30.0))
    age = np.asarray(pos_age_min_arr, dtype=float)
    have_1h = (a1 != 0) | (b1 != 0)
    have_15m = (a15 != 0) | (b15 != 0)
    have_3m = (a3 != 0) | (b3 != 0)
    if is_long:
        flipped = a1 < b1
        confirm = np.where(have_15m, a15 < b15, True)
        m3_with = have_3m & (a3 > b3)
    else:
        flipped = a1 > b1
        confirm = np.where(have_15m, a15 > b15, True)
        m3_with = have_3m & (a3 < b3)
    fire = have_1h & flipped
    if require_15m:
        fire &= confirm
    veto = m3_with & (age < veto_age)
    return fire & ~veto
