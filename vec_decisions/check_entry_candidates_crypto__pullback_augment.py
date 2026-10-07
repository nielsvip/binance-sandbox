"""check_entry_candidates_crypto__pullback_augment.py

SHARED scalar+vectorized predicate for PULLBACK_AUGMENT_QUICK in
check_entry_candidates_for_account (ez_positions_quick.py:15047-15053).

Allows augmenting a SMALL, slightly-negative position when 3m stoch shows a
pullback (so the average-down is into a dip, not a falling knife).

FAITHFUL EXTRACTION of ez_positions_quick.py:15048-15053:

    allow_neg_augment = False
    if pos_amt>0 and current_gain<=0 and is_small:
        is_pullback = (is_long and k_3m<35) or (short and k_3m>65)
        if is_pullback:
            allow_neg_augment = True

STATE SEAM: the GATE conditions `pos_amt>0`, `current_gain<=0`, `is_small`
(pos_val < 1.5*START_POSITION_SIZE) are per-position SIM STATE the backtest engine
already maintains — they are NOT per-bar indicator predicates, so they are passed
in as caller flags (exactly like the pyramid template passes is_long). The PURE
per-bar SIGNAL is the k_3m pullback test on the NPZ field stoch_k_3m; the full
fire = signal AND the state gates. Both expressed in one core so scalar and vec
cannot drift (mirrors strategy_enhancements.py _pyramid_fires).
"""
from typing import Tuple
import numpy as np

_K3M_LONG_PULLBACK = 35.0
_K3M_SHORT_PULLBACK = 65.0


def _pullback_signal(k_3m: float, is_long: bool) -> bool:
    """PURE per-bar pullback signal. Mirrors ez_positions_quick.py:15050."""
    if is_long:
        return k_3m < _K3M_LONG_PULLBACK
    return k_3m > _K3M_SHORT_PULLBACK


def _pullback_augment_fires(k_3m: float, is_long: bool, has_position: bool,
                            gain_non_positive: bool, is_small: bool) -> bool:
    """Full fire = state gates (pos>0, gain<=0, small) AND the pullback signal.
    Mirrors ez_positions_quick.py:15048-15051."""
    if not (has_position and gain_non_positive and is_small):
        return False
    return _pullback_signal(k_3m, is_long)


def check_pullback_augment(config, indicators: dict, is_long: bool,
                           has_position: bool, gain_non_positive: bool,
                           is_small: bool, k_3m: float = None) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (allow_neg_augment, reason). Caller supplies the
    state flags (has_position / gain_non_positive / is_small) from sim/live position
    state. k_3m read from indicators if not passed explicitly."""
    if k_3m is None:
        v = (indicators or {}).get("stoch_k_3m", 50.0)
        try:
            k_3m = float(v) if v is not None else 50.0
        except (TypeError, ValueError):
            k_3m = 50.0
    if not _pullback_augment_fires(float(k_3m), is_long, has_position,
                                   gain_non_positive, is_small):
        return False, ""
    return True, f"PULLBACK_AUGMENT_QUICK_k3={float(k_3m):.1f}"


def check_pullback_augment_vec(config, k_3m_arr, is_long, has_position_arr,
                               gain_non_positive_arr, is_small_arr):
    """VECTORIZED per-bar fire mask. SAME logic as scalar. The three state arrays
    are per-bar booleans the engine synthesizes from simulated position state."""
    k3 = np.asarray(k_3m_arr, dtype=float)
    hp = np.asarray(has_position_arr, dtype=bool)
    gn = np.asarray(gain_non_positive_arr, dtype=bool)
    sm = np.asarray(is_small_arr, dtype=bool)
    if is_long:
        sig = k3 < _K3M_LONG_PULLBACK
    else:
        sig = k3 > _K3M_SHORT_PULLBACK
    return hp & gn & sm & sig
