"""Paired 15m twin of EPQ AUGMENTED_DC_BREAK_REDUCE_TO_MIN."""

import numpy as np


def augmented_dc_break_pos_min_qty(symbol, price, min_position_usd):
    """Mirror EPQ's max(2 * MIN_POSITION_SIZE / price, exchange minQty)."""
    try:
        px = float(price)
        min_usd = float(min_position_usd)
        if not np.isfinite(px) or px <= 0.0 or not np.isfinite(min_usd) or min_usd < 0.0:
            return 0.0
        from vec_decisions.qty_quantizers import _min_qty_table

        row = _min_qty_table().get(str(symbol), {}) or {}
        exchange_min = float(row.get("minQty") or 0.0)
        if not np.isfinite(exchange_min) or exchange_min < 0.0:
            return 0.0
        return max(2.0 * min_usd / px, exchange_min)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def augmented_dc_break_fires(position_qty, pos_min_qty, price, dc_level, gain_pct, is_long, in_grace, enabled, hedge_mode):
    """Return the native fire decision; HEDGE_MODE fails closed without tracker state."""
    try:
        qty, min_qty, px, level, gain = map(float, (position_qty, pos_min_qty, price, dc_level, gain_pct))
    except (TypeError, ValueError, OverflowError):
        return False
    if not all(np.isfinite(x) for x in (qty, min_qty, px, level, gain)):
        return False
    if not enabled or hedge_mode or in_grace or px <= 0.0 or level <= 0.0 or gain <= 0.1:
        return False
    if qty <= 1.2 * min_qty:
        return False
    return (px < level) if is_long else (px > level)


def augmented_dc_break_reduce_qty(position_qty, pos_min_qty, gain_pct, min_profit_pct, low_frac, med_frac, high_frac):
    """Mirror EPQ's hard-exit sizing bands for the non-full-close reason."""
    try:
        qty, min_qty, gain, min_profit, low, med, high = map(
            float,
            (position_qty, pos_min_qty, gain_pct, min_profit_pct, low_frac, med_frac, high_frac),
        )
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not all(np.isfinite(x) for x in (qty, min_qty, gain, min_profit, low, med, high)):
        return 0.0
    if qty < 0.0 or min_qty < 0.0:
        return 0.0
    if gain < 1.0 and gain >= min_profit:
        fraction = low if gain < 0.5 else med
        return max(min_qty, qty * fraction)
    if 1.0 <= gain < 3.0:
        return qty * high
    return max(0.0, qty - min_qty * 0.9)
