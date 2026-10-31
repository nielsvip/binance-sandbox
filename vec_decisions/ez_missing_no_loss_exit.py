"""Vector twin for the critical near-zero NO_LOSS_EXIT rule in ez_manage."""

import numpy as np


def no_loss_critical_exit_core(gain_pct, current_price, is_long, indicators):
    """Apply the live near-zero, adverse-stoch, 15m-structure predicate."""
    gain_pct = np.atleast_1d(np.asarray(gain_pct, dtype=float))
    current_price = np.broadcast_to(np.atleast_1d(np.asarray(current_price, dtype=float)), gain_pct.shape)
    size = gain_pct.size

    def values(key, default=np.nan):
        value = indicators.get(key)
        if value is None:
            return np.full(size, default, dtype=float)
        value = np.atleast_1d(np.asarray(value, dtype=float))
        if value.size == 1 and size != 1:
            value = np.full(size, value[0], dtype=float)
        if value.size != size:
            return np.full(size, default, dtype=float)
        return value

    dc = values("dc_low_15m" if is_long else "dc_high_15m")
    k = values("k_15m")
    d = values("d_15m")
    k_fallback = values("stoch_k_15m")
    d_fallback = values("stoch_d_15m")
    k = np.where(np.isfinite(k), k, k_fallback)
    d = np.where(np.isfinite(d), d, d_fallback)
    if is_long:
        k_against = k < d
        under_dc = (dc > 0.0) & (current_price < dc)
        structure_break = (values("high_15m") > 0.0) & (values("high_15m_prev") > 0.0) & (values("high_15m") < values("high_15m_prev"))
    else:
        k_against = k > d
        under_dc = (dc > 0.0) & (current_price > dc)
        structure_break = (values("low_15m") > 0.0) & (values("low_15m_prev") > 0.0) & (values("low_15m") > values("low_15m_prev"))
    return (gain_pct > 0.0) & (gain_pct < 0.03) & under_dc & k_against & structure_break


def no_loss_critical_exit_mask(npz, n, is_long, cfg, gain_pct, enabled_name="NO_LOSS_EXIT_CRITICAL_ENABLED"):
    """Return the named switch's per-bar exit mask; missing decision inputs do not fire."""
    if not bool(getattr(cfg, enabled_name)):
        return np.zeros(n, dtype=bool)
    required = ("close", "dc_low_15m" if is_long else "dc_high_15m", "high_15m", "high_15m_prev") if is_long else ("close", "dc_high_15m", "low_15m", "low_15m_prev")
    if not any(key in npz for key in ("k_15m", "stoch_k_15m")) or not any(key in npz for key in ("d_15m", "stoch_d_15m")):
        return np.zeros(n, dtype=bool)
    if any(key not in npz for key in required):
        return np.zeros(n, dtype=bool)
    indicators = {key: np.asarray(npz[key]) for key in required if key != "close"}
    indicators["k_15m"] = np.asarray(npz["k_15m"] if "k_15m" in npz else npz["stoch_k_15m"])
    indicators["d_15m"] = np.asarray(npz["d_15m"] if "d_15m" in npz else npz["stoch_d_15m"])
    return no_loss_critical_exit_core(gain_pct, np.asarray(npz["close"]), is_long, indicators)


def no_loss_critical_exit_live_adapter(indicators, current_price, is_long, gain_pct):
    """Scalar adapter with 15m K/D replacing the live 1m/3m inputs."""
    data = dict(indicators or {})
    data["k_15m"] = data.get("k_15m", data.get("stoch_k_15m"))
    data["d_15m"] = data.get("d_15m", data.get("stoch_d_15m"))
    fired = bool(no_loss_critical_exit_core(gain_pct, current_price, is_long, data)[0])
    if fired:
        return True, f"NO_LOSS_EXIT_CRITICAL_gain={float(gain_pct):.3f}%_ALL_TF_AGAINST"
    if gain_pct <= 0.0:
        return False, "ALREADY_NEGATIVE"
    if gain_pct > 0.5:
        return False, "HEALTHY_GAIN"
    if gain_pct >= 0.03:
        return False, "HOLD"
    dc = float(data.get("dc_low_15m" if is_long else "dc_high_15m", 0.0) or 0.0)
    px = float(data.get("current_price") or current_price or data.get("close") or 0.0)
    under_dc = dc > 0.0 and px > 0.0 and (px < dc if is_long else px > dc)
    if not under_dc:
        return False, f"HOLD_NEED_1H_OR_UNDER_DC_g{gain_pct:.3f}"
    return False, "HOLD"
