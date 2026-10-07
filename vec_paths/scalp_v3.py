"""
vec_paths/scalp_v3.py — SCALP_V3 (ultra-short bar-based scalper) for vec_engine_v1.

Mirrors scalp_v3_live.py logic for the NPZ store interface.
Per CLAUDE.md: Tier-1 shortlist tool only.

SOURCE OF TRUTH: scalp_v3_live.py + ez_positions_quick.py:14709
LIVE TAG PREFIX: SCALP_V3_OPEN_ (current) / QUICK_SCALP_V3_OPEN_ (historical pre-2026-04-28)
DEFAULT: OFF (SCALP_V3_ENABLED=False in VecConfig)

VecConfig fields consumed (all default OFF / conservative):
    SCALP_V3_ENABLED              — master gate (default False)
    SCALP_V3_MAX_CONCURRENT       — position cap (default 8)
    SCALP_V3_POSITION_CAP_USD     — USD cap per position (default 20.0)

    Entry path switches (mirrors scalp_v3_live.py):
    SCALP_V3_ENTRY_TREND_ENABLED  — standard trend entry (default True)
    SCALP_V3_ENTRY_BAR_BREAK_ENABLED
    SCALP_V3_ENTRY_PULLBACK_ENABLED
    SCALP_V3_ENTRY_DC_BREAK_ENABLED
    SCALP_V3_ENTRY_WT_CROSS_ENABLED
    SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED
    SCALP_V3_ENTRY_STDEV_ENABLED
    SCALP_V3_SIDE_MODE            — "BOTH"|"LONG_ONLY"|"SHORT_ONLY" (default "BOTH")

    K-window (default match live code at time of writing):
    SCALP_V3_K_FRESH_LO / MID_LO / MID_HI / HI
    SCALP_V3_LONG_K_RISE_MIN / MAX
    SCALP_V3_SHORT_K_FALL_MIN / MAX

    Exit path switches:
    SCALP_V3_EXIT_BAR_REVERSAL_ENABLED  (default True)
    SCALP_V3_EXIT_WT_FLIP_ENABLED       (default True)
    SCALP_V3_EXIT_K_CROSS_ENABLED       (default True)
    SCALP_V3_EXIT_REQUIRE_N_SIGNALS     (default 1)
    SCALP_V3_EXIT_PROFIT_ONLY           (default False)
    SCALP_V3_MAX_HOLD_MIN               (default 5.0 → bars in 3m base TF)

NPZ approximation notes:
    - wt1_3m rising/falling is used as "bar rising/falling" proxy
      (live uses real high_3m/low_3m OHLC; backtest arrays HAVE these).
    - When high_3m fields are present in NPZ, they are used directly
      (same logic as scalp_v3_live.py backtest path).
    - session_block hours are ignored in backtest (no UTC clock).
"""
from __future__ import annotations

from typing import Optional, Dict, Any

_REASON_PREFIX = "SCALP_V3_OPEN_"
_EXIT_PREFIX = "SCALP_V3_CLOSE_"


def _sf(store: Any, key: str, idx: int, default: float = 0.0) -> float:
    try:
        return store.f(key, idx, default)
    except Exception:
        return default


def _prev_idx(bar_idx: int) -> int:
    return max(0, bar_idx - 1)


