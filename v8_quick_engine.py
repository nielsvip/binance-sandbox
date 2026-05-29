"""v8_quick_engine — compat shim re-exporting the modern Tier-1 vec engine.

Re-exports QuickConfig + simulate from quick_engine_compat (routes to
vec_engine_v1.VecEngine). See quick_engine_compat.py for the BAN history of the
original OPUS_VOMIT engine.

────────────────────────────────────────────────────────────────────────────
MTF_ATR_TRAIL wiring (2026-05-29 USER MANDATE) — live-parity ratchet via the
SHARED helper vec_paths/mtf_atr_trail.py. Mirror of the Tier-2 DISC-MTF_ATR_TRAIL
block at backtest_v8_engine.py:4052-4085.

PARITY CAVEAT (read this — it is load-bearing):
  This module is a 2-line re-export shim; it owns NO per-bar loop, NO open-
  position table and NO NPZ accessor. Those live in the real Tier-1 engine
  old/vec_engine_v1.VecEngine.simulate(), which this task forbids editing.
  A genuine per-bar ratchet can ONLY fire from inside that bar loop — firing it
  at the simulate() boundary (only the final result + per-trade JSONL are
  visible there) would FABRICATE closes, violating the NO-LIES mandate.

  Two facts make this safe and honest:
   1. The real engine ALREADY fires the live MTF_ATR_TRAIL stop per-bar: see
      old/vec_engine_v1.py:2840-2868 (_check_mtf_compound_exit), gated on the
      SAME master switch MTF_EXIT_USE_COMPOUND. So Tier-1 is NOT missing the
      exit — it is wired, just through the bundled compound-exit checker rather
      than the standalone vec_paths/mtf_atr_trail.py helper.
   2. This shim binds the standalone helper (update_and_check / mtf_atr_trail_tf)
      and a per-position-state ratchet (mtf_atr_trail_step) keyed by
      position_key, identical in shape to the live trade_manager
      .mtf_compound_exit_state[pk]['trail'] and the Tier-2 block. The engine bar
      loop can call mtf_atr_trail_step() directly; the gate
      (mtf_atr_trail_enabled) keeps it inert unless BOTH knobs are True.

  So: the requested helper is imported and exposed (not reimplemented), the
  enable gate matches live, per-position trail state is persisted in a dict
  keyed by position_key. The ONE thing this shim cannot do without editing
  old/vec_engine_v1.py is splice the call into the bar loop — and that splice
  already exists for the equivalent compound-exit path under the same gate.
────────────────────────────────────────────────────────────────────────────
"""
from quick_engine_compat import QuickConfig, simulate

from vec_paths.mtf_atr_trail import (
    update_and_check as _mtfat_update_and_check,
    mtf_atr_trail_tf as _mtfat_tf,
    mtf_atr_trail_enabled as _mtfat_enabled,
)

mtf_atr_trail_enabled = _mtfat_enabled


def mtf_atr_trail_step(cfg, mode, state, position_key, entry_price, current_price, indicator_row, is_long):
    """Per-bar MTF_ATR_TRAIL ratchet for one open position (live-parity).

    Mirror of backtest_v8_engine.py:4060-4083 using the SHARED helper. `state`
    is the cross-bar dict keyed by position_key (e.g. engine
    pos_state.mtf_compound_exit_state) — same role as live
    trade_manager.mtf_compound_exit_state[pk]. `indicator_row` is the NPZ
    indicator dict/accessor for the current bar (provides atr_<tf>).
    Returns (fired, reason, trail_level). Inert (False) unless BOTH
    MTF_EXIT_USE_COMPOUND and MTF_ATR_TRAIL_ENABLED are True.
    """
    if not _mtfat_enabled(cfg):
        return False, "", 0.0
    tf = _mtfat_tf(cfg, mode)
    mult = float(getattr(cfg, "MTF_ATR_TRAIL_MULT", 2.0))
    try:
        atr = float(indicator_row.get(f"atr_{tf}", 0) or 0)
    except AttributeError:
        atr = float(indicator_row(f"atr_{tf}", 0) or 0)
    pos_state = state.setdefault(position_key, {"trail": 0.0})
    return _mtfat_update_and_check(pos_state, float(entry_price or 0), float(current_price or 0), atr, bool(is_long), mult, tf)


__all__ = ["QuickConfig", "simulate", "mtf_atr_trail_enabled", "mtf_atr_trail_step"]
