"""
vec_paths/delta_engine.py — DELTA_ENGINE entry path (vec approximation of wt_dc_delta.py).

SOURCE: tradier_manage.py:1917-1965 + wt_dc_delta.py:DeltaTracker.update()

Live logic summary:
  1. DeltaTracker.update(symbol, indicators) is called each bar.
  2. Computes bar-to-bar deltas of ALL WT+DC fields across configured TFs.
  3. Weights TF deltas by tf_weights = {3m:3, 15m:2, 1h:1, 4h:1, D:0.5}.
  4. total_bull = sum(tf_bull[tf] * weight[tf]) / count across all TFs.
  5. Entry fires when entry_long=True from _run_redzone:
     - BASELINE_BOUNCE_LONG: price touched dc_basis_1h from above, bull bounce.
     - TOP_REJECTION_SHORT: price at extreme, velocity reversing.
     - BOTTOM_BOUNCE_LONG: price at extreme bottom, velocity inflecting up.
  6. In tradier_manage, fired when: DELTA_ENGINE_ENABLED AND DELTA_ENTRY_ENABLED AND
     delta_tracker.update().entry_long/short.

Vec approximation:
  - NPZ has wt_velocity_* (= dWT/dbar) and wt_acceleration_* (= d²WT/d²bar).
  - We approximate the multi-TF speed sum using wt_velocity across TFs.
  - bull_speed = weighted sum of max(vel, 0) for all TFs.
  - bear_speed = weighted sum of max(-vel, 0) for all TFs.
  - bull_tf_count = count of TFs where vel > speed_threshold.
  - Entry fires when: bull_tf_count >= entry_min_tf (default 4) AND
    zone assessment gives BASELINE_BOUNCE or BOTTOM_BOUNCE.
  - Zone: BASELINE = dc_position_1h in [0.3, 0.7], BOTTOM = dc_position_1h < 0.3 (LONG).
  - HTF gate: DELTA_HTF_GATE config key ("none", "4h", "4h_D", "4h_D_strict", "hh_hl_4h").

LIMITATIONS vs live:
  - No stateful _prev tracking across bars (live tracker uses prev bar's stored values).
    The NPZ wt_velocity field IS the bar-to-bar delta precomputed by the precompute step,
    so no stateful diff is needed.
  - _run_redzone is a 400-line function — we approximate the zone detection using
    dc_position and bb_pct_b fields that ARE in the NPZ.
  - No pyramid/consolidation signals in this approximation.
  - exit_long/exit_short from delta: delegated to the existing DELTA_ENGINE_ENABLED
    velocity proxy exit in vec_engine_v1 (the WT velocity slowdown path at line 869).

See validate_against_live.py for accuracy measurements.
"""
from __future__ import annotations
from typing import Dict, Any, Optional, Tuple

