"""check_exit_candidates_crypto__emergency_dc1h_breach — EMERGENCY_DC1H_BREACH exit
predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
13933-13938 (EMERGENCY: 1h DC structural breach):

    _dc1h_breach = (is_long and dc_low_1h > 0 and current_price < dc_low_1h * 0.997) \
                or (not is_long and dc_high_1h > 0 and current_price > dc_high_1h * 1.003)
    if not hard_exit_reason and _dc1h_breach and not _is_no_loss:
        hard_exit_reason = "EMERGENCY_DC1H_BREACH_technical"

Pure per-bar technical exit: price breaks the 1h Donchian low (LONG) / high (SHORT)
by a 0.3% buffer. NOT a %-stop — a structural break. It is in the
LOSS_EXIT_TECHNICAL_BYPASS list (closes even at a loss). The `not _is_no_loss` account
gate is STATE (account membership), applied by the caller.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_emergency_dc1h_breach) AND vec (check_emergency_dc1h_breach_vec) share the pure
core _emergency_dc1h_breach_fires() — CANNOT drift.

NPZ / indicator fields read:
  - dc_low_1h / dc_high_1h  — present in NPZ
  - current_price / close

Config:
  EMERGENCY_DC1H_BREACH_ENABLED   (default True — live has no flag; always active when
                                   account not no-loss, so we default the knob True to
                                   preserve live behavior when absent)
  EMERGENCY_DC1H_LOW_MULT         (default 0.997)  — live HARDCODED 0.997
  EMERGENCY_DC1H_HIGH_MULT        (default 1.003)  — live HARDCODED 1.003
"""
from typing import Tuple
import numpy as np


def _emergency_dc1h_breach_fires(current_price: float, dc_low_1h: float, dc_high_1h: float,
                                 is_long: bool, low_mult: float, high_mult: float) -> bool:
    """PURE per-bar fire test. Mirrors ez_positions_quick.py:13933 EXACTLY."""
    if is_long:
        return dc_low_1h > 0 and current_price < dc_low_1h * low_mult
    return dc_high_1h > 0 and current_price > dc_high_1h * high_mult


def _emergency_dc1h_thresholds(config):
    return (float(getattr(config, "EMERGENCY_DC1H_LOW_MULT", 0.997)),
            float(getattr(config, "EMERGENCY_DC1H_HIGH_MULT", 1.003)))


def check_emergency_dc1h_breach(config, indicators: dict, current_price: float,
                                is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). Fire from shared core. The
    `not _is_no_loss` account gate is applied by the caller (it is state, not data)."""
    if not getattr(config, "EMERGENCY_DC1H_BREACH_ENABLED", True):
        return False, ""
    low_mult, high_mult = _emergency_dc1h_thresholds(config)
    dc_low_1h = float(indicators.get("dc_low_1h", 0) or 0)
    dc_high_1h = float(indicators.get("dc_high_1h", 0) or 0)
    if not _emergency_dc1h_breach_fires(current_price, dc_low_1h, dc_high_1h, is_long,
                                        low_mult, high_mult):
        return False, ""
    return True, "EMERGENCY_DC1H_BREACH_technical"


def check_emergency_dc1h_breach_vec(config, current_price_arr, dc_low_1h_arr,
                                    dc_high_1h_arr, is_long) -> np.ndarray:
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as scalar.
    Arrays (NPZ): close, dc_low_1h, dc_high_1h."""
    p = np.asarray(current_price_arr, dtype=float)
    n = len(p)
    if not getattr(config, "EMERGENCY_DC1H_BREACH_ENABLED", True):
        return np.zeros(n, dtype=bool)
    low_mult, high_mult = _emergency_dc1h_thresholds(config)
    if is_long:
        lo = np.asarray(dc_low_1h_arr, dtype=float)
        return (lo > 0) & (p < lo * low_mult)
    hi = np.asarray(dc_high_1h_arr, dtype=float)
    return (hi > 0) & (p > hi * high_mult)
