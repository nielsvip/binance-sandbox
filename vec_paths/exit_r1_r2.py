"""
vec_paths/exit_r1_r2.py — R1 DC emergency exit + R2 WT velocity slow exit.

Both paths BYPASS the UNIVERSAL_NOLOSS_GATE — they are the only two
vectorized loss-exit paths allowed per CLAUDE.md EXIT RULES 2026-05-09.

R1 SOURCE: ez_manage.py:~20960 / tradier_manage.py:~1506
R2 SOURCE: ez_manage.py:~21022 / tradier_manage.py:~1564

LIVE REASON PATTERNS (searchable in /history/):
  R1 crypto:  R1_DC_LOW4_3M_EMERGENCY_g<N>_age<N>m_entry_<sig>
  R1 tradier: R1_DC_LOW4_EMERGENCY_g<N>_age<N>m_entry_<sig>
  R2 crypto:  R2_WT_VEL_SLOW_DECEL_15m_g<N>%_vel<N>vs<N>
  R2 tradier: R2_WT_VEL_SLOW_DECEL_1h_g<N>%_ ... (1h/4h/D)
  Legacy:     WT_15M_VEL_SLOW* (alias honored in bypass list)

RETURN TYPE (both functions):
    None  — did not fire
    dict  — {
        "reason": str,      # full reason string matching live pattern
        "path": str,        # "R1_DC_EMERGENCY" or "R2_WT_VEL_SLOW"
        "bypass_noloss": True,
    }

Both functions are STATELESS — all state is read from pos_state / store.
pos_state is the _PositionState dataclass from vec_engine_v1.py.

IMPORTANT: These functions do NOT close the position — they only signal
that the position should be closed. The engine does the state transition.

CONFIG KEYS MIRRORED (from config.py / config_tradier.py):
    R1:
        R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = True
        R1_NEWBORN_WINDOW_MIN: int = 15                   # LEGACY — live ignores age (fixed-stop active)
        R1_NEWBORN_WINDOW_ENFORCE: bool = False           # vec default OFF — matches live (no age window)
        R1_USE_DC_4BAR: bool = True
        R1_TF: str = "3m"  (crypto) / "5m" (tradier)
        R1_RESTRICT_TO_OVERBOUGHT_BREAKOUT: bool = True  # 2026-05-23 USER MANDATE — R1 only fires for overbought-breakout entries
        R1_ATR_3M_MULT: float = 3.0                       # 2026-05-23 OR-condition (b) — 3x ATR_3m fixed stop from entry
    R2:
        WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED: bool = True
        WT_15M_VEL_SLOW_GAIN_FLOOR_PCT: float = 0.01
        WT_15M_VEL_SLOW_GAIN_BAND_PCT: float = 0.10
        R2_PEAK_MIN_PCT: float = 0.5  (must have peaked >= this before collapse)
        WT_VEL_DECEL_RATIO: float = 0.5
        WT_VEL_USE_DECEL_RATIO_ONLY: bool = True
        WT_15M_VEL_NEAR_ZERO_THRESHOLD: float = 0.1  (legacy; only if USE_DECEL_RATIO_ONLY=False)
        R2_TF_LIST: list = ["15m"]  (crypto) / ["1h","4h","D"] (tradier)
"""
from __future__ import annotations

from typing import Any, Optional


_R1_OVERBOUGHT_BREAKOUT_MARKERS = (
    "STRONG_BUY", "DC_BREAK", "BREAKOUT", "_BREAK_", "PARABOLIC", "MOMENTUM_RIDER",
    "TOR_BREAK", "WT_DC_HTF", "HIGH_BREAK", "PEAK_BREAK",
)


