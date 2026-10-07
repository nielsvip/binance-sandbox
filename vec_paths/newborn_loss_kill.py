"""
vec_paths/newborn_loss_kill.py — NEWBORN_LOSS_KILL exit trigger (V4 surgical+min_age).

LIVE SOURCE: ez_manage.py:~37878 (process_position_enter, just before R1 block).

WHY THIS EXISTS:
    User mandate 2026-05-22 (post-ORDIUSDC + breakout-respect):
    "BREAKOUTS GET RESPECTED but CLOSE AT ENTRY PRICE as they most likely fall
    back — THEN THEY NEED TO GET FLAGGED AND REENTER AS SOON AS THEY BREAK OUT
    AGAIN OR BOUNCE."

    Plus mid-session user clarification:
    "they need time to breathe so do not let it kick in until 12? 25? min after open?"

    V4 surgical NLK fires when ALL of:
        breakout_entry == True (entry bypassed TOR_BLOCK via raw_dc_pos>1.0)
        AND NEWBORN_LOSS_KILL_MIN_AGE_MIN ≤ age ≤ NEWBORN_LOSS_KILL_WINDOW_MIN
        AND gain_pct ≤ NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT
        AND (REQUIRE_VEL_AGAINST=False OR wt_velocity_TF is against position)
    → CLOSE at ~breakeven, bypassing UNIVERSAL_NOLOSS_GATE.

    THRESHOLD HISTORY (sub-floor 7-sym × 1.95yr vec):
      V1   -0.5% no vel, 30min window           : ΔSharpe=-0.0139 (closed wicks)
      V2   -0.5% + vel against, 30min           : ΔSharpe=-0.0140 (vel-against ~always true)
      V3    0.0% + vel against, 30min, all-pos  : ΔSharpe=-0.0070 (still hurts)
      V3a   0.0% + vel against, 60min, all-pos  : ΔSharpe=-0.0230 (longer = much worse)
      V3b  -0.1% + vel against, 30min, all-pos  : ΔSharpe=-0.0065 (smallest of the bad)
      V4 (current) surgical (breakout-only) + min_age 15min + 0.0% + vel against
          → testing now; expected smaller scope = less collateral damage on
            non-breakout entries that just have normal pullbacks.

CONFIG KEYS:
    NEWBORN_LOSS_KILL_ENABLED: bool = False
    NEWBORN_LOSS_KILL_MIN_AGE_MIN: float = 15.0    # V4 — give it time to breathe
    NEWBORN_LOSS_KILL_WINDOW_MIN: float = 30.0
    NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT: float = 0.0
    NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST: bool = True
    NEWBORN_LOSS_KILL_VEL_TF: str = ""             # auto: "3m" crypto / "5m" tradier
    NEWBORN_LOSS_KILL_SURGICAL_ONLY: bool = True   # V4 — only fire on breakout_entry positions

RETURN TYPE:
    None  — did not fire
    dict  — {
        "reason": str,         # NEWBORN_LOSS_KILL_age<N>m_g<X>_vel<V>
        "path": "NEWBORN_LOSS_KILL",
        "bypass_noloss": True,
    }
"""
from __future__ import annotations

from typing import Any, Optional


def check_newborn_loss_kill_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    """NEWBORN_LOSS_KILL V4 — surgical breakout-only with min_age gate.

    Mirrors ez_manage.py:~37878 (NEWBORN_LOSS_KILL block in process_position_enter).
    Bypasses UNIVERSAL_NOLOSS_GATE and NEWBORN_PROTECT.

    Args:
        store:     _NPZStore (supports .f(key, idx))
        bar_idx:   current bar index
        pos_state: _PositionState (has open, side, gain_pct, entry_ts, breakout_entry)
        mode:      "crypto" or "tradier"
        cfg:       VecConfig (NEWBORN_LOSS_KILL_* attributes)

    Returns:
        dict with reason/path/bypass_noloss, or None if not firing.
    """
    if not getattr(cfg, "NEWBORN_LOSS_KILL_ENABLED", False):
        return None
    if not pos_state.open:
        return None

    # V4 surgical gate — only fire on positions that bypassed TOR via breakout exception.
    if bool(getattr(cfg, "NEWBORN_LOSS_KILL_SURGICAL_ONLY", True)):
        if not bool(getattr(pos_state, "breakout_entry", False)):
            return None

    age_min = 9999.0
    try:
        _ts_arr = store.timestamps if hasattr(store, "timestamps") else store.arrays.get("timestamps")
        if _ts_arr is not None and bar_idx < len(_ts_arr) and pos_state.entry_ts > 0:
            age_min = (float(_ts_arr[bar_idx]) - pos_state.entry_ts) / 60.0
    except Exception:
        pass

    min_age = float(getattr(cfg, "NEWBORN_LOSS_KILL_MIN_AGE_MIN", 15.0))
    window_min = float(getattr(cfg, "NEWBORN_LOSS_KILL_WINDOW_MIN", 30.0))
    if age_min < min_age or age_min > window_min:
        return None

    gain = float(getattr(pos_state, "gain_pct", 0.0))
    threshold = float(getattr(cfg, "NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT", 0.0))
    if gain > threshold:
        return None

    vel_used = 0.0
    if bool(getattr(cfg, "NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST", True)):
        tf = getattr(cfg, "NEWBORN_LOSS_KILL_VEL_TF", "")
        if not tf:
            tf = "5m" if mode == "tradier" else "3m"
        vel_used = float(store.f(f"wt_velocity_{tf}", bar_idx, 0.0))
        is_long = (pos_state.side == "LONG")
        vel_against = (is_long and vel_used < 0) or ((not is_long) and vel_used > 0)
        if not vel_against:
            return None

    reason = f"NEWBORN_LOSS_KILL_BREAKOUT_age{age_min:.1f}m_g{gain:.2f}_vel{vel_used:.2f}"
    return {
        "reason": reason,
        "path": "NEWBORN_LOSS_KILL",
        "bypass_noloss": True,
    }
