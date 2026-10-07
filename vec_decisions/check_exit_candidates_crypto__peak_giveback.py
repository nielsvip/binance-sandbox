"""check_exit_candidates_crypto__peak_giveback — PEAK_GIVEBACK_PROTECTION exit
predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
14238-14253:

  GATE (caller state): not hard_exit_reason, not is_hedge, not _in_grace_period,
       PEAK_GIVEBACK_PROTECTION_ENABLED (default True)
  FIRE:
    if max_gain >= PEAK_GIVEBACK_MIN_PEAK_PCT (default 0.5):
        hard_zero = PEAK_GIVEBACK_HARD_ZERO_ENABLED (default True)
        drop_enabled = PEAK_GIVEBACK_DROP_TRIGGER_ENABLED (default False)
        if hard_zero and current_gain < 0.08:  -> PEAK_GIVEBACK_GAIN_EROSION_STOP (hard-zero)
        elif drop_enabled and current_gain < max_gain - PEAK_GIVEBACK_DROP_PCT (default 1.0):
                                                -> PEAK_GIVEBACK_GAIN_EROSION_STOP (drop)

This is a pure STATE predicate on (max_gain, current_gain) — no indicator fields. It
is a classic stateful_seam: the engine already maintains per-position max_gain and
gain. Faithfully extractable as a pure % core.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar +
vec share the pure core _peak_giveback_fires() — CANNOT drift.

State seam fields (engine-maintained):
  - max_gain (peak gain%), current_gain (gain%)

Config:
  PEAK_GIVEBACK_PROTECTION_ENABLED  (default True)
  PEAK_GIVEBACK_MIN_PEAK_PCT        (default 0.5)
  PEAK_GIVEBACK_HARD_ZERO_ENABLED   (default True)
  PEAK_GIVEBACK_DROP_TRIGGER_ENABLED(default False)
  PEAK_GIVEBACK_DROP_PCT            (default 1.0)
  PEAK_GIVEBACK_HARD_ZERO_GAIN      (default 0.08)  — live HARDCODED `< 0.08`
"""
from typing import Tuple
import numpy as np


def _peak_giveback_fires(max_gain: float, current_gain: float, min_peak: float,
                         hard_zero_enabled: bool, hard_zero_gain: float,
                         drop_enabled: bool, drop_pct: float) -> bool:
    """PURE per-bar fire test on state only. Mirrors live 14241-14252 EXACTLY."""
    if max_gain < min_peak:
        return False
    if hard_zero_enabled and current_gain < hard_zero_gain:
        return True
    if drop_enabled and current_gain < (max_gain - drop_pct):
        return True
    return False


def _peak_giveback_thresholds(config):
    return (float(getattr(config, "PEAK_GIVEBACK_MIN_PEAK_PCT", 0.5)),
            bool(getattr(config, "PEAK_GIVEBACK_HARD_ZERO_ENABLED", True)),
            float(getattr(config, "PEAK_GIVEBACK_HARD_ZERO_GAIN", 0.08)),
            bool(getattr(config, "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED", False)),
            float(getattr(config, "PEAK_GIVEBACK_DROP_PCT", 1.0)))


def check_peak_giveback(config, max_gain: float, current_gain: float) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason)."""
    if not bool(getattr(config, "PEAK_GIVEBACK_PROTECTION_ENABLED", True)):
        return False, ""
    min_peak, hz_en, hz_gain, drop_en, drop_pct = _peak_giveback_thresholds(config)
    if not _peak_giveback_fires(max_gain, current_gain, min_peak, hz_en, hz_gain, drop_en, drop_pct):
        return False, ""
    return True, f"PEAK_GIVEBACK_GAIN_EROSION_STOP_peak{max_gain:.2f}%_cur{current_gain:.2f}%"


def check_peak_giveback_vec(config, max_gain_arr, current_gain_arr) -> np.ndarray:
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as scalar.
    Arrays are per-bar state: peak gain%, current gain%."""
    mg = np.asarray(max_gain_arr, dtype=float)
    n = len(mg)
    if not bool(getattr(config, "PEAK_GIVEBACK_PROTECTION_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    min_peak, hz_en, hz_gain, drop_en, drop_pct = _peak_giveback_thresholds(config)
    g = np.asarray(current_gain_arr, dtype=float)
    m = mg >= min_peak
    fire = np.zeros(n, dtype=bool)
    if hz_en:
        fire |= (g < hz_gain)
    if drop_en:
        fire |= (g < (mg - drop_pct))
    return m & fire
