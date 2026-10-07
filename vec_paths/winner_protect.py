"""
vec_paths/winner_protect.py — WINNER_PROTECT hold guard + WT_15M_VEL_SLOW near-zero exit.

Sources:
  - WINNER_PROTECT: ez_positions_quick.py:13918-13931
  - WT_15M_VEL_SLOW (zero-gain band variant):
      ez_manage.py:21022-21075 (R2 block)
      CLAUDE.md feedback_wt15m_loss_bypass_augment_lock_20260509

Public API:
    check_winner_protect_hold(store, bar_idx, pos_state, mode, cfg) -> bool
    check_wt_15m_vel_slow_zero_gain(store, bar_idx, pos_state, mode, cfg) -> Optional[str]

check_winner_protect_hold:
  Returns True if WINNER_PROTECT should block a TREND_REVERSAL_EXIT for this position.
  Logic: position gain in [0, WP_MIN_GAIN_PCT) AND ranking points >= RP_PROTECT_THRESHOLD.
  Because vec_engine has no live ranking, we use an approximation:
    wt_bullish (4h+D) as a ranking proxy for LONG (bearish for SHORT).
  The original live check uses 0ranking_points_global (live market-data field).
  Vec fallback: count of HTF WT cross/bias aligned with direction >= threshold.

check_wt_15m_vel_slow_zero_gain:
  Returns a reason string (CLOSE signal) or None.
  This is the R2 path — fires BEFORE NOLOSS gate (is a NOLOSS bypass).
  Trigger conditions (per R2_TF_LIST, default ['15m']):
    - max_gain_pct >= R2_PEAK_MIN_PCT (default 0.5%) — must have peaked
    - gain in [WT_15M_VEL_SLOW_GAIN_FLOOR_PCT, WT_15M_VEL_SLOW_GAIN_BAND_PCT)
      default [0.01, 0.10) — "approaching 0 from above"
    - wt_velocity_<TF> opposes position direction
    - DECEL: |vel| < |vel_prev| * WT_VEL_DECEL_RATIO (default 0.5)
      OR DYING: |vel| <= WT_15M_VEL_NEAR_ZERO_THRESHOLD (when WT_VEL_USE_DECEL_RATIO_ONLY=False)

Config defaults from config.py (also config_tradier.py):
  WINNER_PROTECT_ENABLED             = False  (config.py default; True in some presets)
  RP_PROTECT_THRESHOLD               = 70.0
  RP_PROTECT_MIN_GAIN                = 2.0
  WINNER_PROTECT_HTF_MIN_ALIGNED     = 2      (vec-only proxy for missing ranking points)

  WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED = True
  R2_PEAK_MIN_PCT                    = 0.5
  WT_15M_VEL_SLOW_GAIN_BAND_PCT      = 0.10
  WT_15M_VEL_SLOW_GAIN_FLOOR_PCT     = 0.01
  WT_15M_VEL_NEAR_ZERO_THRESHOLD     = 0.1
  WT_VEL_DECEL_RATIO                 = 0.5
  WT_VEL_USE_DECEL_RATIO_ONLY        = True
  R2_TF_LIST                         = ['15m']  (tradier: ['1h','4h','D'])
"""
from __future__ import annotations

from typing import Any, List, Optional


