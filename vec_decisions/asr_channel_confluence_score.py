"""ASR stochastic, DC-basis and channel-breakout score additions."""
import numpy as np


def apply_channel_confluence_scalar(score, values, is_long):
    """Mirror ordered per-bar K/DC score additions; values are native locals."""
    v = values
    result = float(score)
    if (is_long and v["k_3m"] > v["d_3m"]) or (not is_long and v["k_3m"] < v["d_3m"]): result += 2.0
    if (is_long and v["k_15m"] < 20) or (not is_long and v["k_15m"] > 80): result += 2.0
    if (is_long and v["k_15m"] > 80) or (not is_long and v["k_15m"] < 20): result -= 3.0
    if (is_long and v["k_3m"] < 20) or (not is_long and v["k_3m"] > 80): result += 2.0
    if (is_long and v["k_3m"] > 80) or (not is_long and v["k_3m"] < 20): result -= 4.0
    if (is_long and v["k_1h"] < v["d_1h"]) or (not is_long and v["k_1h"] > v["d_1h"]): result -= 2.0
    if (is_long and v["k_4h"] < v["d_4h"]) or (not is_long and v["k_4h"] > v["d_4h"]): result -= 1.0
    if (is_long and v["k_1h"] < 30 and v["k_1h"] > v["d_1h"]) or (not is_long and v["k_1h"] > 70 and v["k_1h"] < v["d_1h"]): result += 2.0
    px = v["current_price"]
    if (is_long and px > v["dc_basis_1h"] * 1.04 and px > v["dc_basis_15m"]) or (not is_long and px < v["dc_basis_15m"] * .96 and px < v["dc_basis_15m"]): result += 3.0
    elif (is_long and px > v["dc_basis_1h"] * 1.03) or (not is_long and px < v["dc_basis_1h"] * .93): result += 1.0
    if (is_long and v["sco1h"] and px > v["dc_basis_1h"]) or (not is_long and v["scu1h"] and px < v["dc_basis_1h"] * .93): result += 1.0
    if (is_long and v["sco15m"] and px > v["dc_basis_15m"]) or (not is_long and v["scu15m"] and px < v["dc_basis_15m"]): result += 2.0
    if (is_long and px > v["dc_basis_1h"]) or (not is_long and px < v["dc_basis_1h"]): result += 1.0
    if (is_long and px < v["dc_basis_4h"]) or (not is_long and px > v["dc_basis_4h"]): result -= 2.0
    if v["dc_high_1m"] > 0 and v["dc_low_1m"] > 0:
        if (is_long and px >= v["dc_high_1m"]) or (not is_long and px <= v["dc_low_1m"]): result += .5
    if (is_long and px >= v["dc_high_3m"]) or (not is_long and px <= v["dc_low_3m"]): result += 1.0
    if (is_long and px >= v["dc_high_15m"]) or (not is_long and px <= v["dc_low_15m"]): result += 1.5
    if (is_long and px >= v["dc_high_1h"]) or (not is_long and px <= v["dc_low_1h"]): result += 2.0
    if (is_long and px >= v["dc_high_4h"]) or (not is_long and px <= v["dc_low_4h"]): result += 2.0
    return result


