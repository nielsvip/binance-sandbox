"""BREAKEVEN_GAIN_EROSION exit + HTF_EXIT_VETO + HARD_BREAKEVEN_FLOOR + TREND_REGIME_VETO — ONE pure core for live and vector.

USER 2026-10-17: "hard-zero breakeven protection ... IT IS A SWITCH THAT FLIPS ACCORDING TO v15_pilot.py test results" -> "make the
engine version". HARD_BREAKEVEN_FLOOR_ENABLED is not an exit of its own: it is the override inside the live breakeven-erosion exit
(ez_positions_quick check_exit_candidates_for_account) that lets a former winner (max_gain >= HARD_BREAKEVEN_MIN_PEAK_PCT) take the
near-breakeven exit even while the HTF exit veto is active, and makes a position that never peaked wait. v12_quick_engine had only a
dead auto-wired stub (_batch1_template_wiring, no callers), so every pilot row of these switches measured 0. Live and the engine now
both call the functions below — they cannot drift.

  trend_veto(get, is_long, wt)           TREND_REGIME_VETO_ENABLED and WT 1h, 4h and D all with the side -> the exit is vetoed
  htf_veto(get, gain, is_long, wt)       HTF_EXIT_VETO_ENABLED, |gain| <= HTF_EXIT_VETO_MAX_LOSS_PCT and >= HTF_EXIT_VETO_MIN_ALIGNED
                                         of WT 1h/4h/D with the side
  window_decision(get, gain, max_gain, age_min, htf_veto_active)
                                         -> (fire, why, aligned-agnostic reason) for the erosion window:
                                            REQUIRE_PROFIT: BREAKEVEN_GAIN_EROSION_MIN_GAIN <= gain < max(MIN_GAIN + 0.5, 0.02)
                                            else gain < 0.02; only after BREAKEVEN_GRACE_MINUTES.
`get(knob, default)` is the caller's per-sym reader; `wt` maps wt1_1h/wt2_1h/wt1_4h/wt2_4h/wt1_D/wt2_D to floats (missing = 0.0).
"""
from __future__ import annotations

import math

REASON = "BREAKEVEN_GAIN_EROSION_STOP"


def _f(v, d=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def _aligned(is_long, wt):
    pairs = [(_f(wt.get(f"wt1_{t}")), _f(wt.get(f"wt2_{t}"))) for t in ("1h", "4h", "D")]
    return [(a > b) if is_long else (a < b) for a, b in pairs]


def trend_veto(get, is_long, wt) -> bool:
    return bool(get("TREND_REGIME_VETO_ENABLED", True)) and all(_aligned(is_long, wt))


def htf_veto(get, gain, is_long, wt) -> tuple:
    """(active, aligned_count)."""
    if not bool(get("HTF_EXIT_VETO_ENABLED", True)) or abs(_f(gain)) > _f(get("HTF_EXIT_VETO_MAX_LOSS_PCT", 2.0), 2.0):
        return False, 0
    n = sum(_aligned(is_long, wt))
    return n >= int(_f(get("HTF_EXIT_VETO_MIN_ALIGNED", 2), 2)), n


def window_decision(get, gain, max_gain, age_min, htf_veto_active) -> tuple:
    """(fire, why, reason). why in {"", "GRACE", "WINDOW", "LOSS_HOLD", "HTF_VETO", "PEAK_GATE", "FIRE", "FIRE_HBF_OVERRIDE"}."""
    gain, max_gain, age_min = _f(gain), _f(max_gain), _f(age_min, 9999.0)
    grace = _f(get("BREAKEVEN_GRACE_MINUTES", 15.0), 15.0)
    req_profit = bool(get("BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT", True))
    comm_buf = _f(get("COMMISSION_BUFFER_PCT", 0.10), 0.10)
    min_gain = _f(get("BREAKEVEN_GAIN_EROSION_MIN_GAIN", comm_buf), comm_buf)
    upper = max(min_gain + 0.5, 0.02)
    in_window = (min_gain <= gain < upper) if req_profit else (gain < 0.02)
    if age_min < grace:
        return False, "GRACE", ""
    if not in_window:
        return False, ("LOSS_HOLD" if gain < 0 else "WINDOW"), ""
    hbf = bool(get("HARD_BREAKEVEN_FLOOR_ENABLED", True))
    min_peak = _f(get("HARD_BREAKEVEN_MIN_PEAK_PCT", 0.5), 0.5)
    override = hbf and max_gain >= min_peak
    if htf_veto_active and not override:
        return False, "HTF_VETO", ""
    if hbf and max_gain < min_peak:
        return False, "PEAK_GATE", ""
    return True, ("FIRE_HBF_OVERRIDE" if (override and htf_veto_active) else "FIRE"), f"{REASON}_age{age_min:.0f}m_gain{gain:.2f}%"


def in_grace(age_min, reentered, augmented, reentry_grace_min) -> bool:
    """Live _in_grace_period: 3 min (REENTRY_GRACE_MINUTES after a re-entry), never once augmented."""
    return _f(age_min, 9999.0) < (_f(reentry_grace_min, 30.0) if reentered else 3.0) and not augmented
