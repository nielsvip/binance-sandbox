"""
vec_paths/reduce_paths.py — Partial-close path implementations.

Sources:
  - K1M_EXTREME_REVERSE: ez_positions_quick.py:13541-13555 (scan_positions_for_exit_candidates)
  - STRONG_REDUCE_K: OPUS_VOMIT.py:4874-4890 (_strong_reduce_k_mask)
  - PROFIT_TAKE_REDUCE: ez_manage.py action='PROFIT_TAKE' — triggered from check_exit_candidates

All three fire a PARTIAL close (50% of position qty by default) rather than full close.
In the vec engine, a partial close appends a fractional pnl to returns and reduces pos.qty.

Public API:
    check_k1m_extreme_reverse(store, bar_idx, pos_state, mode, cfg) -> Optional[dict]
    check_strong_reduce_k(store, bar_idx, pos_state, mode, cfg) -> Optional[dict]
    check_profit_take_reduce(store, bar_idx, pos_state, mode, cfg) -> Optional[dict]

Each returns None (no action) or a dict:
    {
        "reason": str,          # log-ready reason string
        "frac": float,          # fraction of qty to close (0.5 = 50%)
        "require_profit": bool, # if True, caller must check gain >= 0 before applying
    }

Config defaults (mirror config.py defaults — both default OFF):
  K1M_EXTREME_REVERSE_ENABLED   = False
  K1M_EXTREME_HIGH              = 90.0
  K1M_EXTREME_LOW               = 10.0
  K1M_REVERSE_REQUIRES_PROFIT   = True
  K1M_REVERSE_REDUCE_FRAC       = 0.5

  STRONG_REDUCE_K_ENABLED       = False
  SRK_K15M_LONG_MIN             = 80.0
  SRK_K1H_LONG_MAX              = 30.0
  SRK_K15M_SHORT_MAX            = 20.0
  SRK_K1H_SHORT_MIN             = 70.0
  SRK_REDUCE_FRAC               = 0.5

  PROFIT_TAKE_REDUCE_ENABLED    = False
  PROFIT_TAKE_GAIN_PCT          = 2.0
  PROFIT_TAKE_REDUCE_FRAC       = 0.5
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _sf(x: Any, default: float = 0.0) -> float:
    """Safe float."""
    try:
        if x is None:
            return default
        v = float(x)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def _get_ltf(mode: str, cfg: Any) -> str:
    """Determine base timeframe for stoch lookup."""
    ltf = getattr(cfg, 'LTF', None)
    if ltf:
        return str(ltf)
    return '3m' if mode == 'crypto' else '5m'


# ════════════════════════════════════════════════════════════════════════════
# K1M_EXTREME_REVERSE
# Source: ez_positions_quick.py:13541-13555
# Live: fires when k_1m > 90 (LONG) / < 10 (SHORT) AND turning back AND gain >= 0.
# NPZ note: stoch_k_1m is NOT in most NPZ files (requires 1m feed).
# Fallback: use stoch_k_<LTF> (3m for crypto, 5m for tradier) as a proxy.
# When the proxy field equals exactly 50.0 for all bars, OPUS_VOMIT treats that
# as the "zero-filled / missing" sentinel and blocks firing (returns zeros mask).
# We replicate that guard here.
# ════════════════════════════════════════════════════════════════════════════

def check_k1m_extreme_reverse(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[Dict]:
    """K1M_EXTREME_REVERSE partial-close check.

    Args:
        store:      NPZStore (or compatible) for the symbol.
        bar_idx:    Current bar index in the store.
        pos_state:  _PositionState — needs .side and .gain_pct.
        mode:       'crypto' | 'tradier'.
        cfg:        VecConfig or config object.

    Returns dict or None.
    """
    if not bool(getattr(cfg, 'K1M_EXTREME_REVERSE_ENABLED', False)):
        return None

    is_long = (pos_state.side == 'LONG')
    gain = float(getattr(pos_state, 'gain_pct', 0.0))

    req_profit = bool(getattr(cfg, 'K1M_REVERSE_REQUIRES_PROFIT', True))
    if req_profit and gain < 0:
        return None

    hi = float(getattr(cfg, 'K1M_EXTREME_HIGH', 90.0))
    lo = float(getattr(cfg, 'K1M_EXTREME_LOW', 10.0))
    frac = float(getattr(cfg, 'K1M_REVERSE_REDUCE_FRAC', 0.5))

    # Try stoch_k_1m first; fall back to LTF proxy
    ltf = _get_ltf(mode, cfg)
    k_now = store.f('stoch_k_1m', bar_idx, -1.0)
    using_proxy = False
    if k_now < 0:
        k_now = store.f(f'stoch_k_{ltf}', bar_idx, 50.0)
        using_proxy = True

    # Guard: if proxy is all-50 (zero-filled NPZ field), don't fire
    if using_proxy and abs(k_now - 50.0) < 0.01:
        return None

    k_prev = store.f('stoch_k_1m', max(0, bar_idx - 1), -1.0)
    if k_prev < 0:
        k_prev = store.f(f'stoch_k_{ltf}', max(0, bar_idx - 1), k_now)

    if is_long and k_now > hi and k_now < k_prev:
        return {
            'reason': f'K1M_EXTREME_REVERSE_LONG_k1m={k_now:.0f}<prev={k_prev:.0f}_gain{gain:.2f}%',
            'frac': frac,
            'require_profit': req_profit,
        }
    if not is_long and k_now < lo and k_now > k_prev:
        return {
            'reason': f'K1M_EXTREME_REVERSE_SHORT_k1m={k_now:.0f}>prev={k_prev:.0f}_gain{gain:.2f}%',
            'frac': frac,
            'require_profit': req_profit,
        }
    return None


# ════════════════════════════════════════════════════════════════════════════
# STRONG_REDUCE_K
# Source: OPUS_VOMIT.py:4874-4890 (scalar equivalent; live fires from
#         ez_positions_quick.scan_positions_for_exit_candidates approximately).
# LONG reduces 50% when k_15m extreme high AND k_1h trending opposite.
# SHORT mirrors.
# Requires gain >= 0 (profit-only reduce).
# ════════════════════════════════════════════════════════════════════════════

def check_strong_reduce_k(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[Dict]:
    """STRONG_REDUCE_K partial-close check.

    Returns dict or None.
    """
    if not bool(getattr(cfg, 'STRONG_REDUCE_K_ENABLED', False)):
        return None

    is_long = (pos_state.side == 'LONG')
    gain = float(getattr(pos_state, 'gain_pct', 0.0))
    if gain < 0:
        return None

    frac = float(getattr(cfg, 'SRK_REDUCE_FRAC', 0.5))
    k15m = store.f('stoch_k_15m', bar_idx, 50.0)
    k1h = store.f('stoch_k_1h', bar_idx, 50.0)

    if is_long:
        k15_min = float(getattr(cfg, 'SRK_K15M_LONG_MIN', 80.0))
        k1h_max = float(getattr(cfg, 'SRK_K1H_LONG_MAX', 30.0))
        if k15m > k15_min and k1h < k1h_max:
            return {
                'reason': f'STRONG_REDUCE_K_LONG_k15m={k15m:.0f}>={k15_min:.0f}_k1h={k1h:.0f}<={k1h_max:.0f}_gain{gain:.2f}%',
                'frac': frac,
                'require_profit': True,
            }
    else:
        k15_max = float(getattr(cfg, 'SRK_K15M_SHORT_MAX', 20.0))
        k1h_min = float(getattr(cfg, 'SRK_K1H_SHORT_MIN', 70.0))
        if k15m < k15_max and k1h > k1h_min:
            return {
                'reason': f'STRONG_REDUCE_K_SHORT_k15m={k15m:.0f}<={k15_max:.0f}_k1h={k1h:.0f}>={k1h_min:.0f}_gain{gain:.2f}%',
                'frac': frac,
                'require_profit': True,
            }
    return None


# ════════════════════════════════════════════════════════════════════════════
# PROFIT_TAKE_REDUCE
# Source: ez_manage.py action='PROFIT_TAKE'. Fires a partial reduce when
#         gain >= PROFIT_TAKE_GAIN_PCT. In live it's called by the exit
#         candidate scanner; in vec we wire it as an exit path.
# Default OFF (mirrors config.py PROFIT_TAKE_REDUCE_ENABLED=False default).
# ════════════════════════════════════════════════════════════════════════════

def check_profit_take_reduce(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[Dict]:
    """PROFIT_TAKE partial-close check.

    Fires when gain >= PROFIT_TAKE_GAIN_PCT and the pnl hasn't yet been taken.
    Tracks whether a profit-take already fired via pos_state.ppl_fired (reusing
    the PPL state flag as a generic 'profit_take_done' signal when PPL is OFF).

    Returns dict or None.
    """
    if not bool(getattr(cfg, 'PROFIT_TAKE_REDUCE_ENABLED', False)):
        return None

    gain = float(getattr(pos_state, 'gain_pct', 0.0))
    pt_gain = float(getattr(cfg, 'PROFIT_TAKE_GAIN_PCT', 2.0))
    frac = float(getattr(cfg, 'PROFIT_TAKE_REDUCE_FRAC', 0.5))

    # Only fire once per position (reuse ppl_fired as the "already took profit" flag)
    if bool(getattr(pos_state, 'ppl_fired', False)):
        return None

    if gain >= pt_gain:
        return {
            'reason': f'PROFIT_TAKE_REDUCE_gain{gain:.2f}%_thr{pt_gain:.2f}%',
            'frac': frac,
            'require_profit': True,
        }
    return None
