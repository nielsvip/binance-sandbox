"""Causal signal scan for ez_manage's daily DC/BB break and crossback reversal.

This module emits per-side close/open intent masks. It does not inspect or
invent the opposite-side portfolio position; order pairing belongs to the
caller that has that state.
"""

import numpy as np


def _get(cfg, name, default):
    return getattr(cfg, name, default)


def _array(npz, key, n, default=0.0, fallback=None):
    source = npz.get(key)
    if source is None and fallback:
        source = npz.get(fallback)
    if source is None:
        return np.full(n, default, dtype=float)
    value = np.asarray(source, dtype=float)
    if value.ndim == 0:
        return np.full(n, float(value), dtype=float)
    if len(value) != n:
        return np.full(n, default, dtype=float)
    return value


def dc_bb_d_break_reverse_state_step(state, close, dc_high, dc_low, bb_upper,
                                    bb_lower, timestamp, hysteresis_pct,
                                    continuation_enabled, continuation_hours):
    """Advance the native last-break state once and return target/event/band."""
    state = state if state is not None else {}
    sym_state = state
    close = float(close)
    dc_high = float(dc_high)
    dc_low = float(dc_low)
    bb_upper = float(bb_upper)
    bb_lower = float(bb_lower)
    timestamp = float(timestamp)
    break_up = (dc_high > 0.0 and close > dc_high) or (
        bb_upper > 0.0 and close > bb_upper
    )
    break_down = (dc_low > 0.0 and close < dc_low) or (
        bb_lower > 0.0 and close < bb_lower
    )
    target, event, band = 0, "", ""
    if break_up:
        level = dc_high if dc_high > 0.0 and close > dc_high else bb_upper
        band = "DC" if dc_high > 0.0 and close > dc_high else "BB"
        previous_up_ts = float(sym_state.get("last_up_ts", 0.0) or 0.0)
        state.update(
            last_dir="UP",
            last_level=level,
            last_band=band,
            last_up_ts=previous_up_ts if previous_up_ts > 0.0 else timestamp,
            last_dn_ts=float(sym_state.get("last_dn_ts", 0.0) or 0.0),
        )
        target, event = 1, "BREAK_UP"
    elif break_down:
        level = dc_low if dc_low > 0.0 and close < dc_low else bb_lower
        band = "DC" if dc_low > 0.0 and close < dc_low else "BB"
        previous_dn_ts = float(sym_state.get("last_dn_ts", 0.0) or 0.0)
        state.update(
            last_dir="DOWN",
            last_level=level,
            last_band=band,
            last_up_ts=float(sym_state.get("last_up_ts", 0.0) or 0.0),
            last_dn_ts=previous_dn_ts if previous_dn_ts > 0.0 else timestamp,
        )
        target, event = -1, "BREAK_DOWN"
    else:
        direction = sym_state.get("last_dir")
        level = float(sym_state.get("last_level", 0.0) or 0.0)
        band = str(sym_state.get("last_band", "DC") or "DC")
        hysteresis = float(hysteresis_pct) / 100.0
        if direction == "UP" and level > 0.0 and close < level * (1.0 - hysteresis):
            last_up_ts = float(sym_state.get("last_up_ts", 0.0) or 0.0)
            elapsed_hours = (timestamp - last_up_ts) / 3600.0 if last_up_ts > 0.0 else 999.0
            if not (continuation_enabled and last_up_ts > 0.0 and elapsed_hours < continuation_hours):
                target, event = -1, "CROSSBACK_TO_SHORT"
                state.update(
                    last_dir="DOWN",
                    last_level=level,
                    last_band=band,
                    last_up_ts=last_up_ts,
                    last_dn_ts=timestamp,
                )
        elif direction == "DOWN" and level > 0.0 and close > level * (1.0 + hysteresis):
            last_dn_ts = float(sym_state.get("last_dn_ts", 0.0) or 0.0)
            elapsed_hours = (timestamp - last_dn_ts) / 3600.0 if last_dn_ts > 0.0 else 999.0
            if not (continuation_enabled and last_dn_ts > 0.0 and elapsed_hours < continuation_hours):
                target, event = 1, "CROSSBACK_TO_LONG"
                state.update(
                    last_dir="UP",
                    last_level=level,
                    last_band=band,
                    last_up_ts=timestamp,
                    last_dn_ts=last_dn_ts,
                )
    if target and not band:
        band = str(state.get("last_band", "DC") or "DC")
    return target, event, band


