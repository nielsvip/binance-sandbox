"""check_entry_candidates_crypto__strict_stoch_gate.py

SHARED scalar+vectorized predicate for STRICT_STOCH_GATE — the signal-based-entry
final stoch confirmation in check_entry_candidates_for_account
(ez_positions_quick.py:15473-15480).

After the score/rec threshold passes for a SIGNAL-BASED entry, the 1m stoch must
confirm direction or the entry is BLOCKED:

FAITHFUL EXTRACTION of ez_positions_quick.py:15473-15480:

    _k1m = ind.get('stoch_k_1m', 50)
    _d1m = ind.get('stoch_d_1m', 50)
    if is_long and _k1m < _d1m:        BLOCK  (no should_trade)
    elif short and _k1m > _d1m:        BLOCK
    else:                              should_trade = True   (gate PASSES)

This module returns the PASS predicate: True == gate allows the entry through.
Pure per-bar test on NPZ fields stoch_k_1m, stoch_d_1m. The upstream score/rec
threshold is a separate concern (the caller only invokes this gate once the
score/rec check has already passed). Mirrors strategy_enhancements.py
_pyramid_fires single-core pattern so scalar and vec cannot drift.
"""
from typing import Tuple
import numpy as np


def _strict_stoch_gate_passes(k1m: float, d1m: float, is_long: bool) -> bool:
    """PURE per-bar test. True == gate PASSES (entry allowed). Mirrors
    ez_positions_quick.py:15475-15480 (block when k1m on wrong side of d1m)."""
    if is_long and k1m < d1m:
        return False
    if (not is_long) and k1m > d1m:
        return False
    return True


def check_strict_stoch_gate(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (passes, reason). reason is the BLOCK reason when
    it does NOT pass (matching the live log), empty when it passes."""
    def g(k):
        v = indicators.get(k, 50)
        try:
            return float(v) if v is not None else 50.0
        except (TypeError, ValueError):
            return 50.0
    k1m = g("stoch_k_1m")
    d1m = g("stoch_d_1m")
    if _strict_stoch_gate_passes(k1m, d1m, is_long):
        return True, ""
    side = "LONG" if is_long else "SHORT"
    op = "<" if is_long else ">"
    return False, f"STRICT_STOCH_GATE_BLOCK_{side}_k1={k1m:.1f}{op}d1={d1m:.1f}"


def check_strict_stoch_gate_vec(config, k1m_arr, d1m_arr, is_long):
    """VECTORIZED per-bar PASS mask. SAME logic as scalar. True == entry allowed."""
    k = np.asarray(k1m_arr, dtype=float)
    d = np.asarray(d1m_arr, dtype=float)
    if is_long:
        return ~(k < d)
    return ~(k > d)
