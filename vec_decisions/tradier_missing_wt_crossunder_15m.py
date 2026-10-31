"""15-minute-floor adapter for the native Tradier WT final exit."""
import numpy as np
from vec_decisions.check_exit_candidates_stocks__wt_crossunder_final import (
    _wt_crossunder_final_fires,
)


def _values(indicators):
    indicators = indicators or {}
    return (
        float(indicators.get("wt1_15m", 0.0) or 0.0),
        float(indicators.get("wt2_15m", 0.0) or 0.0),
        float(indicators.get("wt1_15m", 0.0) or 0.0),
        float(indicators.get("wt2_15m", 0.0) or 0.0),
        float(indicators.get("wt1_1h", 0.0) or 0.0),
        float(indicators.get("wt2_1h", 0.0) or 0.0),
        float(indicators.get("wt1_4h", 0.0) or 0.0),
        float(indicators.get("wt2_4h", 0.0) or 0.0),
        float(indicators.get("wt1_D", 0.0) or 0.0),
        float(indicators.get("wt2_D", 0.0) or 0.0),
    )


def wt_crossunder_15m_live(get, is_long, indicators, gain_pct=0.0, hold_time_min=0.0):
    """Evaluate the live directional WT + 15m and HTF confirmation on 15m data."""
    if not bool(get("WT_CROSSUNDER_FINAL_ENABLED", False)):
        return False, ""
    try:
        vals = _values(indicators)
        if not _wt_crossunder_final_fires(*vals, is_long):
            return False, ""
        w1_1h, w2_1h = vals[4], vals[5]
        w1_4h, w2_4h = vals[6], vals[7]
        w1_d, w2_d = vals[8], vals[9]
        if is_long:
            t1, t4, td = w1_1h < w2_1h, w1_4h < w2_4h, w1_d < w2_d
            side = "WT_CROSSUNDER_FINAL"
        else:
            t1, t4, td = w1_1h > w2_1h, w1_4h > w2_4h, w1_d > w2_d
            side = "WT_CROSSOVER_FINAL"
        reason = (
            f"{side}_5m_15m_1h{t1}_4h{t4}_D{td}_g{gain_pct:.2f}%_"
            f"hold{hold_time_min:.0f}m_MANDATORY_REENTRY"
        )
        return True, reason
    except (TypeError, ValueError):
        return False, ""


def wt_crossunder_15m_mask(npz, n, is_long, cfg, safe):
    """Return the native predicate on 15m values, including the parabolic veto."""
    if not bool(getattr(cfg, "WT_CROSSUNDER_FINAL_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    w1 = np.asarray(safe(npz, "wt1_15m", n, 0.0), dtype=float)
    w2 = np.asarray(safe(npz, "wt2_15m", n, 0.0), dtype=float)
    w1h = np.asarray(safe(npz, "wt1_1h", n, 0.0), dtype=float)
    w2h = np.asarray(safe(npz, "wt2_1h", n, 0.0), dtype=float)
    w4h = np.asarray(safe(npz, "wt1_4h", n, 0.0), dtype=float)
    w24h = np.asarray(safe(npz, "wt2_4h", n, 0.0), dtype=float)
    wd = np.asarray(safe(npz, "wt1_D", n, 0.0), dtype=float)
    w2d = np.asarray(safe(npz, "wt2_D", n, 0.0), dtype=float)
    if is_long:
        fire = (w1 < w2) & ((w1 < w2) | (w1 > 95)) & ((w1h < w2h) | (w4h < w24h) | (wd < w2d))
    else:
        fire = (w1 > w2) & ((w1 > w2) | (w1 < -95)) & ((w1h > w2h) | (w4h > w24h) | (wd > w2d))
    try:
        from vec_decisions.wt_crossover_final_vec import parabolic_guard_mask

        fire &= ~parabolic_guard_mask(npz, n, is_long, cfg, safe)
    except Exception:
        pass
    return np.asarray(fire, dtype=bool)