def _sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def check_winner_protect_hold(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> bool:
    """Return True if WINNER_PROTECT should block TREND_REVERSAL_EXIT.

    In live: checks 0ranking_points_global >= threshold.
    In vec: approximates with HTF WT alignment count (4h + D + W).
    The gate only applies when gain is in [0, RP_PROTECT_MIN_GAIN).

    Args:
        store:      NPZStore for the symbol.
        bar_idx:    Current bar index.
        pos_state:  _PositionState — needs .side and .gain_pct.
        mode:       'crypto' | 'tradier'.
        cfg:        Config object.

    Returns:
        True  — TREND_REVERSAL_EXIT should be skipped.
        False — no protection applies.
    """
    if not bool(getattr(cfg, 'WINNER_PROTECT_ENABLED', False)):
        return False

    gain = float(getattr(pos_state, 'gain_pct', 0.0))
    wp_min_gain = float(getattr(cfg, 'RP_PROTECT_MIN_GAIN', 2.0))

    # Only protect positions not yet at the gain threshold
    if not (0.0 <= gain < wp_min_gain):
        return False

    is_long = (pos_state.side == 'LONG')

    # Live check uses 0ranking_points_global — unavailable in vec/NPZ.
    # Proxy: count of HTF (4h, D, W) WT aligned with direction.
    # If count >= WINNER_PROTECT_HTF_MIN_ALIGNED, treat as "top-ranked".
    min_al = int(getattr(cfg, 'WINNER_PROTECT_HTF_MIN_ALIGNED', 2))
    aligned = 0
    for tf in ('4h', 'D', 'W'):
        w1 = store.f(f'wt1_{tf}', bar_idx, 0.0)
        w2 = store.f(f'wt2_{tf}', bar_idx, 0.0)
        if is_long and w1 > w2 and w1 != 0:
            aligned += 1
        elif not is_long and w1 < w2 and w1 != 0:
            aligned += 1

    return aligned >= min_al


def check_wt_15m_vel_slow_zero_gain(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[str]:
    """WT velocity slow-down near breakeven exit (R2 path).

    Source: ez_manage.py:21022-21075 R2 block.
    This is a NOLOSS BYPASS — fires even on losing positions (within the gain band).
    In vec_engine_v1, call BEFORE the UNIVERSAL_NOLOSS_GATE check.

    Args:
        store:      NPZStore for the symbol.
        bar_idx:    Current bar index.
        pos_state:  _PositionState — needs .side, .gain_pct, .max_gain_pct.
        mode:       'crypto' | 'tradier'.
        cfg:        Config object.

    Returns:
        None           — no action.
        reason string  — full-close signal (R2_WT_VEL_SLOW_...).
    """
    if not bool(getattr(cfg, 'WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED', True)):
        return None

    gain = float(getattr(pos_state, 'gain_pct', 0.0))
    max_gain = float(getattr(pos_state, 'max_gain_pct', 0.0))

    peak_min = float(getattr(cfg, 'R2_PEAK_MIN_PCT', 0.5))
    band = float(getattr(cfg, 'WT_15M_VEL_SLOW_GAIN_BAND_PCT', 0.10))
    floor = float(getattr(cfg, 'WT_15M_VEL_SLOW_GAIN_FLOOR_PCT', 0.01))

    # PEAK-THEN-COLLAPSE: must have peaked ≥ peak_min AND now be in [floor, band)
    if max_gain < peak_min:
        return None
    if not (floor <= gain < band):
        return None

    is_long = (pos_state.side == 'LONG')

    near_zero_thr = float(getattr(cfg, 'WT_15M_VEL_NEAR_ZERO_THRESHOLD', 0.1))
    decel_ratio = float(getattr(cfg, 'WT_VEL_DECEL_RATIO', 0.5))
    decel_only = bool(getattr(cfg, 'WT_VEL_USE_DECEL_RATIO_ONLY', True))

    # Iterate over configured TFs (first hit wins, matching live break logic)
    tf_list: List[str] = list(getattr(cfg, 'R2_TF_LIST', ('15m',)) or ('15m',))

    for tf in tf_list:
        vel_key = f'wt_velocity_{tf}'
        vel_prev_key = f'wt_velocity_{tf}_prev'

        vel = store.f(vel_key, bar_idx, 0.0)
        # vel_prev: try explicit prev field, fall back to prior bar
        vel_prev = store.f(vel_prev_key, bar_idx, 0.0)
        if vel_prev == 0.0 and bar_idx > 0:
            vel_prev = store.f(vel_key, bar_idx - 1, 0.0)

        _against = (is_long and vel < 0) or (not is_long and vel > 0)
        if not _against:
            continue

        _decel = (abs(vel_prev) > 1e-6) and (abs(vel) < abs(vel_prev) * decel_ratio)
        _dying = (not decel_only) and (abs(vel) <= near_zero_thr)

        if _decel or _dying:
            tag = 'DECEL' if _decel else 'DYING'
            return (
                f"R2_WT_VEL_SLOW_{tag}_{tf}"
                f"_g{gain:.3f}%"
                f"_vel{vel:.3f}vs{vel_prev:.3f}"
                f"_peak{max_gain:.2f}%"
            )

    return None
