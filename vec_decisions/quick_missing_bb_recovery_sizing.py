"""Pure BB_RECOVERY partial-reduce sizing shared by scalar/vector paths."""

import numpy as np


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