def dc_bb_d_break_reverse_scan(npz, n, cfg):
    """Return causal target, close, and open masks for each 15m decision bar."""
    n = int(n)
    names = (
        "target_long",
        "target_short",
        "close_long",
        "close_short",
        "open_long",
        "open_short",
        "event",
        "band",
    )
    result = {
        name: np.zeros(n, dtype=bool)
        for name in names[:6]
    }
    result["event"] = np.full(n, "", dtype=object)
    result["band"] = np.full(n, "", dtype=object)
    if n <= 0 or not bool(_get(cfg, "DC_BB_D_BREAK_REVERSE_ENABLED", True)):
        return result
    if "timestamps" not in npz:
        return result
    ts = _array(npz, "timestamps", n, np.nan)
    if not np.isfinite(ts).all():
        return result
    close = _array(npz, "close", n, np.nan)
    if not np.isfinite(close).all():
        return result
    dc_high = _array(npz, "dc_high_D_prev", n, 0.0, "dc_high_D")
    dc_low = _array(npz, "dc_low_D_prev", n, 0.0, "dc_low_D")
    bb_upper = _array(npz, "bb_upper_D", n, 0.0)
    bb_lower = _array(npz, "bb_lower_D", n, 0.0)
    rsi_4h = _array(npz, "rsi_4h", n, 50.0)
    rsi_1h = _array(npz, "rsi_1h", n, 50.0)
    bb_pct_b_4h = _array(npz, "bb_pct_b_4h", n, 0.5)
    hyst = float(_get(cfg, "DC_BB_CROSSBACK_HYSTERESIS_PCT", 2.0))
    continuation_on = bool(_get(cfg, "BB_BREAKOUT_CONT_ENABLED", True))
    continuation_hours = float(_get(cfg, "BB_BREAKOUT_CONT_HOURS", 72.0))
    parabolic_on = bool(_get(cfg, "PARABOLIC_PROTECTION_ENABLED", True))
    rsi_4h_up = float(_get(cfg, "PARABOLIC_RSI_4H_MIN", 70.0))
    rsi_1h_up = float(_get(cfg, "PARABOLIC_RSI_1H_MIN", 65.0))
    bb_up = float(_get(cfg, "PARABOLIC_BB_PCT_B_4H_MIN", 0.70))
    rsi_4h_down = float(_get(cfg, "PARABOLIC_RSI_4H_MAX", 30.0))
    rsi_1h_down = float(_get(cfg, "PARABOLIC_RSI_1H_MAX", 35.0))
    bb_down = float(_get(cfg, "PARABOLIC_BB_PCT_B_4H_MAX", 0.10))
    state = {}
    for i in range(n):
        target, event, band = dc_bb_d_break_reverse_state_step(
            state,
            close[i],
            dc_high[i],
            dc_low[i],
            bb_upper[i],
            bb_lower[i],
            ts[i],
            hyst,
            continuation_on,
            continuation_hours,
        )
        if not target:
            continue
        parabolic_up = (
            parabolic_on
            and rsi_4h[i] >= rsi_4h_up
            and rsi_1h[i] >= rsi_1h_up
            and bb_pct_b_4h[i] >= bb_up
        )
        parabolic_down = (
            parabolic_on
            and rsi_4h[i] <= rsi_4h_down
            and rsi_1h[i] <= rsi_1h_down
            and bb_pct_b_4h[i] <= bb_down
        )
        if target > 0:
            result["target_long"][i] = True
            if not parabolic_down:
                result["close_short"][i] = True
                result["open_long"][i] = True
        else:
            result["target_short"][i] = True
            if not parabolic_up:
                result["close_long"][i] = True
                result["open_short"][i] = True
        result["event"][i] = event
        result["band"][i] = band
    return result
