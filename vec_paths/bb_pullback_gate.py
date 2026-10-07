"""BB Pullback Gate — pre-entry filter requiring price in favorable BB region.

LONG: only enter when bb_pct_b_{TF} <= threshold (price near lower BB = pullback)
SHORT: only enter when bb_pct_b_{TF} >= (1 - threshold) (price near upper BB = pullback)

Proven by vec_top_combos: bb15_lt30 was key component of pool_sharpe 0.68 winning combo.
The OLD BB_BREAKOUT logic (buy when bb_pct_b > 1.0 = overbought) was harmful (ΔSharpe -0.0069).
"""
import numpy as np


def precompute_bb_pullback_gate(npz, config, is_long, n, mode="tradier"):
    if not bool(getattr(config, "BB_PULLBACK_GATE_ENABLED", False)):
        return np.ones(n, dtype=bool)
    tf = str(getattr(config, "BB_PULLBACK_GATE_TF", "15m"))
    key = f"bb_pct_b_{tf}"
    bb_pctb = npz.get(key)
    if bb_pctb is None:
        return np.ones(n, dtype=bool)
    bb_pctb = np.nan_to_num(bb_pctb, nan=0.5).astype(np.float32)
    if is_long:
        thr = float(getattr(config, "BB_PULLBACK_GATE_LONG_MAX", 0.30))
        return bb_pctb <= thr
    else:
        thr = float(getattr(config, "BB_PULLBACK_GATE_SHORT_MIN", 0.70))
        return bb_pctb >= thr
