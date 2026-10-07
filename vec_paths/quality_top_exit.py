"""
vec_paths/quality_top_exit.py — symmetric "exit at the top" partner for quality_bottom_entry.

USER MANDATE 2026-05-23: "IN AT BOTTOM, OUT AT TOP" — the bottom-entry detector
already exists (quality_bottom_entry.precompute_quality_bottom_long_mask) but had
no matching exit. Positions were getting closed by PPL_TP at +0.5%, R2_WT_VEL_SLOW
at break-even, peak_giveback, etc. — pennies instead of full moves.

This module reuses the EXISTING top-detection logic (precompute_quality_top_short_mask
in quality_bottom_entry.py) as a LONG EXIT signal. Same bar-level criteria that say
"this is a quality top to short" also say "this is the right time to exit a LONG".

Mirror for SHORT positions: precompute_quality_bottom_long_mask as a SHORT exit.

OUTPUT:
  precompute_quality_top_exit_mask(npz, cfg, is_long) -> np.ndarray[bool], shape (N,)
    True at bars where an open position of this side should be CLOSED.

CONFIG (SweepConfig + live config mirror):
  QUALITY_TOP_EXIT_ENABLED        (default False — opt-in kill switch off by default)
  QUALITY_TOP_EXIT_MIN_GAIN_PCT   (default 0.0 — require gain >= X% before firing;
                                   used to suppress premature exits on noise. Set to
                                   e.g. 1.5 to mirror HLR_TOP_MIN_GAIN_PCT.)

DESIGN NOTE: the mask itself is a bar-level signal; the gain-floor is enforced at the
exit-check site in v8_vec_sweep.py using pos_state.gain_pct. The mask doesn't know the
caller's entry price.
"""
from __future__ import annotations
from typing import Any, Dict
import numpy as np

from vec_paths.quality_bottom_entry import (
    precompute_quality_bottom_long_mask,
    precompute_quality_top_short_mask,
)


def precompute_quality_top_exit_mask(npz: Dict, cfg: Any, is_long: bool) -> np.ndarray:
    """Return per-bar exit signal mask for a position of the given side.

    is_long=True  → LONG exit fires when quality-top-short conditions are met
                    (HTF bearish, rally from 1h low, K_15m/1h overbought,
                    HA flip green→red, upper-band proximity, volume confirm)
    is_long=False → SHORT exit fires when quality-bottom-long conditions are met
                    (mirror image)
    """
    if not bool(getattr(cfg, "QUALITY_TOP_EXIT_ENABLED", False)):
        n = len(npz.get("close", []))
        return np.zeros(n, dtype=bool)
    if is_long:
        return precompute_quality_top_short_mask(npz, cfg).astype(bool)
    return precompute_quality_bottom_long_mask(npz, cfg).astype(bool)
