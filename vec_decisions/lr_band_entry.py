"""LR_BAND_ENTRY vec twin — faithful numpy port of ez_manage.py:40770-40790.

LIVE SOURCE (crypto; stocks mirror in tradier_manage — same predicate):
  tf = LR_BAND_ENTRY_TF ('4h'); pb = lrL_pct_b_tf; slope = lrL_slope_tf;
  r2 = lrL_r2_tf (ALL in NPZ for 15m/1h/4h/D — verified S1).
  lo = LR_BAND_ENTRY_LO (0.1); REGIME mode: lo = max(lo, LR_BAND_REGIME_MAX_PB 0.6).
  LONG:  pb <= lo and slope > 0  (sides gate: LONG always evaluated)
  SHORT: "S" in SIDES and pb >= 1-lo and slope < 0
  fire = r2 >= R2_MIN (0.7) and (long_ok or short_ok) → Signal(conviction 85).

Vec placement: entry SOURCE → OR into final entry_sig (live returns a Signal
that opens). Default ENABLED=False = no-op. Missing arrays (r2/slope/pb all
zero) → no fire on those bars (live requires all three non-None).
"""
from __future__ import annotations

import numpy as np


def entry_fire_mask(npz, n, is_long, cfg, safe):
    """Bool[n] True = LR band entry fires. None when disabled."""
    if not bool(getattr(cfg, "LR_BAND_ENTRY_ENABLED", False)):
        return None
    tf = str(getattr(cfg, "LR_BAND_ENTRY_TF", "4h") or "4h").strip()
    pb = safe(npz, f"lrL_pct_b_{tf}", n, np.nan)
    slope = safe(npz, f"lrL_slope_{tf}", n, np.nan)
    r2 = safe(npz, f"lrL_r2_{tf}", n, np.nan)
    have = np.isfinite(pb) & np.isfinite(slope) & np.isfinite(r2)
    lo = float(getattr(cfg, "LR_BAND_ENTRY_LO", 0.1))
    if bool(getattr(cfg, "LR_BAND_REGIME_ENABLED", False)):
        lo = max(lo, float(getattr(cfg, "LR_BAND_REGIME_MAX_PB", 0.6)))
    sides = str(getattr(cfg, "LR_BAND_ENTRY_SIDES", "L"))
    r2min = float(getattr(cfg, "LR_BAND_ENTRY_R2_MIN", 0.7))
    if is_long:
        ok = (pb <= lo) & (slope > 0)
    else:
        ok = ("S" in sides) & (pb >= 1.0 - lo) & (slope < 0)
    fire = have & (r2 >= r2min) & ok
    return np.asarray(fire, dtype=bool)
