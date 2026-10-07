"""SHARED scalar+vectorized predicate for the LIVE decision STOP_FUNCTIONS_KILL.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~52235-52290
(decision) + ~52380-52440 (kill1/kill2 REDUCE sizing).

`should_stop_kill` — the decision that a large losing position should be
size-reduced ("killed" toward 2x START_POSITION_SIZE / pos_min floor). Computed
AFTER an early size guard:

  SIZE GUARD (early return, no kill): |positionAmt| <= 0.5 * START_POSITION_SIZE / price
                                      -> should_stop_kill stays False

  should_stop_kill = True iff EITHER:
    (A) LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED
        AND current_gain < -5.0
        AND positionAmt > 5 * START_POSITION_SIZE / price
    (B) LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED   (2026-09-28: routed through the knob;
        the pre-fix code fired B ungated — 57 leaked STOP_FUNCTIONS_KILL_2/24h)
        AND (A) did NOT fire (live elif)
        AND time_since_entry >= 5.0 (minutes)
        AND ( LONG : k_3m < d_3m ; SHORT : k_3m > d_3m )

exit-crypto FIX vs the previously staged (uncalled) copy: branch B was missing
the kill_enabled gate (would have fired with the switch OFF). Fixed here.

USER ORDER 2026-10-01 (LG-13): NO 3m/1m/5m data in the vector system. Branch B
needs k_3m/d_3m -> INERT in vec ('needs 3m, ignored'). The vec hook therefore
wires branch A only; callers pass indicators without k_3m/d_3m (None -> "not
against", matching the live is-not-None guard).

Downstream sizing (live): HEDGE_MODE accounts block (hedge instead); otherwise
kill2-shaped REDUCE (gain<0.4, age>18min, MANAGE_REDUCE): reduce_qty =
min(amt - pos_min_qty, amt*0.99). The hook applies exactly that shape.

CLASSIFICATION: stateful_seam. Branch A is pure per-bar over the seam values
current_gain / positionAmt / price; age gate (18min, kill2) applied by caller.
"""
from typing import Tuple
import numpy as np


def _stop_functions_kill_thresholds(config):
    return (bool(getattr(config, "LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED", False)),
            float(getattr(config, "START_POSITION_SIZE", 9.0)))


def _stop_functions_kill_fires(gain: float, age_min: float, position_amt: float,
                               price: float, k_3m, d_3m, is_long: bool,
                               kill_enabled: bool, start_size: float) -> bool:
    """Pure per-bar/seam should_stop_kill test. A short-circuits B (live elif);
    B requires kill_enabled (2026-09-28 fix) and real k_3m/d_3m (None -> False)."""
    if not kill_enabled:
        return False
    if price <= 0:
        return False
    if abs(position_amt) <= 0.5 * start_size / price:
        return False
    cond_a = (gain < -5.0
              and position_amt > 5.0 * start_size / price)
    if cond_a:
        return True
    if age_min >= 5.0:
        if k_3m is None or d_3m is None:
            return False
        if is_long:
            return k_3m < d_3m
        return k_3m > d_3m
    return False


def check_stop_functions_kill(config, indicators: dict, is_long: bool, price: float,
                              gain: float, age_min: float, position_amt: float) -> Tuple[bool, str]:
    """LIVE/scalar path. gain/age_min/position_amt from per-position state seam."""
    kill_enabled, start_size = _stop_functions_kill_thresholds(config)
    g = indicators or {}
    k_3m = g.get("stoch_k_3m", None)
    d_3m = g.get("stoch_d_3m", None)
    k_3m = float(k_3m) if k_3m is not None else None
    d_3m = float(d_3m) if d_3m is not None else None
    if not _stop_functions_kill_fires(gain, age_min, position_amt, price, k_3m, d_3m,
                                      is_long, kill_enabled, start_size):
        return False, ""
    return True, f"STOP_FUNCTIONS_KILL_g{gain:.2f}%_age{age_min:.0f}m"


def check_stop_functions_kill_vec(config, gain_arr, age_min_arr, position_amt_arr,
                                  price_arr, k_3m_arr, d_3m_arr, is_long):
    """VECTORIZED per-bar/seam should_stop_kill mask. SAME logic (B gated on
    kill_enabled; NaN in k/d -> treated as None, matching live)."""
    kill_enabled, start_size = _stop_functions_kill_thresholds(config)
    g = np.asarray(gain_arr, dtype=float)
    if not kill_enabled:
        return np.zeros(len(g), dtype=bool)
    age = np.asarray(age_min_arr, dtype=float)
    amt = np.asarray(position_amt_arr, dtype=float)
    p = np.asarray(price_arr, dtype=float)
    k3 = np.asarray(k_3m_arr, dtype=float)
    d3 = np.asarray(d_3m_arr, dtype=float)
    valid_price = p > 0
    size_ok = np.abs(amt) > 0.5 * start_size / np.where(valid_price, p, 1.0)
    cond_a = ((g < -5.0)
              & (amt > 5.0 * start_size / np.where(valid_price, p, 1.0)))
    kd_present = (~np.isnan(k3)) & (~np.isnan(d3))
    if is_long:
        b_against = kd_present & (k3 < d3)
    else:
        b_against = kd_present & (k3 > d3)
    cond_b = (~cond_a) & (age >= 5.0) & b_against
    return valid_price & size_ok & (cond_a | cond_b)
