"""
vec_paths/scalp_v2.py — SCALP_V2 (HTF Breakout Scalper) path for vec_engine_v1.

Mirrors htf_breakout_scalper.py logic translated for the NPZ store interface.
Per CLAUDE.md: this is a Tier-1 shortlist tool only.  Any result must be
confirmed via backtest_v8_engine.py before touching live config.

SOURCE OF TRUTH: htf_breakout_scalper.py + ez_positions_quick.py:14672
LIVE TAG PREFIX: SCALP_V2_OPEN_
DEFAULT: OFF (SCALP_MODE=False in config.py)

VecConfig fields consumed:
    SCALP_MODE                — master gate (default False)
    SCALP_V2_VARIANT          — exit variant (default V1_WT_CONFIRM)
    SCALP_V2_DC_HTF_LIST      — TF list for DC breakout gate (default ["15m","1h"])
    SCALP_V2_DC_HTF_REQUIRE_ALL — AND vs OR across TFs (default True)
    SCALP_V2_ENTRY_MODE       — "breakout" | "pullback" (default "breakout")
    SCALP_V2_MAX_HOLD_MINUTES — safety net (default 15.0)
    SCALP_V2_REDZONE_EXIT     — secondary exit (default True)
    SCALP_V2_REDZONE_K_THRESHOLD — K threshold (default 90)
    SCALP_V2_LH_LL_EXIT       — secondary LH/LL exit (default True)
    SCALP_V2_LH_LL_TF         — TF for LH/LL (default "15m")
    SCALP_V2_MAX_CONCURRENT   — concurrent cap (default 5)
"""
from __future__ import annotations

from typing import Optional, Dict, Tuple, Any

_REASON_PREFIX = "SCALP_V2_OPEN_"
_EXIT_PREFIX = "SCALP_V2_EXIT_"


def _sf(store: Any, key: str, idx: int, default: float = 0.0) -> float:
    """Safe float fetch from NPZ store."""
    try:
        return store.f(key, idx, default)
    except Exception:
        return default


def _sb(store: Any, key: str, idx: int, default: bool = False) -> bool:
    """Safe bool fetch from NPZ store."""
    try:
        return store.b(key, idx, default)
    except Exception:
        return default


def _htf_breakout_ok(store: Any, bar_idx: int, side: str, tfs: list, require_all: bool) -> Tuple[bool, str]:
    """DC breakout gate across listed TFs.

    LONG: price > dc_high_<tf> (prev bar value — use dc_high_<tf> as proxy for prev).
    SHORT: price < dc_low_<tf>.
    """
    price = store.price(bar_idx)
    if price <= 0:
        return False, "NO_PRICE"
    results = []
    detail_parts = [f"px={price:.6f}"]
    for tf in tfs:
        if side == "LONG":
            level = _sf(store, f"dc_high_{tf}", bar_idx, 0.0)
            ok = level > 0 and price > level
        else:
            level = _sf(store, f"dc_low_{tf}", bar_idx, 0.0)
            ok = level > 0 and price < level
        results.append(ok)
        detail_parts.append(f"dc{tf}={level:.6f}({'OK' if ok else 'X'})")
    detail = " ".join(detail_parts)
    if require_all:
        return all(results), detail
    return any(results), detail


