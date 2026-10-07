"""REDUCE_PROFIT_LOCK family — live-parity vec twins (2026-09-28 parity round 3).

Pure per-bar predicates consumed by v12_quick_engine's position loop; no numpy state,
no side effects. USER: "red flag any 0 or repeated delta... we need books filled and
credible trades on charts" — these switches were QuickConfig fields with no engine
reads, echoing one identical delta across 100+ yellows.

LIVE SOURCES:
1. PARTIAL_PROFIT_LOCK v2 — stocks tradier_manage.py:18720-18798, crypto
   ez_positions_quick.py:18586-18640 (same v2 state machine, non-_TRADIER param names):
   Step 1: gain >= GAIN_PCT (0.5) -> reduce FRAC (0.5), stop_level = entry*(1 -/+ BE_BUFFER_PCT/100);
   Step 2: gain >= ARM_GAIN_PCT (0.75) -> stop_level upgraded to first_exit_price;
   Step 3: price back through stop_level -> close the remainder.
2. WT_D_BOUNCE_DD_STOP — tradier_manage.py:11705-11716: after an augment, price falling
   back through the last augment price cuts the augment leg (REDUCE of that leg's qty),
   once per leg (dd_qty zeroed after the cut, re-armed by the next augment).
3. NOLOSS_BYPASS_WT_5OF5 — tradier_manage.py:19067-19086: when enabled, >= MIN_TFS of
   the five WT TFs (5m/15m/1h/4h/D) against the position (long: wt1<wt2) bypasses the
   NOLOSS hold so technical exits may close at a loss. Default OFF, sweep-only.

NOT vectorizable (stub-only live reads, tradier_manage.py:32589/32623 `_=getattr`):
PARTIAL_PROFIT_LOCK_V2_FILTER_TF, NOLOSS_BYPASS_WT5OF5_FILTER_TF -> DEAD_VEC list.
"""
from __future__ import annotations


def ppl_params(config, is_tradier: bool):
    """Venue-aware PPL thresholds with live defaults (tradier *_TRADIER names first)."""
    enabled = bool(getattr(config, "PARTIAL_PROFIT_LOCK_ENABLED", False))
    if is_tradier:
        min_gain = float(getattr(config, "PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER", 0.5) or 0.5)
        arm_gain = float(getattr(config, "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER", 0.75) or 0.75)
        be_buffer = float(getattr(config, "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER", 0.02) or 0.02)
        frac = float(getattr(config, "PARTIAL_PROFIT_LOCK_FRAC_TRADIER", 0.5) or 0.5)
    else:
        min_gain = float(getattr(config, "PARTIAL_PROFIT_LOCK_GAIN_PCT", getattr(config, "PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER", 0.5)) or 0.5)
        arm_gain = float(getattr(config, "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT", getattr(config, "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER", 0.75)) or 0.75)
        be_buffer = float(getattr(config, "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT", getattr(config, "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER", 0.02)) or 0.02)
        frac = float(getattr(config, "PARTIAL_PROFIT_LOCK_FRAC", getattr(config, "PARTIAL_PROFIT_LOCK_FRAC_TRADIER", 0.5)) or 0.5)
    return enabled, min_gain, arm_gain, be_buffer, frac


def ppl_step(config, is_tradier: bool, is_long: bool, px: float, entry_px: float, gain_pct: float, state: dict):
    """One PPL state-machine step. Returns (action, frac, reason, new_state).
    action in ('', 'REDUCE', 'CLOSE_REMAINDER'). state keys: fired, first_exit_price,
    stop_level, stop_upgraded — faithful to trade_manager.partial_profit_lock_state."""
    enabled, min_gain, arm_gain, be_buffer, frac = ppl_params(config, is_tradier)
    if not enabled:
        return "", 0.0, "", state if not state else {}
    st = dict(state or {})
    fired = bool(st.get("fired"))
    stop_level = float(st.get("stop_level", 0.0) or 0.0)
    first_exit = float(st.get("first_exit_price", 0.0) or 0.0)
    upgraded = bool(st.get("stop_upgraded"))
    if not fired and gain_pct >= min_gain and entry_px > 0:
        # [N4 fix] live (tradier_manage.py:19340 / ez_positions_quick.py:18651): BE stop = entry*(1+buf/100) LONG, entry*(1-buf/100) SHORT (BE PLUS buffer, i.e. locks a hair of profit); vec had the sign inverted
        _live_sign = bool(getattr(config, 'PPL_BE_STOP_LIVE_SIGN', False))  # [FLT] default False = legacy vector sign (neutral); True = live BE+buffer
        be_stop = (entry_px * (1 + be_buffer / 100.0) if is_long else entry_px * (1 - be_buffer / 100.0)) if _live_sign else (entry_px * (1 - be_buffer / 100.0) if is_long else entry_px * (1 + be_buffer / 100.0))
        st = {"fired": True, "first_exit_price": px, "stop_level": be_stop, "stop_upgraded": False}
        return "REDUCE", frac, f"PPL_TP_g{gain_pct:.2f}%>={min_gain:.2f}%_stopBE{be_stop:.4f}", st
    if fired and not upgraded and gain_pct >= arm_gain and first_exit > 0:
        st["stop_level"] = first_exit
        st["stop_upgraded"] = True
        return "", 0.0, "", st
    if fired and stop_level > 0 and ((is_long and px <= stop_level) or ((not is_long) and px >= stop_level)):
        label = "upgraded-scalp" if upgraded else "BE+buffer"
        return "CLOSE_REMAINDER", 1.0, f"PPL_SL_CLOSE_{label}_px{px:.4f}_stop{stop_level:.4f}", st
    return "", 0.0, "", st


def dd_bounce_stop_fires(config, is_long: bool, px: float, last_aug_px: float, last_aug_qty: float, dd_armed: bool):
    """WT_D_BOUNCE_DD_STOP: cut the augment leg when price crosses back through the last
    augment price (tradier_manage.py:11710). Once per leg (dd_armed re-set on augment)."""
    if not bool(getattr(config, "WT_D_BOUNCE_DD_STOP_ENABLED", True)):
        return False
    if not dd_armed or last_aug_qty <= 0 or last_aug_px <= 0:
        return False
    return px < last_aug_px if is_long else px > last_aug_px


def noloss_bypass_params(config):
    return bool(getattr(config, "NOLOSS_BYPASS_WT_5OF5_ENABLED", False)), int(float(getattr(config, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS", 5) or 5))


def noloss_bypass_pass(config, against_count: int) -> bool:
    """>= MIN_TFS WT TFs against the position -> bypass the NOLOSS hold (allow loss exits)."""
    enabled, min_tfs = noloss_bypass_params(config)
    return enabled and against_count >= min_tfs
