"""SHARED scalar+vectorized predicate for the LIVE decision WT_EXHAUST_EXIT.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~41410-41430.

Closes when 4h WT momentum is exhausted against the position, confirmed by 1h or
15m. wt_momentum_state_* are STRING enums (decoded by IndicatorStore in
backtest) — uppercased before compare.

  LONG  exit iff mom_4h=='EXHAUST_UP'   AND (mom_1h=='EXHAUST_UP'   OR mom_15m=='EXHAUST_UP')
  SHORT exit iff mom_4h=='EXHAUST_DOWN' AND (mom_1h=='EXHAUST_DOWN' OR mom_15m=='EXHAUST_DOWN')
  then gain gate (caller-state): (not REQUIRE_GAIN or gain>0) AND gain>=MIN_GAIN_PCT

CLASSIFICATION: stateful_seam. The momentum-state predicate is pure per-bar over
NPZ string fields; the final gain gate uses the per-position seam value `gain`.
This core handles the momentum predicate; the caller applies the gain gate
(exposed here too for completeness).
"""
from typing import Tuple
import numpy as np

_UP = "EXHAUST_UP"
_DOWN = "EXHAUST_DOWN"


def _wt_exhaust_momentum_fires(mom_4h: str, mom_1h: str, mom_15m: str, is_long: bool) -> bool:
    """Pure momentum-state predicate (no gain gate)."""
    m4 = (mom_4h or "").upper()
    m1 = (mom_1h or "").upper()
    m15 = (mom_15m or "").upper()
    if is_long:
        return m4 == _UP and (m1 == _UP or m15 == _UP)
    return m4 == _DOWN and (m1 == _DOWN or m15 == _DOWN)


def _wt_exhaust_fires(mom_4h: str, mom_1h: str, mom_15m: str, is_long: bool,
                      gain: float, require_gain: bool, min_gain: float) -> bool:
    """Full live predicate incl. the gain gate (gain from per-position state)."""
    if not _wt_exhaust_momentum_fires(mom_4h, mom_1h, mom_15m, is_long):
        return False
    return (not require_gain or gain > 0) and gain >= min_gain


def _wt_exhaust_params(config):
    return (bool(getattr(config, "WT_EXHAUST_EXIT_REQUIRE_GAIN", False)),
            float(getattr(config, "WT_EXHAUST_EXIT_MIN_GAIN_PCT", 0.0)))


def check_wt_exhaust_exit(config, indicators: dict, is_long: bool, gain: float) -> Tuple[bool, str]:
    """LIVE/scalar path. gain from per-position state."""
    req, mg = _wt_exhaust_params(config)
    m4 = str((indicators or {}).get("wt_momentum_state_4h", "") or "")
    m1 = str((indicators or {}).get("wt_momentum_state_1h", "") or "")
    m15 = str((indicators or {}).get("wt_momentum_state_15m", "") or "")
    if not _wt_exhaust_fires(m4, m1, m15, is_long, gain, req, mg):
        return False, ""
    return True, f"WT_EXHAUST_mom4h={m4.upper()}_1h={m1.upper()}_g={gain:.2f}%"


def check_wt_exhaust_exit_vec(config, mom_4h_arr, mom_1h_arr, mom_15m_arr, gain_arr, is_long):
    """VECTORIZED per-bar fire mask. Arrays of strings (object dtype) + gain.
    SAME logic as _wt_exhaust_fires (vectorized via uppercased equality)."""
    req, mg = _wt_exhaust_params(config)
    m4 = np.array([(str(x) or "").upper() for x in mom_4h_arr], dtype=object)
    m1 = np.array([(str(x) or "").upper() for x in mom_1h_arr], dtype=object)
    m15 = np.array([(str(x) or "").upper() for x in mom_15m_arr], dtype=object)
    g = np.asarray(gain_arr, dtype=float)
    tag = _UP if is_long else _DOWN
    mom = (m4 == tag) & ((m1 == tag) | (m15 == tag))
    if req:
        gain_ok = (g > 0) & (g >= mg)
    else:
        gain_ok = g >= mg
    return mom & gain_ok
