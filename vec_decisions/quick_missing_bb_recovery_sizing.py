"""Pure BB_RECOVERY partial-reduce sizing shared by scalar/vector paths."""

import numpy as np


def bb_recovery_qty_limits(symbol, price, cfg):
    """Return live pos_min_qty and execute_trade_action min viable order qty."""
    try:
        px = float(price)
    except (TypeError, ValueError, OverflowError):
        return 0.0, float("inf")
    if not np.isfinite(px) or px <= 0.0:
        return 0.0, float("inf")
    try:
        from vec_decisions.qty_quantizers import _min_qty_table

        row = _min_qty_table().get(str(symbol), {}) or {}
    except Exception:
        row = {}
    try:
        raw_exchange_min = row.get("minQty")
        exchange_min = float(raw_exchange_min or 0.0)
        order_exchange_min = float(0.0001 if raw_exchange_min is None else raw_exchange_min)
        min_notional = float(row.get("minNotional", 5.0) or 5.0)
        min_position = float(getattr(cfg, "MIN_POSITION_SIZE", 1.0) or 1.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0, float("inf")
    if not np.isfinite(exchange_min) or exchange_min < 0.0:
        return 0.0, float("inf")
    if not np.isfinite(min_notional) or min_notional < 0.0:
        return 0.0, float("inf")
    if not np.isfinite(min_position) or min_position < 0.0:
        return 0.0, float("inf")
    pos_min_qty = max(2.0 * min_position / px, exchange_min)
    order_min_qty = max(order_exchange_min * 1.2, 6.0 / px, min_notional / px)
    return pos_min_qty, order_min_qty


def bb_recovery_reduce_qty(position_qty, min_qty, gain_pct, min_profit_pct, low_frac, med_frac, high_frac):
    """Return native REDUCE quantity; BB_RECOVERY is never a full CLOSE reason."""
    position_qty, min_qty, gain_pct, min_profit_pct, low_frac, med_frac, high_frac = np.broadcast_arrays(
        np.asarray(position_qty, dtype=np.float64),
        np.asarray(min_qty, dtype=np.float64),
        np.asarray(gain_pct, dtype=np.float64),
        np.asarray(min_profit_pct, dtype=np.float64),
        np.asarray(low_frac, dtype=np.float64),
        np.asarray(med_frac, dtype=np.float64),
        np.asarray(high_frac, dtype=np.float64),
    )
    valid = (
        np.isfinite(position_qty)
        & np.isfinite(min_qty)
        & np.isfinite(gain_pct)
        & np.isfinite(min_profit_pct)
        & np.isfinite(low_frac)
        & np.isfinite(med_frac)
        & np.isfinite(high_frac)
        & (position_qty >= 0.0)
        & (min_qty >= 0.0)
    )
    qty = position_qty - min_qty * 0.9
    low_band = (gain_pct < 1.0) & (gain_pct >= min_profit_pct)
    low_fraction = np.where(gain_pct < 0.5, low_frac, med_frac)
    qty = np.where(low_band, np.maximum(min_qty, position_qty * low_fraction), qty)
    mid_band = (gain_pct >= 1.0) & (gain_pct < 3.0)
    qty = np.where(mid_band, position_qty * high_frac, qty)
    return np.where(valid, qty, 0.0)


def bb_recovery_reduce_qty_scalar(position_qty, min_qty, gain_pct, min_profit_pct, low_frac, med_frac, high_frac):
    """Scalar adapter matching ez_positions_quick's BB_RECOVERY REDUCE branch."""
    result = bb_recovery_reduce_qty(
        position_qty, min_qty, gain_pct, min_profit_pct, low_frac, med_frac, high_frac
    )
    return float(np.asarray(result).reshape(-1)[0])