# TF weights matching DEFAULT_CFG in wt_dc_delta.py
# Tradier uses 5m instead of 3m as base TF
_CRYPTO_TF_WEIGHTS: Dict[str, float] = {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5}
_TRADIER_TF_WEIGHTS: Dict[str, float] = {"5m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5}


def _get_tf_weights(mode: str) -> Dict[str, float]:
    return _TRADIER_TF_WEIGHTS if mode == "tradier" else _CRYPTO_TF_WEIGHTS


def compute_delta_speeds(
    store,
    bar_idx: int,
    mode: str = "tradier",
) -> Tuple[float, float, int, int, Dict[str, float], Dict[str, float]]:
    """Compute bull_speed, bear_speed, bull_tf_count, bear_tf_count.

    Uses wt_velocity_* precomputed fields from NPZ (= bar-to-bar delta of WT).
    Each field already captures the rate of change — no manual differencing needed.

    Returns
    -------
    (total_bull, total_bear, bull_tf_count, bear_tf_count, tf_bull, tf_bear)
    """
    tw = _get_tf_weights(mode)
    speed_thresh = 0.5  # DEFAULT_CFG["entry_speed_threshold"]

    tf_bull: Dict[str, float] = {}
    tf_bear: Dict[str, float] = {}
    total_bull = 0.0
    total_bear = 0.0
    bull_tf_count = 0
    bear_tf_count = 0

    for tf, weight in tw.items():
        vel = float(store.f(f"wt_velocity_{tf}", bar_idx, 0.0))
        # Also consider wt_acceleration for confirmation (mirrors delta of delta)
        accel = float(store.f(f"wt_acceleration_{tf}", bar_idx, 0.0))
        # Use velocity as primary delta signal
        bull_raw = max(vel, 0.0)
        bear_raw = max(-vel, 0.0)
        # Boost when acceleration confirms direction
        if accel > 0:
            bull_raw = max(bull_raw, abs(accel) * 0.5)
        elif accel < 0:
            bear_raw = max(bear_raw, abs(accel) * 0.5)
        tf_bull[tf] = bull_raw
        tf_bear[tf] = bear_raw
        total_bull += bull_raw * weight
        total_bear += bear_raw * weight
        if bull_raw > speed_thresh:
            bull_tf_count += 1
        if bear_raw > speed_thresh:
            bear_tf_count += 1

    return total_bull, total_bear, bull_tf_count, bear_tf_count, tf_bull, tf_bear


def _check_zone_long(store, bar_idx: int, mode: str, cfg) -> Tuple[str, str]:
    """Assess zone for LONG entry (approximates _run_redzone BASELINE and BOTTOM zones).

    Returns (zone, zone_reason) or ("NEUTRAL", "") if no zone fires.
    """
    dc_pos_1h = float(store.f("dc_position_1h", bar_idx, 0.5))
    bb_1h = float(store.f("bb_pct_b_1h", bar_idx, 0.5))
    wt_vel_1h = float(store.f("wt_velocity_1h", bar_idx, 0.0))
    wt1_1h = float(store.f("wt1_1h", bar_idx, 0.0))
    wt2_1h = float(store.f("wt2_1h", bar_idx, 0.0))
    k_1h = float(store.f("stoch_k_1h", bar_idx, 50.0))

    # BASELINE zone: price near dc_basis_1h (0.3 < dc_position < 0.7)
    # BASELINE_BOUNCE_LONG fires when: bull momentum + touched basis
    if 0.25 < dc_pos_1h < 0.75 and wt_vel_1h > 0 and wt1_1h > wt2_1h:
        # Check if dc_basis was touched (crossover of dc_basis_crossover)
        dc_basis_cross = bool(store.b(f"dc_basis_crossover_1h", bar_idx, False))
        if dc_basis_cross:
            reason = (
                f"BASELINE_BOUNCE_LONG_touched=1h_dc={dc_pos_1h:.2f}_"
                f"vel1h={wt_vel_1h:.1f}_k={k_1h:.0f}_bb={bb_1h:.2f}"
            )
            return "BASELINE", reason

    # BOTTOM zone: deep oversold (dc_position < 0.20, bb_pctb < 0.15)
    if dc_pos_1h < 0.25 and bb_1h < 0.20 and k_1h < 25 and wt_vel_1h > 0:
        reason = (
            f"BOTTOM_BOUNCE_LONG_dc={dc_pos_1h:.2f}_bb={bb_1h:.2f}_"
            f"k={k_1h:.0f}_vel1h={wt_vel_1h:.1f}"
        )
        return "BOTTOM", reason

    return "NEUTRAL", ""


def _check_zone_short(store, bar_idx: int, mode: str, cfg) -> Tuple[str, str]:
    """Assess zone for SHORT entry (approximates _run_redzone TOP zone)."""
    dc_pos_1h = float(store.f("dc_position_1h", bar_idx, 0.5))
    bb_1h = float(store.f("bb_pct_b_1h", bar_idx, 0.5))
    wt_vel_1h = float(store.f("wt_velocity_1h", bar_idx, 0.0))
    wt1_1h = float(store.f("wt1_1h", bar_idx, 0.0))
    wt2_1h = float(store.f("wt2_1h", bar_idx, 0.0))
    k_1h = float(store.f("stoch_k_1h", bar_idx, 50.0))

    # BASELINE_BOUNCE_SHORT
    if 0.25 < dc_pos_1h < 0.75 and wt_vel_1h < 0 and wt1_1h < wt2_1h:
        dc_basis_cross = bool(store.b("dc_basis_crossunder_1h", bar_idx, False))
        if dc_basis_cross:
            reason = (
                f"BASELINE_BOUNCE_SHORT_touched=1h_dc={dc_pos_1h:.2f}_"
                f"vel1h={wt_vel_1h:.1f}_k={k_1h:.0f}_bb={bb_1h:.2f}"
            )
            return "BASELINE", reason

    # TOP zone: overbought + velocity reversing down
    if dc_pos_1h > 0.75 and bb_1h > 0.80 and k_1h > 75 and wt_vel_1h < 0:
        reason = (
            f"TOP_REJECTION_SHORT_dc={dc_pos_1h:.2f}_bb={bb_1h:.2f}_"
            f"vel1h={wt_vel_1h:.1f}_k={k_1h:.0f}"
        )
        return "TOP", reason

    return "NEUTRAL", ""


def check_delta_entry(
    store,
    bar_idx: int,
    side: str,
    mode: str,
    cfg,
    prev_bar_idx: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Check whether DELTA_ENGINE entry should fire.

    Parameters
    ----------
    store : _NPZStore
        Indicator store for this symbol.
    bar_idx : int
        Current bar index.
    side : str
        "LONG" or "SHORT".
    mode : str
        "tradier" or "crypto".
    cfg : VecConfig
        Config with DELTA_ENGINE_ENABLED, DELTA_ENTRY_ENABLED, DELTA_HTF_GATE,
        DELTA_ENTRY_Z_THRESHOLD, DELTA_ENTRY_ACCEL_THRESHOLD, DELTA_ENTRY_MIN_TF.
    prev_bar_idx : int, optional
        Previous bar index (for delta computation). If None, bar_idx - 1 is used.

    Returns
    -------
    dict with keys: zone, zone_reason, bull_speed, bear_speed, entry_long, entry_short
    or None if entry does not fire.
    """
    if not getattr(cfg, "DELTA_ENGINE_ENABLED", False):
        return None
    if not getattr(cfg, "DELTA_ENTRY_ENABLED", False):
        return None

    # ── Block TRA account entries (mirrors TRA_DISABLE_DELTA_ENTRY=True default) ──
    # In vec we have no account concept — skip this gate.

    is_long = (side == "LONG")

    # ── Compute delta speeds ──
    total_bull, total_bear, bull_tf_count, bear_tf_count, tf_bull, tf_bear = compute_delta_speeds(
        store, bar_idx, mode
    )

    entry_min_tf = int(getattr(cfg, "DELTA_ENTRY_MIN_TF", 4))
    if is_long and bull_tf_count < entry_min_tf:
        return None
    if not is_long and bear_tf_count < entry_min_tf:
        return None

    # ── Zone assessment ──
    if is_long:
        zone, zone_reason = _check_zone_long(store, bar_idx, mode, cfg)
        entry_fires = zone in ("BASELINE", "BOTTOM")
    else:
        zone, zone_reason = _check_zone_short(store, bar_idx, mode, cfg)
        entry_fires = zone in ("BASELINE", "TOP")

    if not entry_fires or not zone_reason:
        return None

    # ── HTF gate (mirrors tradier_manage:1924-1948) ──
    htf_gate = str(getattr(cfg, "DELTA_HTF_GATE", "none")).lower()
    if htf_gate != "none":
        wt1_4h = float(store.f("wt1_4h", bar_idx, 0.0))
        wt2_4h = float(store.f("wt2_4h", bar_idx, 0.0))
        wt1_D = float(store.f("wt1_D", bar_idx, 0.0))
        wt2_D = float(store.f("wt2_D", bar_idx, 0.0))
        if htf_gate in ("4h", "4h_d", "4h_d_strict"):
            ok_4h = (wt1_4h > wt2_4h) if is_long else (wt1_4h < wt2_4h)
            if not ok_4h:
                return None
        if htf_gate in ("4h_d", "4h_d_strict"):
            ok_D = (wt1_D > wt2_D) if is_long else (wt1_D < wt2_D)
            if not ok_D:
                return None
        if htf_gate == "4h_d_strict":
            k4h = float(store.f("stoch_k_4h", bar_idx, 50.0))
            kD = float(store.f("stoch_k_D", bar_idx, 50.0))
            if is_long and (k4h > 80 or kD > 80):
                return None
            if not is_long and (k4h < 20 or kD < 20):
                return None
        if htf_gate == "hh_hl_4h":
            # Live config.py: LONG if (dc_high_4h>dc_high_4h_prev AND dc_low_4h>dc_low_4h_prev) OR ha_color_4h==1 (green)
            # SHORT if (dc_high_4h<dc_high_4h_prev AND dc_low_4h<dc_low_4h_prev) OR ha_color_4h==-1 (red)
            dc_h4 = float(store.f("dc_high_4h", bar_idx, 0.0))
            dc_l4 = float(store.f("dc_low_4h", bar_idx, 0.0))
            dc_h4_prev = float(store.f("dc_high_4h_prev", bar_idx, 0.0))
            dc_l4_prev = float(store.f("dc_low_4h_prev", bar_idx, 0.0))
            ha_col4 = int(store.f("ha_color_4h", bar_idx, 0))
            if is_long:
                hh_hl = (dc_h4 > dc_h4_prev and dc_l4 > dc_l4_prev) if (dc_h4_prev > 0 and dc_l4_prev > 0) else False
                if not (hh_hl or ha_col4 == 1):
                    return None
            else:
                ll_lh = (dc_h4 < dc_h4_prev and dc_l4 < dc_l4_prev) if (dc_h4_prev > 0 and dc_l4_prev > 0) else False
                if not (ll_lh or ha_col4 == -1):
                    return None

    # ── ATR noise filter (mirrors tradier_manage DELTA_ATR_ENTRY_FILTER) ──
    # Fails-open: when False (default) skip. When True, block low-volatility bars
    # where abs(close - close_5m_prev) < atr_1h * 0.3 (same 0.3 factor as live).
    if getattr(cfg, "DELTA_ATR_ENTRY_FILTER", False):
        try:
            atr_1h = float(store.f("atr_1h", bar_idx, 0.0))
            close_now = float(store.f("close", bar_idx, 0.0))
            close_prev = float(store.f("close_5m_prev", bar_idx, close_now))
            if atr_1h > 0 and abs(close_now - close_prev) < atr_1h * 0.3:
                return None
        except Exception:
            pass  # fails-open on missing data

    # ── DELTA_ENTRY_Z_THRESHOLD / ACCEL / MIN_TF already applied above; ──
    # ensure Z and ACCEL are respects side-aware thresholds (live: bull_speed
    # vs z_threshold). For vec we gate bull/bear speeds directly.
    z_thr = float(getattr(cfg, "DELTA_ENTRY_Z_THRESHOLD", 2.5))
    accel_thr = float(getattr(cfg, "DELTA_ENTRY_ACCEL_THRESHOLD", 0.0))
    if is_long:
        if total_bull < z_thr:
            return None
        # accel approximation: wt_acceleration_1h must exceed threshold when set
        if accel_thr > 0:
            accel_1h = float(store.f("wt_acceleration_1h", bar_idx, 0.0))
            if accel_1h < accel_thr:
                return None
    else:
        if total_bear < z_thr:
            return None
        if accel_thr > 0:
            accel_1h = float(store.f("wt_acceleration_1h", bar_idx, 0.0))
            if accel_1h > -accel_thr:
                return None

    _side_char = "L" if is_long else "S"
    reason = f"DELTA_ENTRY_{zone}_{_side_char}_{zone_reason}"

    return {
        "zone": zone,
        "zone_reason": zone_reason,
        "reason": reason,
        "bull_speed": total_bull,
        "bear_speed": total_bear,
        "bull_tf_count": bull_tf_count,
        "bear_tf_count": bear_tf_count,
        "entry_long": is_long,
        "entry_short": not is_long,
    }
