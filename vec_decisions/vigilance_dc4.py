"""vigilance_dc4 — VIGILANCE_DC4 structural stop, ONE predicate for live (ez_manage) and the vector (v12_quick_engine).

USER 2026-10-27 (operator ruling, verbatim): "VIGILANCE_DC4_EXIT needs to be in switches and vectorized functions".
USER 2026-09-28 (4th mandate): the DC4 stop is "one of many exit switches to be tried NOT a hardcoded exit path".

Fires when the position is at a LOSS and price breaches dc_low4_{TF} (LONG) / dc_high4_{TF} (SHORT) by at least the
tolerance on the CHANNEL LEVEL (VIGILANCE_DC4_BREACH_TOLERANCE_PCT, a touch is not a breach). The switch is
VIGILANCE_DC4_STOP_ARMED (default False = what both engines did before: the lane was hard-disabled on both sides),
and VIGILANCE_DC4_STOP_TF ('OFF' disables). VIGILANCE_GUARD_ENABLED is deliberately NOT a master here: its default
differs between QuickConfig (False) and config.py (True), so using it would make the same per-sym set arm the stop
live but not on the board. Pure.
"""

from __future__ import annotations


def armed(get) -> bool:
    """get(key, default) -> value. Both must allow the stop."""
    try:
        if not bool(get("VIGILANCE_DC4_STOP_ARMED", False)):
            return False
        return str(get("VIGILANCE_DC4_STOP_TF", "15m") or "OFF").strip().upper() != "OFF"
    except Exception:
        return False


def breach(is_long: bool, px: float, lvl: float, tol_pct: float) -> bool:
    try:
        px, lvl, tol = float(px), float(lvl), float(tol_pct or 0.0) / 100.0
    except (TypeError, ValueError):
        return False
    if lvl <= 0 or px <= 0:
        return False
    return (is_long and px <= lvl * (1.0 - tol)) or ((not is_long) and px >= lvl * (1.0 + tol))


def fires(is_long: bool, px: float, lvl: float, tol_pct: float, gain_pct: float) -> bool:
    try:
        if not float(gain_pct) < 0.0:
            return False
    except (TypeError, ValueError):
        return False
    return breach(is_long, px, lvl, tol_pct)


def block_expired(age_hours: float, hours: float) -> bool:
    """The DC4-stop entry block expires after VIGILANCE_BLOCK_HOURS (0 = open-ended). Live ez_vigilance_is_blocked and the
    vector's release both call this."""
    try:
        h = float(hours or 0.0)
        return h > 0 and float(age_hours) >= h
    except (TypeError, ValueError):
        return False


def loss_close_allowed(reason, armed_now: bool) -> bool:
    """Live twin of the vector's mandatory_stop_kind=VIGILANCE_DC4_ARMED marker: an armed DC4 vigilance close may close red."""
    return bool(armed_now) and str(reason or "").upper().startswith("VIGILANCE_DC4_")