def _htf_pullback_ok(store: Any, bar_idx: int, side: str) -> Tuple[bool, str]:
    """Pullback-to-trend entry (price near DC opposite edge, stoch bounce).

    Mirrors htf_breakout_scalper._htf_pullback_ok for NPZ arrays.
    """
    price = store.price(bar_idx)
    dc_high_15m = _sf(store, "dc_high_15m", bar_idx)
    dc_low_15m = _sf(store, "dc_low_15m", bar_idx)
    if dc_high_15m <= 0 or dc_low_15m <= 0:
        return False, "NO_DC_15M"
    dc_range = max(dc_high_15m - dc_low_15m, 1e-9)
    dc_pos = (price - dc_low_15m) / dc_range
    wt1_1h = _sf(store, "wt1_1h", bar_idx)
    wt2_1h = _sf(store, "wt2_1h", bar_idx)
    wt1_4h = _sf(store, "wt1_4h", bar_idx)
    wt2_4h = _sf(store, "wt2_4h", bar_idx)
    wt_vel_1h = _sf(store, "wt_velocity_1h", bar_idx)
    wt_vel_3m = _sf(store, "wt_velocity_3m", bar_idx)
    k_3m = _sf(store, "stoch_k_3m", bar_idx, 50.0)
    k_3m_prev = _sf(store, "stoch_k_3m", max(0, bar_idx - 1), k_3m)
    d_3m = _sf(store, "stoch_d_3m", bar_idx, 50.0)
    if side == "LONG":
        htf_up = (wt1_1h > wt2_1h) and (wt1_4h > wt2_4h) and (wt_vel_1h > -1.0)
        if not htf_up:
            return False, "HTF_NOT_UP"
        if dc_pos >= 0.25:
            return False, f"NOT_AT_BOTTOM dc_pos={dc_pos:.2f}"
        stoch_bounce = (k_3m < 30) and (k_3m > k_3m_prev) and (k_3m > d_3m)
        if not stoch_bounce:
            return False, f"NO_STOCH_BOUNCE k3m={k_3m:.0f}"
        if wt_vel_3m <= -0.5:
            return False, f"VEL_DROPPING vel3m={wt_vel_3m:.2f}"
        return True, f"PULLBACK_LONG dc_pos={dc_pos:.2f}_k3m={k_3m:.0f}"
    else:
        htf_down = (wt1_1h < wt2_1h) and (wt1_4h < wt2_4h) and (wt_vel_1h < 1.0)
        if not htf_down:
            return False, "HTF_NOT_DOWN"
        if dc_pos <= 0.75:
            return False, f"NOT_AT_TOP dc_pos={dc_pos:.2f}"
        stoch_roll = (k_3m > 70) and (k_3m < k_3m_prev) and (k_3m < d_3m)
        if not stoch_roll:
            return False, f"NO_STOCH_ROLL k3m={k_3m:.0f}"
        if wt_vel_3m >= 0.5:
            return False, f"VEL_RISING vel3m={wt_vel_3m:.2f}"
        return True, f"PULLBACK_SHORT dc_pos={dc_pos:.2f}_k3m={k_3m:.0f}"


def check_scalp_v2_entry(store: Any, bar_idx: int, side: str, cfg: Any) -> Optional[Dict]:
    """Check SCALP_V2 entry at bar_idx.

    Returns {"side": side, "reason": str} or None.

    Caller MUST gate on cfg.SCALP_MODE before calling — this function also
    checks it internally for safety.
    """
    if not getattr(cfg, "SCALP_MODE", False):
        return None

    price = store.price(bar_idx)
    if price <= 0:
        return None

    entry_mode = str(getattr(cfg, "SCALP_V2_ENTRY_MODE", "breakout"))
    if entry_mode == "pullback":
        ok, detail = _htf_pullback_ok(store, bar_idx, side)
    else:
        tfs = list(getattr(cfg, "SCALP_V2_DC_HTF_LIST", ["15m", "1h"]))
        require_all = bool(getattr(cfg, "SCALP_V2_DC_HTF_REQUIRE_ALL", True))
        ok, detail = _htf_breakout_ok(store, bar_idx, side, tfs, require_all)

    if not ok:
        return None

    variant = str(getattr(cfg, "SCALP_V2_VARIANT", "V1_WT_CONFIRM"))
    reason = f"{_REASON_PREFIX}{variant}_{entry_mode}_{detail}"
    return {"side": side, "reason": reason}


