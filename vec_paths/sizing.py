"""
vec_paths/sizing.py — Combined position-sizing pipeline.

Sources:
  - position_evaluator.compute_trade_qty_core() (WT_HTF_DISCOUNT + HEDGE_SIZE_CAP + MIN_POS)
  - ez_manage.py execute_now() sizing block (VOL_TARGET, DD_KELLY, WINNER_AUGMENT bonus)
  - VecEngine._compute_sizing() (already has VOL_TARGET/DD_KELLY/GOLDEN_RULE)

Public API:
    compute_position_size(store, bar_idx, side, mode, cfg, dd_state, running_gain,
                          pos_state=None, is_hedge=False, origin_pos_usd=0.0,
                          is_rz_entry=False, base_qty_override=None) -> float

Returns a scalar position size (normalized 1.0 = START_POSITION_SIZE / current_price).
The returned value is the FINAL qty after all sizing mutations. Pass to VecEngine trade open
instead of _compute_sizing to get WT_HTF_DISCOUNT + HEDGE_SIZE_CAP parity.

DIFFERENCE vs VecEngine._compute_sizing():
  _compute_sizing() returns a SIZE MULTIPLIER (1.0 = standard), not an absolute qty.
  This module returns an ABSOLUTE QTY (units) after applying WT_HTF_DISCOUNT and
  HEDGE_SIZE_CAP on top of the base qty derived from START_POSITION_SIZE / price.

When should you call this vs _compute_sizing()?
  - For normal OPEN/AUGMENT/REENTRY paths: use _compute_sizing_v2() (wraps this module)
    which returns the same multiplier API as _compute_sizing.
  - For HEDGE_OPEN: must pass is_hedge=True + origin_pos_usd.
  - For sweep comparisons: both are wired; use _compute_sizing_v2 as opt-in override.

Modifiers (from position_evaluator.py MODIFIER_NAMES):
  0 = NONE
  1 = WT_HTF_DISCOUNT_x0.5  (1/2 HTF aligned)
  2 = WT_HTF_DISCOUNT_x0.3  (0/2 HTF aligned)
  3 = HEDGE_SIZE_CAP
  4 = MIN_POS_FLOOR

WINNER_AUGMENT bonus:
  Source: OPUS_VOMIT.py E6 block. Increases qty by WA_SIZE_MULT when position gain
  has grown by WA_GAIN_GROWTH_REQ since last augment, up to WA_MAX_AUGMENTS.
  In the sizing pipeline this is a multiplier on the OPEN qty (separate from augment
  state tracking in VecEngine.simulate).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from vec_engine_v1 import VecConfig, _NPZStore, _PositionState

# Re-export from position_evaluator for backward-compat
try:
    from position_evaluator import (
        compute_trade_qty_core,
        compute_trade_qty_vec,
        MOD_NONE,
        MOD_WT_HTF_DISCOUNT_HALF,
        MOD_WT_HTF_DISCOUNT_HARSH,
        MOD_HEDGE_SIZE_CAP,
        MOD_MIN_POS_FLOOR,
        MODIFIER_NAMES,
    )
    _PE_AVAILABLE = True
except ImportError:
    _PE_AVAILABLE = False
    MOD_NONE = 0
    MOD_WT_HTF_DISCOUNT_HALF = 1
    MOD_WT_HTF_DISCOUNT_HARSH = 2
    MOD_HEDGE_SIZE_CAP = 3
    MOD_MIN_POS_FLOOR = 4
    MODIFIER_NAMES = {
        MOD_NONE: 'NONE',
        MOD_WT_HTF_DISCOUNT_HALF: 'WT_HTF_DISCOUNT_x0.5',
        MOD_WT_HTF_DISCOUNT_HARSH: 'WT_HTF_DISCOUNT_x0.3',
        MOD_HEDGE_SIZE_CAP: 'HEDGE_SIZE_CAP',
        MOD_MIN_POS_FLOOR: 'MIN_POS_FLOOR',
    }


def _sf(x: Any, default: float = 0.0) -> float:
    """Safe float."""
    try:
        if x is None:
            return default
        v = float(x)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def _htf_aligned_count(store: Any, bar_idx: int, is_long: bool) -> int:
    """Count of HTF (4h, D) WT timeframes aligned with trade direction.
    Matches position_evaluator._wt_htf_aligned_count_scalar."""
    w1_4h = store.f('wt1_4h', bar_idx, 0.0)
    w2_4h = store.f('wt2_4h', bar_idx, 0.0)
    w1_D = store.f('wt1_D', bar_idx, 0.0)
    w2_D = store.f('wt2_D', bar_idx, 0.0)
    if is_long:
        return int(w1_4h > w2_4h and w1_4h != 0) + int(w1_D > w2_D and w1_D != 0)
    return int(w1_4h < w2_4h and w1_4h != 0) + int(w1_D < w2_D and w1_D != 0)


def compute_position_size(
    store: Any,
    bar_idx: int,
    side: str,
    mode: str,
    cfg: Any,
    dd_state: Dict,
    running_gain: float,
    pos_state: Any = None,
    is_hedge: bool = False,
    origin_pos_usd: float = 0.0,
    is_rz_entry: bool = False,
    base_qty_override: Optional[float] = None,
) -> Tuple[float, int]:
    """Full sizing pipeline — returns (final_qty, modifier_id).

    Steps (in order):
      1. Base qty = START_POSITION_SIZE / price (or base_qty_override)
      2. VOL_TARGET scalar (from _compute_sizing)
      3. DD_KELLY scalar
      4. GOLDEN_RULE scalar (from _compute_sizing)
      5. WINNER_AUGMENT bonus (when pos_state has gain >= WA_MIN_GAIN_PCT)
      6. HEDGE_SIZE_CAP (cap hedge qty at origin_pos_usd * HEDGE_MAX_PCT_OF_LOSER / price)
      7. WT_HTF_DISCOUNT (× 0.5 / × 0.3 based on 4h+D alignment)
      8. MIN_POSITION_SIZE floor

    Returns (qty_absolute, modifier_id) where modifier_id is from MODIFIER_NAMES.
    """
    price = store.f('close', bar_idx, 0.0)
    if price <= 0:
        return 0.0, MOD_NONE

    start_pos_size = float(getattr(cfg, 'START_POSITION_SIZE', 600.0))

    if base_qty_override is not None:
        base_qty = float(base_qty_override)
    else:
        base_qty = start_pos_size / price

    # ── Steps 2-4: size scalar from _compute_sizing logic ──────────────────
    sz_mult = 1.0

    # VOL_TARGET sizing
    if getattr(cfg, 'VOL_TARGET_ENABLED', False):
        rv = store.f(getattr(cfg, 'VOL_TARGET_FIELD', 'yz_vol_60_d'), bar_idx, 0.0)
        if rv > 0:
            s = float(getattr(cfg, 'VOL_TARGET_PCT', 0.02)) / rv
            lo = float(getattr(cfg, 'VOL_TARGET_LOW_CAP', 0.5))
            hi = float(getattr(cfg, 'VOL_TARGET_HIGH_CAP', 2.0))
            sz_mult *= max(lo, min(hi, s))

    # DD_KELLY sizing
    if getattr(cfg, 'DD_KELLY_ENABLED', False):
        dd = float(dd_state.get('dd_pct', 0.0))
        if dd <= -20.0:
            sz_mult *= float(getattr(cfg, 'DD_KELLY_TIER3_PCT', 0.125))
        elif dd <= -15.0:
            sz_mult *= float(getattr(cfg, 'DD_KELLY_TIER2_PCT', 0.25))
        elif dd <= -10.0:
            sz_mult *= float(getattr(cfg, 'DD_KELLY_TIER1_PCT', 0.5))

    # GOLDEN_RULE sizing multiplier (highest-TF breakout wins)
    if getattr(cfg, 'GOLDEN_RULE_ENABLED', True):
        best_gr = 1.0
        levels = [
            ('15m', getattr(cfg, 'GOLDEN_RULE_DC_15M_ENABLED', True), getattr(cfg, 'GOLDEN_RULE_BB_15M_ENABLED', True), getattr(cfg, 'GOLDEN_RULE_MULT_15M', 1.0)),
            ('1h', getattr(cfg, 'GOLDEN_RULE_DC_1H_ENABLED', True), getattr(cfg, 'GOLDEN_RULE_BB_1H_ENABLED', True), getattr(cfg, 'GOLDEN_RULE_MULT_1H', 1.5)),
            ('4h', getattr(cfg, 'GOLDEN_RULE_DC_4H_ENABLED', True), getattr(cfg, 'GOLDEN_RULE_BB_4H_ENABLED', True), getattr(cfg, 'GOLDEN_RULE_MULT_4H', 2.0)),
            ('D', getattr(cfg, 'GOLDEN_RULE_DC_D_ENABLED', True), getattr(cfg, 'GOLDEN_RULE_BB_D_ENABLED', True), getattr(cfg, 'GOLDEN_RULE_MULT_D', 3.0)),
        ]
        is_long = (side == 'LONG')
        for tf, dc_en, bb_en, mult in levels:
            if dc_en:
                dc_pos = store.f(f'dc_position_{tf}', bar_idx, 0.5)
                if (is_long and dc_pos > 0.7) or (not is_long and dc_pos < 0.3):
                    best_gr = max(best_gr, mult)
            if bb_en:
                bb_pctb = store.f(f'bb_pct_b_{tf}', bar_idx, 0.5)
                if (is_long and bb_pctb >= 0.8) or (not is_long and bb_pctb <= 0.2):
                    best_gr = max(best_gr, mult)
        sz_mult *= best_gr

    qty = base_qty * sz_mult

    # ── Step 5: WINNER_AUGMENT bonus ─────────────────────────────────────────
    # Source: OPUS_VOMIT E6 block + ez_manage.py:11288.
    # Average-in bonus when position gain has grown by WA_GAIN_GROWTH_REQ.
    # Applied to NEW size (separate from position tracking in simulate()).
    if pos_state is not None and getattr(cfg, 'WINNER_AUGMENT_ENABLED', False):
        _wa_min = float(getattr(cfg, 'WINNER_AUGMENT_MIN_GAIN_PCT', 2.0))
        _wa_mult = float(getattr(cfg, 'WINNER_AUGMENT_SIZE_MULT', 1.5))
        pos_gain = float(getattr(pos_state, 'gain_pct', 0.0))
        if pos_gain >= _wa_min:
            qty *= _wa_mult

    # ── Steps 6-8: position_evaluator pipeline ───────────────────────────────
    modifier = MOD_NONE

    # Step 6. HEDGE_SIZE_CAP
    if is_hedge and origin_pos_usd > 0:
        cap_pct = float(getattr(cfg, 'HEDGE_MAX_PCT_OF_LOSER', 1.0))
        max_qty = (origin_pos_usd * cap_pct) / price
        if qty > max_qty:
            qty = max_qty
            modifier = MOD_HEDGE_SIZE_CAP

    # Step 7. WT_HTF_DISCOUNT (non-hedge, non-RZ entries only)
    if not is_hedge and not is_rz_entry and getattr(cfg, 'WT_HTF_DISCOUNT_ENABLED', True):
        is_long = (side == 'LONG')
        htf_al = _htf_aligned_count(store, bar_idx, is_long)
        if htf_al < 2:
            disc = 0.5 if htf_al == 1 else 0.3
            qty *= disc
            if modifier == MOD_NONE:
                modifier = MOD_WT_HTF_DISCOUNT_HALF if htf_al == 1 else MOD_WT_HTF_DISCOUNT_HARSH

    # Step 8. MIN_POSITION_SIZE floor
    min_pos = float(getattr(cfg, 'MIN_POSITION_SIZE', 55.0))
    min_qty = min_pos / price
    if qty < min_qty:
        qty = min_qty
        if modifier == MOD_NONE:
            modifier = MOD_MIN_POS_FLOOR

    return qty, modifier


def compute_size_multiplier(
    store: Any,
    bar_idx: int,
    side: str,
    mode: str,
    cfg: Any,
    dd_state: Dict,
    running_gain: float,
    pos_state: Any = None,
    is_hedge: bool = False,
    origin_pos_usd: float = 0.0,
    is_rz_entry: bool = False,
) -> float:
    """Like compute_position_size but returns a MULTIPLIER (1.0 = standard).
    Drop-in replacement for VecEngine._compute_sizing() that adds WT_HTF_DISCOUNT
    and HEDGE_SIZE_CAP on top of the existing scalar logic.

    NOTE: WT_HTF_DISCOUNT and HEDGE_SIZE_CAP cannot be expressed as a simple
    multiplier on the existing _compute_sizing result because they depend on the
    absolute price and min_pos_size floor. This function computes both and returns
    qty / base_qty as the effective multiplier."""
    price = store.f('close', bar_idx, 0.0)
    if price <= 0:
        return 1.0
    start_pos = float(getattr(cfg, 'START_POSITION_SIZE', 600.0))
    base_qty = start_pos / max(price, 1e-9)
    qty, _ = compute_position_size(
        store, bar_idx, side, mode, cfg, dd_state, running_gain,
        pos_state=pos_state, is_hedge=is_hedge, origin_pos_usd=origin_pos_usd,
        is_rz_entry=is_rz_entry, base_qty_override=base_qty,
    )
    return qty / max(base_qty, 1e-9)