def check_r1_emergency_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    """R1 DC-LOW4 emergency exit — fires within newborn window if price breaks channel.

    Mirrors ez_manage.py:38272-38427 (crypto) and tradier_manage.py:~1506 (tradier).
    Bypasses UNIVERSAL_NOLOSS_GATE.

    2026-05-23 USER MANDATE (port from live, ez_manage.py:38275-38299):
      R1 only fires for OVERBOUGHT-BREAKOUT entries (entries made in the top of HTF
      range that might be about to crash). For those, R1 uses ANY-OF three triggers:
        (a) dc_low4_3m breach        [original]
        (b) 3x ATR_3m fixed-stop breach from entry
        (c) lower-high + lower-low 3m bar pattern (structural breakdown)
      For all OTHER entries (mid-range opens, reentries, RZ_BOUNCE without breakout),
      R1 is SKIPPED — the entry is bottom-zone, not overbought.

    Args:
        store:     _NPZStore (supports .f(key, idx), .price(idx))
        bar_idx:   current bar index in NPZ
        pos_state: _PositionState for this position (must have .reason set to entry sig)
        mode:      "crypto" or "tradier"
        cfg:       VecConfig (or any object with R1_* attributes)

    Returns:
        dict with reason/path/bypass_noloss/breach_tag, or None if not firing.
    """
    if not getattr(cfg, "R1_DC_LOW4_3M_EMERGENCY_ENABLED", True):
        return None
    if not pos_state.open:
        return None

    # 2026-05-23 RESTRICT gate — skip R1 for non-overbought entries.
    # Mirrors ez_manage.py:38290-38303. Default True per live.
    restrict = bool(getattr(cfg, "R1_RESTRICT_TO_OVERBOUGHT_BREAKOUT", True))
    if restrict:
        entry_sig = str(getattr(pos_state, "reason", "") or "").upper()
        if not any(m in entry_sig for m in _R1_OVERBOUGHT_BREAKOUT_MARKERS):
            return None

    age_min = 0.0
    try:
        _ts_arr = store.timestamps if hasattr(store, "timestamps") else store.arrays.get("timestamps")
        if _ts_arr is not None and bar_idx < len(_ts_arr) and pos_state.entry_ts > 0:
            age_min = (float(_ts_arr[bar_idx]) - pos_state.entry_ts) / 60.0
    except Exception:
        pass

    # 2026-05-09 USER MANDATE: live R1 has NO age window (fixed-stop active).
    # ez_manage.py:38322 and tradier_manage.py:1956 explicitly compute age "for log only".
    # The R1_NEWBORN_WINDOW_MIN config knob is LEGACY and disabled by default in vec to
    # match live behavior. Set R1_NEWBORN_WINDOW_ENFORCE=True ONLY for legacy backtest
    # comparisons that pre-date the 2026-05-09 mandate.
    if bool(getattr(cfg, "R1_NEWBORN_WINDOW_ENFORCE", False)):
        window = float(getattr(cfg, "R1_NEWBORN_WINDOW_MIN", 15))
        if age_min > window:
            return None

    use_4bar = bool(getattr(cfg, "R1_USE_DC_4BAR", True))
    tf = getattr(cfg, "R1_TF", "5m" if mode == "tradier" else "3m")

    if use_4bar:
        dc_level_l = store.f(f"dc_low4_{tf}", bar_idx, 0.0)
        dc_level_h = store.f(f"dc_high4_{tf}", bar_idx, 0.0)
    else:
        dc_level_l = store.f(f"dc_low_{tf}", bar_idx, 0.0)
        dc_level_h = store.f(f"dc_high_{tf}", bar_idx, 0.0)

    price = store.price(bar_idx)
    if price <= 0:
        return None

    is_long = (pos_state.side == "LONG")
    if bool(getattr(cfg, "R1_REQUIRE_WT15_ADVERSE", False)):
        wt1_15 = store.f("wt1_15m", bar_idx, 0.0)
        wt2_15 = store.f("wt2_15m", bar_idx, 0.0)
        vel_15 = store.f("wt_velocity_15m", bar_idx, 0.0)
        wt15_adverse = ((wt1_15 < wt2_15 and vel_15 < 0.0) if is_long
                        else (wt1_15 > wt2_15 and vel_15 > 0.0))
        if not wt15_adverse:
            return None
    # (a) DC_LOW4_3M breach — original condition.
    # Live R1 uses WS-mark `current_price` (intra-bar). NPZ `close` is post-bar so it
    # can MISS true intra-bar breaches captured by live. Use bar's low_3m (LONG) /
    # high_3m (SHORT) as the worst intra-bar price probe; falls back to close when
    # the high/low arrays are unavailable. This is a NPZ-resolution faithful probe,
    # not a tolerance change — bar low/high are bounded by actual price action.
    if is_long:
        probe_lo = store.f("low_3m", bar_idx, price)
        adverse_px = probe_lo if probe_lo > 0 else price
    else:
        probe_hi = store.f("high_3m", bar_idx, price)
        adverse_px = probe_hi if probe_hi > 0 else price
    breached = (
        (is_long and dc_level_l > 0 and adverse_px <= dc_level_l) or
        ((not is_long) and dc_level_h > 0 and adverse_px >= dc_level_h)
    )
    breach_tag: Optional[str] = "DC_LOW4_3M" if breached else None

    # (b) 3x ATR_3m fixed-stop breach from entry — ez_manage.py:38338-38359.
    # Always uses 3m ATR (not R1_TF). Stop set at entry +/- mult * atr_3m.
    if not breached:
        try:
            atr_mult = float(getattr(cfg, "R1_ATR_3M_MULT", 3.0))
            atr_3m = store.f("atr_3m", bar_idx, 0.0)
            entry_px = float(getattr(pos_state, "entry_price", 0.0) or 0.0)
            if atr_3m > 0 and entry_px > 0:
                atr_stop = (entry_px - atr_mult * atr_3m) if is_long else (entry_px + atr_mult * atr_3m)
                if (is_long and adverse_px <= atr_stop) or ((not is_long) and adverse_px >= atr_stop):
                    breached = True
                    breach_tag = f"3xATR_3M_{atr_mult}"
                    dc_level_l = atr_stop if is_long else dc_level_l
                    dc_level_h = atr_stop if (not is_long) else dc_level_h
        except Exception:
            pass

    # (c) lower-high + lower-low 3m bar pattern — ez_manage.py:38360-38378.
    # Always uses 3m (not R1_TF).
    if not breached:
        try:
            h3 = store.f("high_3m", bar_idx, 0.0)
            l3 = store.f("low_3m", bar_idx, 0.0)
            h3_prev = store.f("high_3m_prev", bar_idx, 0.0)
            l3_prev = store.f("low_3m_prev", bar_idx, 0.0)
            if h3 > 0 and h3_prev > 0 and l3 > 0 and l3_prev > 0:
                if is_long:
                    lh_ll = (h3 < h3_prev and l3 < l3_prev)
                else:
                    lh_ll = (h3 > h3_prev and l3 > l3_prev)
                if lh_ll:
                    breached = True
                    breach_tag = "LH_LL_3M"
        except Exception:
            pass

    if not breached:
        return None

    gain = pos_state.gain_pct
    # 2026-05-23 reason format mirrors ez_manage.py:38409 — R1_DC_LOW4_3M_EMERGENCY_via_{TAG}_g{G}_age{A}m
    # Validator regex matches "R1_DC_" — backward compatible with pre-2026-05-23 events.
    reason_pfx = "R1_DC_LOW4_3M_EMERGENCY" if mode == "crypto" else "R1_DC_LOW4_EMERGENCY"
    reason = f"{reason_pfx}_via_{breach_tag}_g{gain:.2f}_age{age_min:.1f}m"
    return {
        "reason": reason,
        "path": "R1_DC_EMERGENCY",
        "bypass_noloss": True,
        "breach_tag": breach_tag,
    }