def check_scalp_v3_entry(store: Any, bar_idx: int, side: str, cfg: Any) -> Optional[Dict]:
    """Check SCALP_V3 entry at bar_idx.

    Returns {"side": side, "reason": str} or None.
    """
    if not getattr(cfg, "SCALP_V3_ENABLED", False):
        return None
    price = store.price(bar_idx)
    if price <= 0:
        return None

    side = side.upper()
    side_mode = str(getattr(cfg, "SCALP_V3_SIDE_MODE", "BOTH")).upper()
    allow_long = (side == "LONG") and (side_mode in ("LONG_ONLY", "BOTH"))
    allow_short = (side == "SHORT") and (side_mode in ("SHORT_ONLY", "BOTH"))
    if not allow_long and not allow_short:
        return None

    k_3m = _sf(store, "stoch_k_3m", bar_idx, 50.0)
    k_3m_prev = _sf(store, "stoch_k_3m", _prev_idx(bar_idx), k_3m)
    k_15m = _sf(store, "stoch_k_15m", bar_idx, 50.0)
    k_1h = _sf(store, "stoch_k_1h", bar_idx, 50.0)
    wt1_3m = _sf(store, "wt1_3m", bar_idx)
    wt2_3m = _sf(store, "wt2_3m", bar_idx)
    wt1_3m_prev = _sf(store, "wt1_3m", _prev_idx(bar_idx), wt1_3m)
    wt2_3m_prev = _sf(store, "wt2_3m", _prev_idx(bar_idx), wt2_3m)
    wt_vel_3m = _sf(store, "wt_velocity_3m", bar_idx)

    # Bar direction — use real OHLC when available (backtest path)
    high_3m = _sf(store, "high_3m", bar_idx)
    low_3m = _sf(store, "low_3m", bar_idx)
    high_3m_prev = _sf(store, "high_3m", _prev_idx(bar_idx))
    low_3m_prev = _sf(store, "low_3m", _prev_idx(bar_idx))
    if high_3m_prev > 0 and low_3m_prev > 0 and high_3m > 0 and low_3m > 0:
        bar_rising = (high_3m > high_3m_prev) and (low_3m > low_3m_prev)
        bar_falling = (high_3m < high_3m_prev) and (low_3m < low_3m_prev)
    else:
        bar_rising = (wt1_3m > wt1_3m_prev) and (wt_vel_3m > 0)
        bar_falling = (wt1_3m < wt1_3m_prev) and (wt_vel_3m < 0)

    # K-freshness window
    _k_lo = float(getattr(cfg, "SCALP_V3_K_FRESH_LO", 25.0))
    _k_mid_lo = float(getattr(cfg, "SCALP_V3_K_FRESH_MID_LO", 50.0))
    _k_mid_hi = float(getattr(cfg, "SCALP_V3_K_FRESH_MID_HI", 50.0))
    _k_hi = float(getattr(cfg, "SCALP_V3_K_FRESH_HI", 75.0))
    _long_k_min = float(getattr(cfg, "SCALP_V3_LONG_K_RISE_MIN", _k_mid_lo))
    _long_k_max = float(getattr(cfg, "SCALP_V3_LONG_K_RISE_MAX", _k_hi))
    _short_k_min = float(getattr(cfg, "SCALP_V3_SHORT_K_FALL_MIN", _k_lo))
    _short_k_max = float(getattr(cfg, "SCALP_V3_SHORT_K_FALL_MAX", _k_mid_hi))
    k_rising = (k_3m > k_3m_prev) and (_long_k_min <= k_3m <= _long_k_max)
    k_falling = (k_3m < k_3m_prev) and (_short_k_min <= k_3m <= _short_k_max)

    wt_bull = wt1_3m > wt2_3m
    wt_bear = wt1_3m < wt2_3m
    htf_bull = True
    htf_bear = True

    # ── ENTRY PATH 0: BAR_BREAK ──────────────────────────────────────────────
    if bool(getattr(cfg, "SCALP_V3_ENTRY_BAR_BREAK_ENABLED", False)):
        _bb_vel_min = float(getattr(cfg, "SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN", 1.0))
        _bb_vel_long = float(getattr(cfg, "SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_LONG", _bb_vel_min))
        _bb_vel_short = float(getattr(cfg, "SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_SHORT", _bb_vel_min))
        if allow_long and bar_rising and wt_vel_3m > _bb_vel_long:
            return {"side": "LONG", "reason": f"{_REASON_PREFIX}LONG_BAR_BREAK_p{price:.4g}_wt3m{wt1_3m:.1f}>{wt1_3m_prev:.1f}_v3m{wt_vel_3m:+.1f}_k3m{k_3m:.0f}"}
        if allow_short and bar_falling and wt_vel_3m < -_bb_vel_short:
            return {"side": "SHORT", "reason": f"{_REASON_PREFIX}SHORT_BAR_BREAK_p{price:.4g}_wt3m{wt1_3m:.1f}<{wt1_3m_prev:.1f}_v3m{wt_vel_3m:+.1f}_k3m{k_3m:.0f}"}

    # ── ENTRY PATH 1: TREND ──────────────────────────────────────────────────
    if bool(getattr(cfg, "SCALP_V3_ENTRY_TREND_ENABLED", True)):
        if allow_long and bar_rising and k_rising and wt_bull and htf_bull:
            return {"side": "LONG", "reason": f"{_REASON_PREFIX}LONG_TREND_k3m{k_3m:.0f}>{k_3m_prev:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
        if allow_short and bar_falling and k_falling and wt_bear and htf_bear:
            return {"side": "SHORT", "reason": f"{_REASON_PREFIX}SHORT_TREND_k3m{k_3m:.0f}<{k_3m_prev:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}

    # ── ENTRY PATH 2: PULLBACK ───────────────────────────────────────────────
    wt1_1h = _sf(store, "wt1_1h", bar_idx)
    wt2_1h = _sf(store, "wt2_1h", bar_idx)
    htf_bullish_loose = (k_1h >= 50) or (wt1_1h > wt2_1h)
    htf_bearish_loose = (k_1h <= 50) or (wt1_1h < wt2_1h)
    bar_pulling_down = (high_3m < high_3m_prev) or (low_3m < low_3m_prev) if high_3m_prev > 0 else not bar_rising
    bar_pulling_up = (high_3m > high_3m_prev) or (low_3m > low_3m_prev) if high_3m_prev > 0 else not bar_falling
    if bool(getattr(cfg, "SCALP_V3_ENTRY_PULLBACK_ENABLED", False)):
        if allow_long and htf_bullish_loose and bar_pulling_down and (k_3m <= 35) and (k_3m > k_3m_prev) and wt_bull:
            return {"side": "LONG", "reason": f"{_REASON_PREFIX}LONG_PULLBACK_k3m{k_3m:.0f}>{k_3m_prev:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
        if allow_short and htf_bearish_loose and bar_pulling_up and (k_3m >= 65) and (k_3m < k_3m_prev) and wt_bear:
            return {"side": "SHORT", "reason": f"{_REASON_PREFIX}SHORT_PULLBACK_k3m{k_3m:.0f}<{k_3m_prev:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}

    # ── ENTRY PATH 3: DC_BREAK ───────────────────────────────────────────────
    if bool(getattr(cfg, "SCALP_V3_ENTRY_DC_BREAK_ENABLED", False)):
        dc_high_15m = _sf(store, "dc_high_15m", bar_idx)
        dc_low_15m = _sf(store, "dc_low_15m", bar_idx)
        if allow_long and dc_high_15m > 0 and price > dc_high_15m and k_1h > 30 and wt_bull:
            return {"side": "LONG", "reason": f"{_REASON_PREFIX}LONG_DCBREAK_p{price:.4g}>dc15m{dc_high_15m:.4g}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
        if allow_short and dc_low_15m > 0 and price < dc_low_15m and k_1h < 70 and wt_bear:
            return {"side": "SHORT", "reason": f"{_REASON_PREFIX}SHORT_DCBREAK_p{price:.4g}<dc15m{dc_low_15m:.4g}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}

    # ── ENTRY PATH 4: WT_CROSS ───────────────────────────────────────────────
    if bool(getattr(cfg, "SCALP_V3_ENTRY_WT_CROSS_ENABLED", False)):
        if allow_long and wt_bull and wt_vel_3m > 0 and k_3m >= 40 and htf_bullish_loose:
            return {"side": "LONG", "reason": f"{_REASON_PREFIX}LONG_WTCROSS_v3m{wt_vel_3m:.2f}_k3m{k_3m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
        if allow_short and wt_bear and wt_vel_3m < 0 and k_3m <= 60 and htf_bearish_loose:
            return {"side": "SHORT", "reason": f"{_REASON_PREFIX}SHORT_WTCROSS_v3m{wt_vel_3m:.2f}_k3m{k_3m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}

    # ── ENTRY PATH 5: STOCH_BOUNCE ───────────────────────────────────────────
    if bool(getattr(cfg, "SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED", False)):
        d_3m = _sf(store, "stoch_d_3m", bar_idx, 50.0)
        if allow_long and k_3m > 25 and k_3m_prev <= 25 and k_3m > d_3m and k_1h > 25:
            return {"side": "LONG", "reason": f"{_REASON_PREFIX}LONG_STOCHBOUNCE_k3m{k_3m_prev:.0f}->{k_3m:.0f}_d3m{d_3m:.0f}_k1h{k_1h:.0f}"}
        if allow_short and k_3m < 75 and k_3m_prev >= 75 and k_3m < d_3m and k_1h < 75:
            return {"side": "SHORT", "reason": f"{_REASON_PREFIX}SHORT_STOCHBOUNCE_k3m{k_3m_prev:.0f}->{k_3m:.0f}_d3m{d_3m:.0f}_k1h{k_1h:.0f}"}

    # ── ENTRY PATH 6: STDEV ──────────────────────────────────────────────────
    if bool(getattr(cfg, "SCALP_V3_ENTRY_STDEV_ENABLED", False)):
        _sd_tf = str(getattr(cfg, "SCALP_V3_STDEV_TF", "3m"))
        _sd_mode = str(getattr(cfg, "SCALP_V3_STDEV_MODE", "BOUNCE")).upper()
        _bb_pctb = _sf(store, f"bb_pct_b_{_sd_tf}", bar_idx, 0.5)
        _bb_touches = _sf(store, f"bb_touches_{_sd_tf}", bar_idx, 0.0)
        if _bb_touches >= 1:
            if _sd_mode == "BREAKOUT":
                _br_long = float(getattr(cfg, "SCALP_V3_STDEV_BREAK_HI_LONG", getattr(cfg, "SCALP_V3_STDEV_BREAK_HI", 1.0)))
                _br_short = float(getattr(cfg, "SCALP_V3_STDEV_BREAK_LO_SHORT", getattr(cfg, "SCALP_V3_STDEV_BREAK_LO", 0.0)))
                if allow_long and _bb_pctb > _br_long and wt_bull and k_3m > k_3m_prev:
                    return {"side": "LONG", "reason": f"{_REASON_PREFIX}LONG_STDEV_BREAK_{_sd_tf}_pctb{_bb_pctb:.2f}>{_br_long:.2f}_k3m{k_3m:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
                if allow_short and _bb_pctb < _br_short and wt_bear and k_3m < k_3m_prev:
                    return {"side": "SHORT", "reason": f"{_REASON_PREFIX}SHORT_STDEV_BREAK_{_sd_tf}_pctb{_bb_pctb:.2f}<{_br_short:.2f}_k3m{k_3m:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}
            else:
                _bo_long = float(getattr(cfg, "SCALP_V3_STDEV_BOUNCE_LO_LONG", getattr(cfg, "SCALP_V3_STDEV_BOUNCE_LO", 0.10)))
                _bo_short = float(getattr(cfg, "SCALP_V3_STDEV_BOUNCE_HI_SHORT", getattr(cfg, "SCALP_V3_STDEV_BOUNCE_HI", 0.90)))
                if allow_long and _bb_pctb < _bo_long and wt_bull and wt1_3m > wt1_3m_prev:
                    return {"side": "LONG", "reason": f"{_REASON_PREFIX}LONG_STDEV_BOUNCE_{_sd_tf}_pctb{_bb_pctb:.2f}<{_bo_long:.2f}_k3m{k_3m:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
                if allow_short and _bb_pctb > _bo_short and wt_bear and wt1_3m < wt1_3m_prev:
                    return {"side": "SHORT", "reason": f"{_REASON_PREFIX}SHORT_STDEV_BOUNCE_{_sd_tf}_pctb{_bb_pctb:.2f}>{_bo_short:.2f}_k3m{k_3m:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}

    return None


def check_scalp_v3_exit(store: Any, bar_idx: int, pos_state: Any, side: str, cfg: Any) -> Optional[Dict]:
    """Check SCALP_V3 exit at bar_idx.

    pos_state must have:
        .open (bool)
        .entry_price (float)
        .open_bar (int) — bar index when opened (for max-hold)
        .reason (str) — tagged with SCALP_V3_OPEN_ or QUICK_SCALP_V3_OPEN_ (legacy)

    Returns {"reason": str} or None.
    """
    if not getattr(cfg, "SCALP_V3_ENABLED", False):
        return None
    if not getattr(pos_state, "open", False):
        return None
    reason_tag = str(getattr(pos_state, "reason", "") or "")
    if "SCALP_V3_OPEN_" not in reason_tag and "QUICK_SCALP_V3_OPEN_" not in reason_tag:
        return None

    price = store.price(bar_idx)
    entry_price = float(getattr(pos_state, "entry_price", 0.0) or 0.0)
    if price <= 0 or entry_price <= 0:
        return None

    is_long = (side == "LONG")
    gain_pct = ((price - entry_price) / entry_price * 100.0) if is_long else ((entry_price - price) / entry_price * 100.0)

    profit_only = bool(getattr(cfg, "SCALP_V3_EXIT_PROFIT_ONLY", False))
    max_hold_min = float(getattr(cfg, "SCALP_V3_MAX_HOLD_MIN", 5.0) or 5.0)
    max_hold_bars = max(1, int(max_hold_min / 3.0))
    open_bar = int(getattr(pos_state, "open_bar", 0) or 0)
    bars_held = bar_idx - open_bar if open_bar > 0 else 0

    if profit_only and gain_pct <= 0:
        if max_hold_bars > 0 and bars_held >= max_hold_bars:
            return {"reason": f"{_EXIT_PREFIX}MAX_HOLD_{side}_bars{bars_held}>={max_hold_bars}_g{gain_pct:+.2f}%"}
        return None

    k_3m = _sf(store, "stoch_k_3m", bar_idx, 50.0)
    k_3m_prev = _sf(store, "stoch_k_3m", _prev_idx(bar_idx), k_3m)
    wt1_3m = _sf(store, "wt1_3m", bar_idx)
    wt2_3m = _sf(store, "wt2_3m", bar_idx)
    wt1_3m_prev = _sf(store, "wt1_3m", _prev_idx(bar_idx), wt1_3m)
    wt_vel_3m = _sf(store, "wt_velocity_3m", bar_idx)
    high_3m = _sf(store, "high_3m", bar_idx)
    low_3m = _sf(store, "low_3m", bar_idx)
    high_3m_prev = _sf(store, "high_3m", _prev_idx(bar_idx))
    low_3m_prev = _sf(store, "low_3m", _prev_idx(bar_idx))

    _live_no_bar = high_3m_prev <= 0 or low_3m_prev <= 0

    bar_on = bool(getattr(cfg, "SCALP_V3_EXIT_BAR_REVERSAL_ENABLED", True))
    wt_on = bool(getattr(cfg, "SCALP_V3_EXIT_WT_FLIP_ENABLED", True))
    k_on = bool(getattr(cfg, "SCALP_V3_EXIT_K_CROSS_ENABLED", True))
    require_n = max(1, int(getattr(cfg, "SCALP_V3_EXIT_REQUIRE_N_SIGNALS", 1)))

    sd_exit_on = bool(getattr(cfg, "SCALP_V3_EXIT_STDEV_REJECT_ENABLED", False))
    sd_exit_tf = str(getattr(cfg, "SCALP_V3_STDEV_TF", "3m"))
    sd_reject_hi = float(getattr(cfg, "SCALP_V3_STDEV_REJECT_HI", 0.95))
    sd_reject_lo = float(getattr(cfg, "SCALP_V3_STDEV_REJECT_LO", 0.05))
    sd_pctb = _sf(store, f"bb_pct_b_{sd_exit_tf}", bar_idx, 0.5)
    sd_touches = _sf(store, f"bb_touches_{sd_exit_tf}", bar_idx, 0.0)

    sigs = []
    if is_long:
        if _live_no_bar:
            bar_falling = (wt1_3m < wt1_3m_prev) and (wt_vel_3m < 0)
        else:
            bar_falling = (low_3m < low_3m_prev) or (high_3m < high_3m_prev)
        if bar_on and bar_falling:
            sigs.append("BAR")
        if wt_on and wt1_3m < wt2_3m:
            sigs.append("WT")
        if k_on and k_3m < k_3m_prev and k_3m < 50:
            sigs.append("K")
        if sd_exit_on and sd_touches >= 1 and sd_pctb >= sd_reject_hi:
            sigs.append(f"SDREJ{sd_exit_tf}")
        if len(sigs) >= require_n:
            return {"reason": f"{_EXIT_PREFIX}{'_'.join(sigs)}_LONG_n{len(sigs)}_k3m{k_3m:.0f}_wt3m{wt1_3m:.1f}/{wt2_3m:.1f}"}
    else:
        if _live_no_bar:
            bar_rising = (wt1_3m > wt1_3m_prev) and (wt_vel_3m > 0)
        else:
            bar_rising = (high_3m > high_3m_prev) or (low_3m > low_3m_prev)
        if bar_on and bar_rising:
            sigs.append("BAR")
        if wt_on and wt1_3m > wt2_3m:
            sigs.append("WT")
        if k_on and k_3m > k_3m_prev and k_3m > 50:
            sigs.append("K")
        if sd_exit_on and sd_touches >= 1 and sd_pctb <= sd_reject_lo:
            sigs.append(f"SDREJ{sd_exit_tf}")
        if len(sigs) >= require_n:
            return {"reason": f"{_EXIT_PREFIX}{'_'.join(sigs)}_SHORT_n{len(sigs)}_k3m{k_3m:.0f}_wt3m{wt1_3m:.1f}/{wt2_3m:.1f}"}

    if max_hold_bars > 0 and bars_held >= max_hold_bars:
        return {"reason": f"{_EXIT_PREFIX}MAX_HOLD_{side}_bars{bars_held}>={max_hold_bars}"}

    return None
