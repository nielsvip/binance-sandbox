"""
vec_paths/bb_recovery.py — BB_RECOVERY exit path (vectorized).

LIVE SOURCE:
  tradier_manage.py:9275-9307 (inside execute_now, before NOLOSS gate for REDUCE):
    if BB_RECOVERY_EXIT_ENABLED_TRADIER:
      _br_entry  = entry_price
      _bbh_1h    = bb_high_1h      (live name — same as bb_upper_1h in NPZ)
      _bbl_1h    = bb_low_1h       (same as bb_lower_1h)
      _br_ha     = ha_3m           ("red" / "green" / "neutral")
      _br_k3     = stoch_k_3m
      _br_k3p    = stoch_k_3m_prev (k_3m_prev alias in live)
      _br_tol    = BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER (0.30%)
      For LONG: entry > bbh_1h AND price within tol of entry AND (ha_3m=red OR k3 < k3p)
      For SHORT: entry < bbl_1h AND price within tol of entry AND (ha_3m=green OR k3 > k3p)
      → _bb_recov_bypass = True → allow close despite NOLOSS gate

PURPOSE:
  When a position was entered OUTSIDE the BB (entry_price > bb_upper_1h for LONG),
  and price has pulled back to within tolerance of entry price AND a 3m reversal
  signal fires → allow exit even at loss (bypass NO_LOSS gate).

VEC APPROXIMATION:
  In the vectorized engine we don't have "entry was stranded outside BB" statefully
  tracked. We check: entry_price (from pos_state.entry_price) vs bb_upper_1h at
  entry bar vs current bar's bb_upper_1h. Since NPZ is forward-filled, bb_upper_1h
  at current bar is the most recent hourly value. We compare entry_price to the
  NPZ bb_upper_1h at the CURRENT bar (conservative — live uses current indicators).

  LIMITATION: entry_price may have been inside BB at entry time even if it's now
  outside. The exact "was entered outside BB" guard requires looking back at the
  entry bar's indicators. We approximate by checking entry vs current bb_upper_1h.
  This makes the vec version slightly more aggressive (may fire when live would not).

NPZ FIELDS:
  bb_upper_1h  (= bb_high_1h in live indicators)
  bb_lower_1h  (= bb_low_1h in live)
  ha_3m        (decoded to "red" / "green" / "neutral")
  stoch_k_3m
  stoch_k_3m_prev   (present in NPZ as stoch_k_3m_prev when precomputed)
  atr_3m       (only needed if BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT > 0)

RETURNS (check_bb_recovery_exit):
  dict with keys:
    side       — "LONG" or "SHORT"
    reason     — str matching live BB_RECOVERY_EXIT_BYPASS format
    bypass     — bool (always True when returned)
  or None if conditions not met.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def check_bb_recovery_exit(
    store,
    bar_idx: int,
    pos_state,
    mode: str,
    cfg,
) -> Optional[Dict[str, Any]]:
    """BB_RECOVERY exit bypass (tradier + crypto, default OFF).

    Args:
        store:     _NPZStore for this symbol.
        bar_idx:   Current bar index.
        pos_state: _PositionState (must be open=True with entry_price set).
        mode:      "crypto" or "tradier".
        cfg:       VecConfig with BB_RECOVERY_EXIT_* fields.

    Returns:
        dict if BB_RECOVERY bypass fires, None otherwise.
        dict keys:
          side, reason, bypass (=True)
    """
    if mode == "tradier":
        enabled = getattr(cfg, "BB_RECOVERY_EXIT_ENABLED_TRADIER", True)
    else:
        enabled = getattr(cfg, "BB_RECOVERY_EXIT_ENABLED", False)
    if not enabled:
        return None
    if pos_state is None or not pos_state.open:
        return None
    entry_price = pos_state.entry_price
    if entry_price <= 0:
        return None
    side = pos_state.side
    is_long = (side == "LONG")
    tol_pct = getattr(cfg, "BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER", 0.30)
    tol_atr_mult = getattr(cfg, "BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER", 0.0)
    if tol_atr_mult > 0:
        atr_3m = store.f("atr_3m", bar_idx, 0.0)
        if atr_3m > 0:
            tol_abs = tol_atr_mult * atr_3m
        else:
            tol_abs = entry_price * tol_pct / 100.0
    else:
        tol_abs = entry_price * tol_pct / 100.0
    price = store.price(bar_idx)
    if price <= 0:
        return None
    within_tol = abs(price - entry_price) <= tol_abs
    if not within_tol:
        return None
    bbh_1h = store.f("bb_upper_1h", bar_idx, 0.0)
    bbl_1h = store.f("bb_lower_1h", bar_idx, 0.0)
    ha_3m = store.s("ha_3m", bar_idx)
    k3 = store.f("stoch_k_3m", bar_idx, 50.0)
    k3p = store.f("stoch_k_3m_prev", bar_idx, k3)
    reversal = False
    if is_long:
        if bbh_1h <= 0 or entry_price <= bbh_1h:
            return None
        if ha_3m == "red" or k3 < k3p:
            reversal = True
    else:
        if bbl_1h <= 0 or entry_price >= bbl_1h:
            return None
        if ha_3m == "green" or k3 > k3p:
            reversal = True
    if not reversal:
        return None
    gain_pct = pos_state.gain_pct
    if is_long:
        reason = (
            f"BB_RECOVERY_EXIT_BYPASS: LONG entry={entry_price:.4f}>bb_upper_1h={bbh_1h:.4f}"
            f", recovered to {price:.4f} (tol={tol_abs:.4f}), 3m reversal — gain={gain_pct:.2f}%"
        )
    else:
        reason = (
            f"BB_RECOVERY_EXIT_BYPASS: SHORT entry={entry_price:.4f}<bb_lower_1h={bbl_1h:.4f}"
            f", recovered to {price:.4f} (tol={tol_abs:.4f}), 3m reversal — gain={gain_pct:.2f}%"
        )
    return {
        "side": side,
        "reason": reason,
        "bypass": True,
    }