def check_r2_wt_vel_slow_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    """R2 WT velocity slowdown near-breakeven exit.

    Mirrors ez_manage.py:~21022 (crypto) and tradier_manage.py:~1564 (tradier).
    Bypasses UNIVERSAL_NOLOSS_GATE.

    Fires when ALL of:
        floor <= current_gain < band        (price giving back from peak)
        peak_gain >= R2_PEAK_MIN_PCT        (must have peaked first — not from-open)
        wt_velocity_<TF> against position   (vel sign opposes pos direction)
        |vel| < |vel_prev| * DECEL_RATIO    (dynamic deceleration) OR
        |vel| <= NEAR_ZERO_THRESHOLD        (dying, if USE_DECEL_RATIO_ONLY=False)

    Args:
        store:     _NPZStore
        bar_idx:   current bar index
        pos_state: _PositionState
        mode:      "crypto" or "tradier"
        cfg:       VecConfig

    Returns:
        dict with reason/path/bypass_noloss, or None.
    """
    if not getattr(cfg, "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED", True):
        return None
    if not pos_state.open:
        return None

    gain = pos_state.gain_pct
    max_gain = pos_state.max_gain_pct
    floor = float(getattr(cfg, "WT_15M_VEL_SLOW_GAIN_FLOOR_PCT", 0.01))
    band = float(getattr(cfg, "WT_15M_VEL_SLOW_GAIN_BAND_PCT", 0.10))
    peak_min = float(getattr(cfg, "R2_PEAK_MIN_PCT", 0.5))

    if not (floor <= gain < band):
        return None
    if max_gain < peak_min:
        return None

    is_long = (pos_state.side == "LONG")
    decel_ratio = float(getattr(cfg, "WT_VEL_DECEL_RATIO", 0.5))
    decel_only = bool(getattr(cfg, "WT_VEL_USE_DECEL_RATIO_ONLY", True))
    near_zero = float(getattr(cfg, "WT_15M_VEL_NEAR_ZERO_THRESHOLD", 0.1))

    tfs = getattr(cfg, "R2_TF_LIST", None)
    if not tfs:
        tfs = ["1h", "4h", "D"] if mode == "tradier" else ["15m"]

    fired_tf = None
    fired_vel = 0.0
    fired_vel_prev = 0.0
    fired_tag = ""

    for tf in tfs:
        vel = store.f(f"wt_velocity_{tf}", bar_idx, 0.0)
        prev_idx = max(0, bar_idx - 1)
        vel_prev = store.f(f"wt_velocity_{tf}", prev_idx, vel)
        against = (is_long and vel < 0) or ((not is_long) and vel > 0)
        decel = abs(vel) < abs(vel_prev) * decel_ratio and abs(vel_prev) > 1e-6
        dying = (not decel_only) and abs(vel) <= near_zero
        if against and (decel or dying):
            fired_tf = tf
            fired_vel = vel
            fired_vel_prev = vel_prev
            fired_tag = "DECEL" if decel else "DYING"
            break

    if not fired_tf:
        return None

    reason = (
        f"R2_WT_VEL_SLOW_{fired_tag}_{fired_tf}"
        f"_g{gain:.3f}%_peak{max_gain:.2f}%"
        f"_vel{fired_vel:.3f}vs{fired_vel_prev:.3f}"
    )
    return {
        "reason": reason,
        "path": "R2_WT_VEL_SLOW",
        "bypass_noloss": True,
        "tf": fired_tf,
        "tag": fired_tag,
    }
