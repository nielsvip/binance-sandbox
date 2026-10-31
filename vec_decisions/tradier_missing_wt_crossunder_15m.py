"""15-minute decision-grid adapter for the Tradier WT final exit.

The native rule currently asks for 5-minute WT + stochastic values. The frozen
NPZ does not carry those series. Under SWITCH_WIRING_GUIDE.md §4, map those
legs to real 15-minute arrays when sub-15m trading is off. The live scalar
adapter delegates to the existing native predicate after this explicit map.
"""
import numpy as np

from vec_decisions.twin_entry_ports_b import wt_crossunder_final_exit


def wt_crossunder_15m_live(get, is_long, indicators):
    """Evaluate the shared native scalar predicate with real 15m observations."""
    indicators = indicators or {}
    w1 = indicators.get("wt1_15m")
    w2 = indicators.get("wt2_15m")
    k = indicators.get("stoch_k_15m", indicators.get("k_15m"))
    if w1 is None or w2 is None or k is None:
        return False, ""
    mapped = {
        "wt1_5m": w1,
        "wt2_5m": w2,
        "wt1_5m_prev": indicators.get("wt1_15m_prev", w1),
        "stoch_k_5m": k,
    }
    return wt_crossunder_final_exit(get, is_long, mapped, "5m")


def wt_crossunder_15m_mask(npz, n, is_long, cfg, safe):
    """Return the native WT_CROSSUNDER_FINAL predicate as a causal bool mask."""
    if not bool(getattr(cfg, "WT_CROSSUNDER_FINAL_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    w1 = np.asarray(safe(npz, "wt1_15m", n, 0.0), dtype=float)
    w2 = np.asarray(safe(npz, "wt2_15m", n, 0.0), dtype=float)
    k = np.asarray(safe(npz, "stoch_k_15m", n, 50.0), dtype=float)
    w1_prev = np.roll(w1, 1)
    if n:
        w1_prev[0] = w1[0]
    if is_long:
        return (w1_prev >= w2) & (w1 < w2) & (k >= 70.0)
    return (w1_prev <= w2) & (w1 > w2) & (k <= 30.0)
