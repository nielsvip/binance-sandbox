"""Re-entry triggers after an exit (USER 2026-10-14: "a switch either breaking exit price or dc_high4_15m or a higher low (vv
short) … all 3 can be true!"). Successor of the USER's precise re-entry rule VEC_REENTRY_DC4_EXITPRICE_ENABLED
(v12_wide_engine.py:7698-7762, never ported to v12_quick_engine) as THREE independent switches, OR-ed:

  REENTRY_ON_EXIT_PRICE_BREAK_ENABLED  LONG close crosses UP through the last exit price   (SHORT: down through)
  REENTRY_ON_DC4_15M_BREAK_ENABLED     LONG close crosses UP through dc_high4_15m of the prior closed bar
                                       (SHORT: down through dc_low4_15m)
  REENTRY_ON_HIGHER_LOW_ENABLED        LONG the bar's low is ABOVE the lowest low since the exit (a higher low formed
                                       after the post-exit low) and the bar closes up   (SHORT: lower high + down close)

Every trigger requires the original rule's 15m WT confirmation: LONG wt1_15m > wt2_15m and wt_velocity_15m >= 0
(SHORT mirrored). Armed after ANY exit (FIXED_STOP included) while flat; the normal entry gates still apply downstream.
ONE function shared by v12_quick_engine.simulate_one and the native live paths (ez_manage / tradier_manage) — no drift.
"""
from __future__ import annotations

SWITCHES = ("REENTRY_ON_EXIT_PRICE_BREAK_ENABLED", "REENTRY_ON_DC4_15M_BREAK_ENABLED", "REENTRY_ON_HIGHER_LOW_ENABLED")


def _on(cfg, name, default=True) -> bool:
    v = cfg(name, default) if callable(cfg) else getattr(cfg, name, default)
    return (str(v).strip().lower() in ("1", "true", "yes")) if isinstance(v, str) else bool(v)


def any_enabled(cfg) -> bool:
    return any(_on(cfg, s) for s in SWITCHES)


def fire(cfg, is_long: bool, close: float, prev_close: float, exit_px: float, dc4_prev: float, dc4_prev2: float,
         bar_low: float, bar_high: float, ext_since_exit: float, wt1: float, wt2: float, wt_vel: float):
    """Return the trigger name that fires on this closed 15m bar, or "".

    dc4_prev / dc4_prev2: dc_high4_15m (LONG) or dc_low4_15m (SHORT) of the previous and the one-before-previous bar
    (the closed-bar channel; live reads it from the indicator dict computed on closed bars).
    ext_since_exit: LONG lowest 15m low / SHORT highest 15m high from the exit bar up to the PREVIOUS bar (0 = unknown).
    cfg: config object or callable(name, default) (live per-sym chain)."""
    try:
        if not (close > 0 and prev_close > 0):
            return ""
        if is_long:
            if not (wt1 > wt2 and wt_vel >= 0.0):
                return ""
        elif not (wt1 < wt2 and wt_vel <= 0.0):
            return ""
        if _on(cfg, "REENTRY_ON_EXIT_PRICE_BREAK_ENABLED") and exit_px > 0:
            if (is_long and close >= exit_px > prev_close) or ((not is_long) and close <= exit_px < prev_close):
                return "REENTRY_EXIT_PRICE_BREAK"
        if _on(cfg, "REENTRY_ON_DC4_15M_BREAK_ENABLED") and dc4_prev > 0:
            if is_long and close > dc4_prev and (dc4_prev2 <= 0 or prev_close <= dc4_prev2):
                return "REENTRY_DC4_15M_BREAK"
            if (not is_long) and close < dc4_prev and (dc4_prev2 <= 0 or prev_close >= dc4_prev2):
                return "REENTRY_DC4_15M_BREAK"
        if _on(cfg, "REENTRY_ON_HIGHER_LOW_ENABLED") and ext_since_exit > 0:
            if is_long and bar_low > ext_since_exit and close > prev_close:
                return "REENTRY_HIGHER_LOW"
            if (not is_long) and 0 < bar_high < ext_since_exit and close < prev_close:
                return "REENTRY_LOWER_HIGH"
    except Exception:
        return ""
    return ""


def fire_live(cfg, is_long: bool, price: float, ind: dict, exit_px: float, exit_ts: float, klines_path=None) -> str:
    """Native live adapter (ez_manage / tradier_manage, used when the vec twin is not deciding): the same fire() on the live
    indicator dict (closed-bar 15m values) + the 15m klines cache for the extreme since the exit."""
    try:
        g = lambda k: float((ind or {}).get(k, 0) or 0)
        prev_close = g("close_15m_prev") or g("close_15m")
        dc4 = g("dc_high4_15m") if is_long else g("dc_low4_15m")
        dc4p = g("dc_high4_15m_prev") if is_long else g("dc_low4_15m_prev")
        ext = 0.0
        if klines_path and exit_ts > 0:
            from live_twins.reentry_augment import klines_bars

            bars = [b for b in (klines_bars(klines_path) or []) if b[0] and b[0] >= exit_ts - 900 and b[1] and b[2]]
            if len(bars) >= 2:
                hist = bars[:-1]
                ext = min(float(b[1]) for b in hist) if is_long else max(float(b[2]) for b in hist)
        return fire(cfg, is_long, float(price), prev_close, float(exit_px or 0), dc4, dc4p, g("low_15m"), g("high_15m"), ext,
                    g("wt1_15m"), g("wt2_15m"), g("wt_velocity_15m"))
    except Exception:
        return ""
