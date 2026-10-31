"""Shared predicate for ez_manage's profitable OPTIMAL_HOLD_BARS exit."""

import numpy as np


def unrealized_position_gain_pct(is_long, current_price, average_entry_price):
    """Match ez_manage.calculate_gain: unrealized price move from current average entry."""
    current_price = float(current_price)
    average_entry_price = float(average_entry_price)
    if average_entry_price <= 0.0:
        return 0.0
    if is_long:
        return (current_price - average_entry_price) / average_entry_price * 100.0
    return (average_entry_price - current_price) / average_entry_price * 100.0


def satoshit_standard_exits_skipped(cfg, entry_reason, augmentation_reasons):
    """Match ez_satoshit.is_satoshit_trade from the latest position reason."""
    if not bool(getattr(cfg, "SATOSHIT_PROTECT_TRADES", False)) or not bool(
        getattr(cfg, "SATOSHIT_ENABLED", False)
    ):
        return False
    reasons = list(augmentation_reasons or [])
    source = reasons[-1] if reasons else entry_reason
    return "SATOSHIT" in str(source or "")


def optimal_hold_bars_exit_core(cfg, gain_pct, elapsed_seconds, eligible):
    """Return a bool mask for the live gain, focus-TF, and wall-age rule."""
    gain_pct = np.asarray(gain_pct, dtype=float)
    elapsed_seconds = np.broadcast_to(np.asarray(elapsed_seconds, dtype=float), gain_pct.shape)
    eligible = np.broadcast_to(np.asarray(eligible, dtype=bool), gain_pct.shape)
    if not bool(cfg.OPTIMAL_HOLD_BARS_EXIT_ENABLED):
        return np.zeros(gain_pct.shape, dtype=bool)
    if str(cfg.TF_FOCUS) != "3m":
        return np.zeros(gain_pct.shape, dtype=bool)
    min_gain = max(float(cfg.GAIN_THRESHOLD_LOW), float(cfg.NOLOSS_MIN_PROFIT_PCT))
    max_age_seconds = float(cfg.OPTIMAL_HOLD_BARS_3M) * 180.0
    return eligible & np.isfinite(gain_pct) & np.isfinite(elapsed_seconds) & (gain_pct > min_gain) & (elapsed_seconds > max_age_seconds)


def optimal_hold_bars_exit_live_adapter(cfg, gain_pct, now_ts, opened_at_ts, last_augmentation_ts, position_active, skip_standard_exits, allow_epoch=False):
    """Scalar live adapter; opened_at has precedence, matching the current caller."""
    anchor = opened_at_ts if opened_at_ts else last_augmentation_ts
    if not anchor or skip_standard_exits or not position_active:
        return False
    try:
        elapsed = (now_ts - anchor).total_seconds()
    except (TypeError, AttributeError):
        if allow_epoch and isinstance(now_ts, (int, float)) and isinstance(anchor, (int, float)):
            elapsed = float(now_ts) - float(anchor)
        else:
            elapsed = 0.0
    return bool(optimal_hold_bars_exit_core(cfg, np.asarray([gain_pct]), np.asarray([elapsed]), np.asarray([True]))[0])


def optimal_hold_bars_exit_mask(npz, n, cfg, gain_pct, opened_at_ts, last_augmentation_ts, position_active, skip_standard_exits):
    """Vector adapter from closed-bar UTC timestamps and explicit position-age state."""
    if "timestamps" not in npz:
        return np.zeros(n, dtype=bool)
    timestamps = np.asarray(npz["timestamps"], dtype=float)
    if len(timestamps) != n:
        return np.zeros(n, dtype=bool)
    opened_at_ts = np.broadcast_to(np.asarray(opened_at_ts, dtype=float), (n,))
    last_augmentation_ts = np.broadcast_to(np.asarray(last_augmentation_ts, dtype=float), (n,))
    anchor = np.where(opened_at_ts > 0.0, opened_at_ts, last_augmentation_ts)
    eligible = np.asarray(position_active, dtype=bool) & ~np.asarray(skip_standard_exits, dtype=bool) & (anchor > 0.0)
    return optimal_hold_bars_exit_core(cfg, gain_pct, timestamps - anchor, eligible)
