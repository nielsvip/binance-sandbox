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
    fire = check_wt_cross_exit_indicator_vec(config, a1, b1, a15, b15, is_long)
    if not bool(getattr(config, "WT_CROSS_EXIT_ENABLED", True)):
        return fire
    veto_age = float(getattr(config, "WT_CROSS_EXIT_3M_VETO_MAX_AGE", 30.0))
    age = np.asarray(pos_age_min_arr, dtype=float)
    have_3m = (a3 != 0) | (b3 != 0)
    if is_long:
        m3_with = have_3m & (a3 > b3)
    else:
        m3_with = have_3m & (a3 < b3)
    veto = m3_with & (age < veto_age)
    return fire & ~veto


def check_wt_cross_exit_indicator_vec(config, w1_1h_a, w2_1h_a, w1_15m_a, w2_15m_a,
                                      is_long) -> np.ndarray:
    """Causal NumPy mask for the 1h cross and optional 15m confirmation.
    Position age and the 3m veto remain state gates in the bar-by-bar caller."""
    a1 = np.asarray(w1_1h_a, dtype=float); b1 = np.asarray(w2_1h_a, dtype=float)
    a15 = np.asarray(w1_15m_a, dtype=float); b15 = np.asarray(w2_15m_a, dtype=float)
    n = len(a1)
    if not bool(getattr(config, "WT_CROSS_EXIT_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    have_1h = (a1 != 0) | (b1 != 0)
    have_15m = (a15 != 0) | (b15 != 0)
    if is_long:
        fire = have_1h & (a1 < b1)
        confirm = np.where(have_15m, a15 < b15, True)
    else:
        fire = have_1h & (a1 > b1)
        confirm = np.where(have_15m, a15 > b15, True)
    if bool(getattr(config, "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True)):
        fire &= confirm
    return fire


# 2026-10-28 WT CROSS EXIT TWIN (OPERATOR: "need to be wired immediately and default to whatever is (not) working now so it
# does not change behavior in live trading"). The vector never booked this exit, so live (S1/S5 vector_direct executes the
# vector's states) never fired it either, while 723 of 785 published per-sym sets carry WT_CROSS_EXIT_ENABLED=True. Arming the
# twin on WT_CROSS_EXIT_ENABLED alone would therefore change 723 live sym_sides at once. The twin is gated by its OWN explicit
# switch WT_CROSS_EXIT_VEC_ENABLED (default False = today's effective behaviour, template row + config/QuickConfig mirrors),
# exactly like DC15M_FORCE_EXIT_ENABLED / IN_GAIN_TREND_EXIT_ENABLED in live_native_exit_twins. A board promoting True arms it;
# WT_CROSS_EXIT_ENABLED / _APPLIES_TO_* / _MIN_AGE_MINUTES / _REQUIRE_15M_CONFIRM then act exactly as in live.
VEC_SWITCH = "WT_CROSS_EXIT_VEC_ENABLED"


def _sf(v) -> float:
    """live safe_fetch_float twin: None / NaN / junk -> 0.0."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if x != x else x


def wt_cross_exit_armed(c) -> bool:
    return bool(c(VEC_SWITCH, False)) and bool(c("WT_CROSS_EXIT_ENABLED", True))


def wt_cross_exit_state_ok(c, st) -> bool:
    """Caller gates of the live block (ez_positions_quick.py check_exit_candidates ~27296-27316): position.gain > 0.015 (percent),
    not in the grace period (age < 3 min, or REENTRY_GRACE_MINUTES after a reentry, unless augmented), age >= MIN_AGE, and
    pnl_ok = (APPLIES_TO_LOSERS and gain < 0) or (APPLIES_TO_WINNERS and gain >= 0). st: {'gain' %, 'age_s', 'reentered', 'augmented'}."""
    gain = _sf(st.get("gain"))
    if not gain > 0.015:
        return False
    age_min = _sf(st.get("age_s")) / 60.0
    grace = float(c("REENTRY_GRACE_MINUTES", 30.0)) if st.get("reentered") else 3.0
    if age_min < grace and not st.get("augmented"):
        return False
    if age_min < float(c("WT_CROSS_EXIT_MIN_AGE_MINUTES", 2.0)):
        return False
    return (bool(c("WT_CROSS_EXIT_APPLIES_TO_LOSERS", True)) and gain < 0) or (bool(c("WT_CROSS_EXIT_APPLIES_TO_WINNERS", True)) and gain >= 0)


def wt_cross_exit_twin(c, g, st, is_long) -> Tuple[bool, str]:
    """ONE decision for live and vector (grey_wire_exits contract fn(c, g, st, is_long) -> (fire, reason)): the live caller gates +
    the shared indicator core _wt_cross_exit_fires (1h flip, optional 15m confirm, 3m veto while age < WT_CROSS_EXIT_3M_VETO_MAX_AGE)."""
    if not wt_cross_exit_armed(c) or not wt_cross_exit_state_ok(c, st):
        return False, ""
    age_min = _sf(st.get("age_s")) / 60.0
    w1_1h, w2_1h = _sf(g("wt1_1h", 0.0)), _sf(g("wt2_1h", 0.0))
    w1_15m, w2_15m = _sf(g("wt1_15m", 0.0)), _sf(g("wt2_15m", 0.0))
    w1_3m, w2_3m = _sf(g("wt1_3m", 0.0)), _sf(g("wt2_3m", 0.0))
    if not _wt_cross_exit_fires(w1_1h, w2_1h, w1_15m, w2_15m, w1_3m, w2_3m, is_long, bool(c("WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True)), age_min < float(c("WT_CROSS_EXIT_3M_VETO_MAX_AGE", 30.0))):
        return False, ""
    return True, f"WT_CROSS_EXIT_1h_{'bear' if is_long else 'bull'}_wt1={w1_1h:.1f}_wt2={w2_1h:.1f}_15m{w1_15m:.1f}/{w2_15m:.1f}_g{_sf(st.get('gain')):.2f}%"