def check_scalp_v2_exit(store: Any, bar_idx: int, pos_state: Any, side: str, cfg: Any) -> Optional[Dict]:
    """Check SCALP_V2 exit at bar_idx.

    pos_state must have:
        .open (bool)
        .entry_price (float)
        .open_bar (int) — bar_idx when position was opened (for max-hold check)
        .reason (str) — tagged with SCALP_V2_OPEN_ to identify V2 positions

    Returns {"reason": str} or None.
    """
    if not getattr(cfg, "SCALP_MODE", False):
        return None
    if not getattr(pos_state, "open", False):
        return None
    reason_tag = str(getattr(pos_state, "reason", "") or "")
    if _REASON_PREFIX not in reason_tag:
        return None

    price = store.price(bar_idx)
    if price <= 0:
        return None

    is_long = (side == "LONG")

    # 1. Universal technical stop (3m low/high prev)
    low_3m_prev = _sf(store, "low_3m", max(0, bar_idx - 1))
    high_3m_prev = _sf(store, "high_3m", max(0, bar_idx - 1))
    if is_long and low_3m_prev > 0 and price <= low_3m_prev:
        return {"reason": f"{_EXIT_PREFIX}STOP_LOW_3M_PREV px={price:.6f}<={low_3m_prev:.6f}"}
    if not is_long and high_3m_prev > 0 and price >= high_3m_prev:
        return {"reason": f"{_EXIT_PREFIX}STOP_HIGH_3M_PREV px={price:.6f}>={high_3m_prev:.6f}"}

    # 2. Variant-specific exit
    variant = str(getattr(cfg, "SCALP_V2_VARIANT", "V1_WT_CONFIRM"))
    exit_fired = False
    detail = ""

    if variant == "V1_WT_CONFIRM":
        wt1_3m = _sf(store, "wt1_3m", bar_idx)
        wt2_3m = _sf(store, "wt2_3m", bar_idx)
        wt1_prev = _sf(store, "wt1_3m", max(0, bar_idx - 1), wt1_3m)
        wt2_prev = _sf(store, "wt2_3m", max(0, bar_idx - 1), wt2_3m)
        if is_long and (wt1_prev > wt2_prev) and (wt1_3m < wt2_3m):
            exit_fired, detail = True, f"WT_CROSS_BEAR_3m wt1={wt1_3m:.1f}<wt2={wt2_3m:.1f}"
        elif not is_long and (wt1_prev < wt2_prev) and (wt1_3m > wt2_3m):
            exit_fired, detail = True, f"WT_CROSS_BULL_3m wt1={wt1_3m:.1f}>wt2={wt2_3m:.1f}"

    elif variant in ("V2_LH_LL_3M", "V3_LH_LL_1M"):
        h_now = _sf(store, "high_3m", bar_idx)
        h_prev = _sf(store, "high_3m", max(0, bar_idx - 1))
        l_now = _sf(store, "low_3m", bar_idx)
        l_prev = _sf(store, "low_3m", max(0, bar_idx - 1))
        if h_now > 0 and h_prev > 0 and l_now > 0 and l_prev > 0:
            if is_long and h_now < h_prev and l_now <= l_prev:
                exit_fired, detail = True, f"LH_3m h={h_now:.6f}<{h_prev:.6f}"
            elif not is_long and l_now > l_prev and h_now >= h_prev:
                exit_fired, detail = True, f"HL_3m l={l_now:.6f}>{l_prev:.6f}"

    elif variant in ("V4_HA_FLIP", "V5_BREAK_HIGH_REENTRY"):
        ha_raw = _sf(store, "ha_3m", bar_idx, 0.0)
        is_red = ha_raw < 0
        is_green = ha_raw > 0
        if is_long and is_red:
            exit_fired, detail = True, "HA_3m_RED"
        elif not is_long and is_green:
            exit_fired, detail = True, "HA_3m_GREEN"

    elif variant == "V6_COMBINED_WT_HA":
        wt1_3m = _sf(store, "wt1_3m", bar_idx)
        wt2_3m = _sf(store, "wt2_3m", bar_idx)
        wt1_prev = _sf(store, "wt1_3m", max(0, bar_idx - 1), wt1_3m)
        wt2_prev = _sf(store, "wt2_3m", max(0, bar_idx - 1), wt2_3m)
        ha_raw = _sf(store, "ha_3m", bar_idx, 0.0)
        wt_fire = (is_long and wt1_prev > wt2_prev and wt1_3m < wt2_3m) or \
                  (not is_long and wt1_prev < wt2_prev and wt1_3m > wt2_3m)
        ha_fire = (is_long and ha_raw < 0) or (not is_long and ha_raw > 0)
        if wt_fire and ha_fire:
            exit_fired, detail = True, f"WT+HA wt1={wt1_3m:.1f} ha={ha_raw:.0f}"

    elif variant == "V7_TIGHT_TRAILING":
        close_3m = _sf(store, "close", bar_idx, price)
        if is_long and low_3m_prev > 0 and close_3m < low_3m_prev:
            exit_fired, detail = True, f"TRAIL close={close_3m:.6f}<low_prev={low_3m_prev:.6f}"
        elif not is_long and high_3m_prev > 0 and close_3m > high_3m_prev:
            exit_fired, detail = True, f"TRAIL close={close_3m:.6f}>high_prev={high_3m_prev:.6f}"

    elif variant == "V8_HTF_RECLAIM":
        tfs = list(getattr(cfg, "SCALP_V2_DC_HTF_LIST", ["15m", "1h"]))
        tf = tfs[0] if tfs else "15m"
        if is_long:
            level = _sf(store, f"dc_high_{tf}", bar_idx)
            if level > 0 and price <= level:
                exit_fired, detail = True, f"HTF_RECLAIM_LONG px={price:.6f}<=dc_high_{tf}={level:.6f}"
        else:
            level = _sf(store, f"dc_low_{tf}", bar_idx)
            if level > 0 and price >= level:
                exit_fired, detail = True, f"HTF_RECLAIM_SHORT px={price:.6f}>=dc_low_{tf}={level:.6f}"

    # 2b. Secondary exits
    if not exit_fired and getattr(cfg, "SCALP_V2_REDZONE_EXIT", True):
        k_3m = _sf(store, "stoch_k_3m", bar_idx, 50.0)
        k_3m_prev = _sf(store, "stoch_k_3m", max(0, bar_idx - 1), k_3m)
        k_thr = int(getattr(cfg, "SCALP_V2_REDZONE_K_THRESHOLD", 90))
        mirror = 100 - k_thr
        if is_long and k_3m_prev >= k_thr and k_3m < k_thr:
            exit_fired, detail = True, f"REDZONE_K{k_thr} k={k_3m:.0f}<{k_thr} prev={k_3m_prev:.0f}"
        elif not is_long and k_3m_prev <= mirror and k_3m > mirror:
            exit_fired, detail = True, f"REDZONE_K{k_thr} k={k_3m:.0f}>{mirror} prev={k_3m_prev:.0f}"

    if not exit_fired and getattr(cfg, "SCALP_V2_LH_LL_EXIT", True):
        _lh_tf = str(getattr(cfg, "SCALP_V2_LH_LL_TF", "15m"))
        h_now = _sf(store, f"high_{_lh_tf}", bar_idx)
        h_prev = _sf(store, f"high_{_lh_tf}", max(0, bar_idx - 1))
        l_now = _sf(store, f"low_{_lh_tf}", bar_idx)
        l_prev = _sf(store, f"low_{_lh_tf}", max(0, bar_idx - 1))
        if h_now > 0 and h_prev > 0 and l_now > 0 and l_prev > 0:
            if is_long and h_now < h_prev and l_now <= l_prev:
                exit_fired, detail = True, f"LH_LL_{_lh_tf} h={h_now:.4f}<{h_prev:.4f}"
            elif not is_long and l_now > l_prev and h_now >= h_prev:
                exit_fired, detail = True, f"HL_LH_{_lh_tf} l={l_now:.4f}>{l_prev:.4f}"

    # 3. Max-hold safety net (bar count proxy — NPZ has no timestamps in this path)
    if not exit_fired:
        max_hold_bars = int(getattr(cfg, "SCALP_V2_MAX_HOLD_MINUTES", 15.0) / 3.0)
        open_bar = int(getattr(pos_state, "open_bar", 0) or 0)
        if open_bar > 0 and (bar_idx - open_bar) >= max_hold_bars:
            exit_fired, detail = True, f"MAX_HOLD bars={bar_idx - open_bar}>={max_hold_bars}"

    if exit_fired:
        return {"reason": f"{_EXIT_PREFIX}{variant}_{detail}"}
    return None
