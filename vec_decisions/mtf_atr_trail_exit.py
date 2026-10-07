"""Shared scalar+vectorized predicate for the LIVE exit MTF_ATR_TRAIL_{TF}_x{mult}.

2026-09-28 EXIT VECTORIZATION PARITY (HANDOFF_EXIT_VECTORIZATION_PARITY.md #2, P0):
faithful vec twin of the live ratcheting ATR trail stop. Replaces the fabricated
v12_quick scaffolding (`atr > 1.0` OR'd into exit_mask + fire-if-TF-not-default),
which was synthetic-distinctness noise, not the live decision.

LIVE source of truth: ez_manage.py:47684-47699 (MTF compound exit block, branch 1),
emitting reason f"MTF_ATR_TRAIL_{tf}_x{mult}_lvl{trail:.6f}" via execute_now CLOSE.

The PURE per-bar decision extracted here (long; short is the mirror):

    cand   = max(entry_price - mult*atr, price - mult*atr)
    trail  = max(trail_prev, cand) if trail_prev > 0 else cand   # ratchet, never loosens
    fire   = trail > 0 and price < trail

Caller-concern preconditions that are NOT part of the core predicate (mirror of the
live gating in ez_manage.py:47655-47684, to be enforced by the simulate_one caller):
    - per-sym MTF_EXIT_USE_COMPOUND is True (default False)
    - per-sym MTF_ATR_TRAIL_ENABLED is True (default False)
    - min-hold satisfied OR dc_15m breach override (live `_min_hold_ok_for_exit`)
    - atr > 0, entry_price > 0, price > 0
    - trail state is PER POSITION: live stores it in
      trade_manager.mtf_compound_exit_state[position_key] and pops it ONLY after a
      SUCCESSFUL execute_now close (ez_manage.py:47791). Backtest twin must reset
      trail state to 0.0 when a position closes (any reason) and start fresh at the
      next open. KNOWN LIVE WART (documented, do NOT mirror): if the close is
      BLOCKED/errors, live keeps the stale trail and refires every eval — that is
      the 4279-blocks/day churn signature from the STRICT_VEC_PARITY era, an
      unreachable state in the backtest because backtest closes never fail.
    - live-only orchestration NOT vectorized: MTF_EXIT_MIN_OPEN_TS startup gate
      (positions opened before manager startup ride legacy exits).

INDICATOR / NPZ FIELDS READ (per bar):
    close (price), atr_{MTF_ATR_TRAIL_TF}

CONFIG THRESHOLDS READ:
    MTF_EXIT_USE_COMPOUND        (master, default False)
    MTF_ATR_TRAIL_ENABLED        (default False)
    MTF_ATR_TRAIL_TF             (default "15m"; OFF disables)
    MTF_ATR_TRAIL_MULT           (default 2.5)
"""
import numpy as np


def atr_trail_update(trail_prev, entry_price, price, atr, mult, is_long):
    """Pure ratchet update — faithful to ez_manage.py:47687-47697.

    Returns the new trail level (0.0 = no trail yet). No fire decision here."""
    if atr <= 0 or entry_price <= 0 or price <= 0:
        return trail_prev
    if is_long:
        cand = max(entry_price - mult * atr, price - mult * atr)
        return max(trail_prev, cand) if trail_prev > 0 else cand
    cand = min(entry_price + mult * atr, price + mult * atr)
    return min(trail_prev, cand) if trail_prev > 0 else cand


def atr_trail_fires(trail, price, is_long):
    """Pure fire predicate — faithful to ez_manage.py:47689/47696.

    Live evaluates fire against the PRE-update ratchet then persists the update;
    the update in ez_manage mutates state BEFORE the compare, so callers must
    call atr_trail_update first and pass the updated trail here (same order)."""
    if trail <= 0 or price <= 0:
        return False
    return price < trail if is_long else price > trail


def atr_trail_scan(prices, atrs, entry_bar, entry_price, mult, is_long):
    """Backtest helper: walk bars [entry_bar..n) for ONE position, return the first
    bar index where the trail fires, or -1. Stateful scan sharing the pure core —
    usable by simulate_one without inlining the predicate (vec-identical hook)."""
    trail = 0.0
    n = len(prices)
    for i in range(int(entry_bar), n):
        px = float(prices[i])
        atr = float(atrs[i]) if i < len(atrs) else 0.0
        trail = atr_trail_update(trail, entry_price, px, atr, mult, is_long)
        if atr_trail_fires(trail, px, is_long):
            return i
    return -1


def atr_trail_levels_vec(prices, atrs, entry_price, mult, is_long):
    """Vectorized trail levels for one position segment (prices/atrs already sliced
    from entry bar). Running max/min of (bound(entry, px) -/+ mult*atr) — identical
    to iterating atr_trail_update. Returns (levels, fires) arrays."""
    px = np.asarray(prices, dtype=float)
    atr = np.asarray(atrs, dtype=float)
    valid = (atr > 0) & (px > 0) & (entry_price > 0)
    if is_long:
        cand = np.where(valid, np.maximum(entry_price - mult * atr, px - mult * atr), -np.inf)
        levels = np.maximum.accumulate(cand)
        fires = (levels > 0) & (px < levels)
    else:
        cand = np.where(valid, np.minimum(entry_price + mult * atr, px + mult * atr), np.inf)
        levels = np.minimum.accumulate(cand)
        fires = np.isfinite(levels) & (levels > 0) & (px > levels)
    return levels, fires