def apply_channel_confluence_vec(npz, n, is_long, score=None, current_price=None):
    """Array twin; absent 1m channel arrays retain the native no-bonus path."""
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if result.shape != (n,): raise ValueError("score must have shape (n,)")
    px = _col(npz, "close", n, 0.0) if current_price is None else _vec(current_price, n, 0.0)
    arrays = {key: _col(npz, key, n, 50.0 if key.startswith(("k_", "d_")) else 0.0) for key in (
        "k_3m", "d_3m", "k_15m", "k_1h", "d_1h", "k_4h", "d_4h",
        "dc_basis_1h", "dc_basis_15m", "dc_basis_4h", "dc_high_1m", "dc_low_1m",
        "dc_high_3m", "dc_low_3m", "dc_high_15m", "dc_low_15m", "dc_high_1h", "dc_low_1h", "dc_high_4h", "dc_low_4h")}
    raw_1h, raw_15m = np.asarray(npz.get("wt_cross_1h", np.zeros(n))), np.asarray(npz.get("wt_cross_15m", np.zeros(n)))
    if raw_1h.size != n: raw_1h = np.zeros(n, dtype=object)
    if raw_15m.size != n: raw_15m = np.zeros(n, dtype=object)
    sco1h, scu1h = _eq(raw_1h, "BULL"), _eq(raw_1h, "BEAR")
    sco15m, scu15m = _eq(raw_15m, "BULL"), _eq(raw_15m, "BEAR")
    k3, d3, k15 = arrays["k_3m"], arrays["d_3m"], arrays["k_15m"]
    k1, d1, k4, d4 = arrays["k_1h"], arrays["d_1h"], arrays["k_4h"], arrays["d_4h"]
    masks = {}
    def add(name, mask, amount):
        masks[name] = np.asarray(mask, dtype=bool)
        result[:] += masks[name].astype(float) * amount
    add("K3_TREND", (k3 > d3) if is_long else (k3 < d3), 2.0)
    add("K15_EXTREME_FAVORABLE", (k15 < 20) if is_long else (k15 > 80), 2.0)
    add("K15_EXTREME_OPPOSITE", (k15 > 80) if is_long else (k15 < 20), -3.0)
    add("K3_EXTREME_FAVORABLE", (k3 < 20) if is_long else (k3 > 80), 2.0)
    add("K3_EXTREME_OPPOSITE", (k3 > 80) if is_long else (k3 < 20), -4.0)
    add("K1_AGAINST_D", (k1 < d1) if is_long else (k1 > d1), -2.0)
    add("K4_AGAINST_D", (k4 < d4) if is_long else (k4 > d4), -1.0)
    add("K1_REVERSAL", ((k1 < 30) & (k1 > d1)) if is_long else ((k1 > 70) & (k1 < d1)), 2.0)
    b1, b15, b4 = arrays["dc_basis_1h"], arrays["dc_basis_15m"], arrays["dc_basis_4h"]
    strong = ((px > b1 * 1.04) & (px > b15)) if is_long else ((px < b15 * .96) & (px < b15))
    mild = ((px > b1 * 1.03) if is_long else (px < b1 * .93)) & ~strong
    add("DC_BASIS_STRONG", strong, 3.0)
    add("DC_BASIS_MILD", mild, 1.0)
    add("WT_1H_BASIS", (sco1h & (px > b1)) if is_long else (scu1h & (px < b1 * .93)), 1.0)
    add("WT_15M_BASIS", (sco15m & (px > b15)) if is_long else (scu15m & (px < b15)), 2.0)
    add("DC_1H_BASIS_SIDE", (px > b1) if is_long else (px < b1), 1.0)
    add("DC_4H_BASIS_OPPOSITE", (px < b4) if is_long else (px > b4), -2.0)
    dc1m = (arrays["dc_high_1m"] > 0) & (arrays["dc_low_1m"] > 0)
    dc1m &= (px >= arrays["dc_high_1m"]) if is_long else (px <= arrays["dc_low_1m"])
    add("DC_1M_BREAKOUT_IF_PRESENT", dc1m, .5)
    for tf, amount in (("3m", 1.0), ("15m", 1.5), ("1h", 2.0), ("4h", 2.0)):
        fire = (px >= arrays[f"dc_high_{tf}"]) if is_long else (px <= arrays[f"dc_low_{tf}"])
        add(f"DC_{tf.upper()}_BREAKOUT", fire, amount)
    return result, masks


def _eq(values, expected):
    return np.asarray([value == expected for value in values], dtype=bool)


def _col(npz, key, n, default):
    if key not in npz: return np.full(n, default, dtype=float)
    try:
        arr = np.asarray(npz[key], dtype=float).reshape(-1)
        return arr if arr.size == n else np.full(n, default, dtype=float)
    except (TypeError, ValueError): return np.full(n, default, dtype=float)


def _vec(value, n, default):
    try:
        arr = np.asarray(value, dtype=float).reshape(-1)
        return arr if arr.size == n else np.full(n, default, dtype=float)
    except (TypeError, ValueError): return np.full(n, default, dtype=float)
