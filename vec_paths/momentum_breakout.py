"""vec_paths/momentum_breakout.py — IMMEDIATE-BREAKOUT entry for crypto.

User mandate 2026-05-26: existing entry paths (GOLDEN_RULE PATH B, B-blocks,
WT_3M_FORCE_OPEN) all fire LATE — diagnostic showed 4-16hr lag on 30 sustained
5%+ moves in 24h. The vec_paths/dc_break.py is TRADIER-only. This module is the
crypto sibling: fires the bar after price breaks the prior 1h DC high/low,
confirmed by base-TF (3m) WT momentum in the same direction.

Decision (per-bar, vectorized):
  LONG  fires when close > prior 1h DC high (price expanded the channel)
        AND wt1_3m > wt2_3m (3m WT bullish)
        AND (optional) NOT already-overextended on 4h (close < 4h DC high * 1.02)
  SHORT fires symmetrically.

Defaults OFF; gate via cfg.MOMENTUM_BREAKOUT_ENABLED. Wiring lives in
v8_vec_sweep.py entry-decision union next to GOLDEN_RULE PATH A.

Knobs:
  MOMENTUM_BREAKOUT_ENABLED            bool   default False
  MOMENTUM_BREAKOUT_REQUIRE_3M_WT      bool   default True
  MOMENTUM_BREAKOUT_BUFFER_PCT         float  default 0.0    (0.0 = exact bar of break)
  MOMENTUM_BREAKOUT_TF                 str    default "1h"   (which DC channel)
  MOMENTUM_BREAKOUT_BLOCK_OVEREXTENDED bool   default True   (4h not maxed out)
  MOMENTUM_BREAKOUT_OVEREXTEND_PCT     float  default 1.02

Reason string emitted:
  MOMENTUM_BREAKOUT_LONG_{TF}_px<close>_dc<prev_dc_high>
  MOMENTUM_BREAKOUT_SHORT_{TF}_px<close>_dc<prev_dc_low>
"""
from __future__ import annotations
import numpy as np
from typing import Dict, Tuple


def build_momentum_breakout_masks(
    npz: Dict[str, np.ndarray],
    cfg,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (long_fire_mask, short_fire_mask) — boolean arrays of len n_bars.

    Vectorized — no per-bar Python loop. Designed to be called once per sweep
    cell and ANDed into the entry union in v8_vec_sweep.
    """
    if not bool(getattr(cfg, "MOMENTUM_BREAKOUT_ENABLED", False)):
        n = len(npz.get("close", np.array([])))
        return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)

    tf = str(getattr(cfg, "MOMENTUM_BREAKOUT_TF", "1h"))
    buf = float(getattr(cfg, "MOMENTUM_BREAKOUT_BUFFER_PCT", 0.0))
    require_3m_wt = bool(getattr(cfg, "MOMENTUM_BREAKOUT_REQUIRE_3M_WT", True))
    block_over = bool(getattr(cfg, "MOMENTUM_BREAKOUT_BLOCK_OVEREXTENDED", True))
    over_pct = float(getattr(cfg, "MOMENTUM_BREAKOUT_OVEREXTEND_PCT", 1.02))

    close = npz.get("close")
    if close is None:
        return np.zeros(0, dtype=bool), np.zeros(0, dtype=bool)
    n = len(close)
    long_fire = np.zeros(n, dtype=bool)
    short_fire = np.zeros(n, dtype=bool)

    dc_high = npz.get(f"dc_high_{tf}")
    dc_low = npz.get(f"dc_low_{tf}")
    if dc_high is None or dc_low is None or len(dc_high) != n:
        return long_fire, short_fire

    # PREVIOUS bar's DC level — fires on the bar where the close FIRST exceeds
    # the prior channel. Without this, dc_high auto-extends with current bar
    # and price never "breaks above" (the bug that ate live for months).
    prev_dc_high = np.roll(dc_high, 1)
    prev_dc_high[0] = dc_high[0]
    prev_dc_low = np.roll(dc_low, 1)
    prev_dc_low[0] = dc_low[0]

    above_long = (prev_dc_high > 0) & (close > prev_dc_high * (1.0 + buf))
    below_short = (prev_dc_low > 0) & (close < prev_dc_low * (1.0 - buf))

    # Edge mask — only fire on the BAR where break first happens (avoid re-firing on each subsequent bar)
    above_long_edge = above_long & ~np.roll(above_long, 1)
    above_long_edge[0] = above_long[0]
    below_short_edge = below_short & ~np.roll(below_short, 1)
    below_short_edge[0] = below_short[0]

    if require_3m_wt:
        wt1_3m = npz.get("wt1_3m")
        wt2_3m = npz.get("wt2_3m")
        if wt1_3m is not None and wt2_3m is not None and len(wt1_3m) == n:
            wt_long_ok = wt1_3m > wt2_3m
            wt_short_ok = wt1_3m < wt2_3m
            above_long_edge &= wt_long_ok
            below_short_edge &= wt_short_ok

    if block_over:
        dc_high_4h = npz.get("dc_high_4h")
        dc_low_4h = npz.get("dc_low_4h")
        if dc_high_4h is not None and len(dc_high_4h) == n:
            # block LONG when price is already at the 4h channel top
            not_over_long = close < dc_high_4h * over_pct
            above_long_edge &= not_over_long
        if dc_low_4h is not None and len(dc_low_4h) == n:
            not_over_short = close > dc_low_4h / over_pct
            below_short_edge &= not_over_short

    return above_long_edge, below_short_edge


def build_reason(side: str, tf: str, price: float, prev_dc: float) -> str:
    return f"MOMENTUM_BREAKOUT_{side}_{tf}_px{price:.2f}_dc{prev_dc:.2f}"
