"""check_exit_candidates_crypto__key_level_crash — KEY_LEVEL_CRASH / KEY_LEVEL_BREAKOUT
multi-TF Donchian-break exit predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
13862-13894 (KEY LEVELS CRASH OVERRIDE):

  LONG:
    count broken among {15m,1h,4h,D} where dc_low_<TF> > 0 and current_price < dc_low_<TF>
    severity = count
    if severity >= 3 and not _is_no_loss:  -> hard CLOSE "KEY_LEVEL_CRASH_S{sev}_DC_LOW_BROKEN_{tfs}"
    elif severity >= 2:                     -> HEDGE (side effect, not a close)
  SHORT (symmetric on dc_high_<TF>, current_price > dc_high_<TF>):
    if severity >= 3 and not _is_no_loss:  -> "KEY_LEVEL_BREAKOUT_S{sev}_DC_HIGH_BROKEN_{tfs}"
    elif severity >= 2:                     -> HEDGE

This module extracts ONLY the severity>=3 CLOSE decision (the pure per-bar
indicator predicate that emits a `hard_exit_reason`). The severity==2 branch is a
HEDGE side effect (asyncio.create_task) — classified as a hedge action, not a vec
close; it is NOT extracted here. The `not _is_no_loss` account gate is STATE (account
membership), applied by the caller.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_key_level_crash) AND vec (check_key_level_crash_vec) share the pure core
_key_level_crash_count() — CANNOT drift.

NPZ / indicator fields read (the four DC TFs):
  - dc_low_15m / dc_low_1h / dc_low_4h / dc_low_D    (LONG)
  - dc_high_15m / dc_high_1h / dc_high_4h / dc_high_D (SHORT)
  - current_price / close
All present in NPZ.

Config:
  KEY_LEVEL_CRASH_CLOSE_MIN_TFS  (default 3)  — live HARDCODED `>= 3` for the CLOSE branch.
"""
from typing import Tuple
import numpy as np

_TFS = ("15m", "1h", "4h", "D")


def _key_level_crash_count(current_price: float, dc_lows: tuple, dc_highs: tuple,
                           is_long: bool) -> int:
    """PURE per-bar count of DC levels broken. Mirrors live append logic
    (dc_low_<TF> > 0 and price < dc_low_<TF>) / (dc_high_<TF> > 0 and price > dc_high_<TF>)."""
    cnt = 0
    if is_long:
        for lvl in dc_lows:
            if lvl > 0 and current_price < lvl:
                cnt += 1
    else:
        for lvl in dc_highs:
            if lvl > 0 and current_price > lvl:
                cnt += 1
    return cnt


def _key_level_crash_min_tfs(config):
    return int(getattr(config, "KEY_LEVEL_CRASH_CLOSE_MIN_TFS", 3))


def check_key_level_crash(config, indicators: dict, current_price: float,
                          is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires_close, reason) for the severity>=MIN_TFS CLOSE.
    The `not _is_no_loss` gate + severity==2 HEDGE branch are handled by the caller."""
    dc_lows = tuple(float(indicators.get(f"dc_low_{tf}", 0) or 0) for tf in _TFS)
    dc_highs = tuple(float(indicators.get(f"dc_high_{tf}", 0) or 0) for tf in _TFS)
    cnt = _key_level_crash_count(current_price, dc_lows, dc_highs, is_long)
    min_tfs = _key_level_crash_min_tfs(config)
    if cnt < min_tfs:
        return False, ""
    if is_long:
        tfs = "+".join(t for t, lvl in zip(_TFS, dc_lows) if lvl > 0 and current_price < lvl)
        return True, f"KEY_LEVEL_CRASH_S{cnt}_DC_LOW_BROKEN_{tfs}"
    tfs = "+".join(t for t, lvl in zip(_TFS, dc_highs) if lvl > 0 and current_price > lvl)
    return True, f"KEY_LEVEL_BREAKOUT_S{cnt}_DC_HIGH_BROKEN_{tfs}"


def check_key_level_crash_vec(config, current_price_arr, dc_low_arrs, dc_high_arrs,
                              is_long) -> np.ndarray:
    """VECTORIZED per-bar CLOSE mask. SAME predicate as scalar.
    dc_low_arrs / dc_high_arrs are 4-tuples of per-bar arrays in TF order
    (15m,1h,4h,D), e.g. (npz['dc_low_15m'], npz['dc_low_1h'], ...)."""
    p = np.asarray(current_price_arr, dtype=float)
    n = len(p)
    min_tfs = _key_level_crash_min_tfs(config)
    cnt = np.zeros(n, dtype=int)
    if is_long:
        for arr in dc_low_arrs:
            a = np.asarray(arr, dtype=float)
            cnt += ((a > 0) & (p < a)).astype(int)
    else:
        for arr in dc_high_arrs:
            a = np.asarray(arr, dtype=float)
            cnt += ((a > 0) & (p > a)).astype(int)
    return cnt >= min_tfs
