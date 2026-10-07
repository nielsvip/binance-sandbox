"""check_exit_candidates_crypto__k1m_extreme_reverse — K1M_EXTREME_REVERSE exit
predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
13715-13723 (the K1M EXTREME REVERSE EXIT block, USER RULE 2026-04-27):

    if not hard_exit_reason and not is_hedge and not _in_grace_period and current_gain >= 0
       and config.K1M_EXTREME_REVERSE_ENABLED and not _trend_veto_active:
        _k1m_now  = indicators['stoch_k_1m']   (default 50)
        _k1m_prev = indicators['k_1m_prev']    (default _k1m_now)
        if is_long  and _k1m_now > 90 and _k1m_now < _k1m_prev:  -> K1M_EXTREME_REVERSE_LONG
        elif not is_long and _k1m_now < 10 and _k1m_now > _k1m_prev: -> K1M_EXTREME_REVERSE_SHORT

This is a PROFIT-LOCK exit: only fires when current_gain >= 0 ("never close at loss").
k_1m at overbought/oversold extreme AND turning back = trade is out.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): the live
scalar (check_k1m_extreme_reverse) AND the vectorized backtest
(check_k1m_extreme_reverse_vec) BOTH derive the indicator-only fire decision from the
same pure per-bar predicate _k1m_extreme_reverse_fires(), so the two paths CANNOT drift.

STATE SEAM (faithful to live): the indicator-only core covers k_1m extreme + turn.
The gain>=0 gate, the _in_grace_period gate, the is_hedge gate, and the
_trend_veto_active gate are STATE/runtime-dependent (gain, position age, hedge map,
multi-TF WT alignment), so the caller layers them on top — exactly as the live block
does. The vec wrapper accepts gain_arr + the veto/grace/hedge masks and ANDs them.

NPZ / indicator fields read by the core:
  - stoch_k_1m  (1m stochastic %K)    — present in NPZ
  - k_1m_prev   (prior-bar %K)         — present in NPZ (precompute writes *_prev)

Config thresholds (live HARDCODED 90 / 10 — exposed as knobs defaulting to the live
values so parity holds when the knob is absent):
  K1M_EXTREME_REVERSE_ENABLED   (default False — live default per getattr)
  K1M_EXTREME_HIGH              (default 90.0)
  K1M_EXTREME_LOW               (default 10.0)
"""
from typing import Tuple
import numpy as np


def _k1m_extreme_reverse_fires(k1m_now: float, k1m_prev: float, is_long: bool,
                               k_high: float, k_low: float) -> bool:
    """PURE per-bar indicator-only fire test (no state, no config, no I/O).
    Mirrors ez_positions_quick.py:13718 / 13721 EXACTLY (strict >/< comparisons)."""
    if is_long:
        return k1m_now > k_high and k1m_now < k1m_prev
    return k1m_now < k_low and k1m_now > k1m_prev


def _k1m_extreme_reverse_thresholds(config):
    return (float(getattr(config, "K1M_EXTREME_HIGH", 90.0)),
            float(getattr(config, "K1M_EXTREME_LOW", 10.0)))


def check_k1m_extreme_reverse(config, indicators: dict, is_long: bool,
                              current_gain: float = None) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). Fire decision from the shared core.
    The gain>=0 profit-lock gate is applied here IF current_gain is supplied (faithful
    to live line 13715); the grace/hedge/trend-veto gates remain the caller's job (the
    live block ANDs them in the `if` before this code runs)."""
    if not getattr(config, "K1M_EXTREME_REVERSE_ENABLED", False):
        return False, ""
    if current_gain is not None and current_gain < 0:
        return False, ""
    k_high, k_low = _k1m_extreme_reverse_thresholds(config)
    k1m_now = float(indicators.get("stoch_k_1m", 50) or 50)
    k1m_prev = float(indicators.get("k_1m_prev", k1m_now) or k1m_now)
    if not _k1m_extreme_reverse_fires(k1m_now, k1m_prev, is_long, k_high, k_low):
        return False, ""
    if is_long:
        return True, f"K1M_EXTREME_REVERSE_LONG_k1m={k1m_now:.0f}<prev={k1m_prev:.0f}"
    return True, f"K1M_EXTREME_REVERSE_SHORT_k1m={k1m_now:.0f}>prev={k1m_prev:.0f}"


def check_k1m_extreme_reverse_vec(config, k1m_now_arr, k1m_prev_arr, is_long,
                                  gain_arr=None) -> np.ndarray:
    """VECTORIZED per-bar fire mask — backtest path. SAME thresholds + predicate as the
    live scalar (vectorized via numpy). Arrays are per-bar (NPZ):
        k1m_now_arr  = npz['stoch_k_1m']
        k1m_prev_arr = npz['k_1m_prev']
        gain_arr     = per-bar gain% (state seam; if given, applies gain>=0 profit-lock)."""
    kn = np.asarray(k1m_now_arr, dtype=float)
    n = len(kn)
    if not getattr(config, "K1M_EXTREME_REVERSE_ENABLED", False):
        return np.zeros(n, dtype=bool)
    k_high, k_low = _k1m_extreme_reverse_thresholds(config)
    kp = np.asarray(k1m_prev_arr, dtype=float)
    if is_long:
        m = (kn > k_high) & (kn < kp)
    else:
        m = (kn < k_low) & (kn > kp)
    if gain_arr is not None:
        m &= np.asarray(gain_arr, dtype=float) >= 0
    return m
