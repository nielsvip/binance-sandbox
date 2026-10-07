"""WIRING LANE C — vec twins of WT entry boycott gates R-G5/R-G7 (batch M2a, 2 switches).

LIVE SOURCES (read-only reference, ez_positions_quick.py::_btc_dedicated_exit_decision):
  R-G7 WT_DIV_ENTRY_GATE_ENABLED (live True, :2317-2322): LONG boycotted when
    wt_any_bear_div; SHORT when wt_any_bull_div. (-100 BOYCOTT.)
  R-G5 WT_EXHAUST_ENTRY_GATE_ENABLED (live True, :2301-2308): LONG boycotted when
    wt_momentum_state_3m AND _15m are EXHAUST_UP; SHORT when both EXHAUST_DOWN.
TWIN: extra_ok entry vetoes. wt_any_*_div recomputed per the precompute recipe
(vec_decisions.htf_causal_align._recompute_composites): OR over TFs (15m/1h/4h/D/W/M)
of (wt_divergence_TF<0 bear / >0 bull); missing keys contribute False.
Momentum enum per vec_decisions.grey_wire_exits: 2=EXHAUST_UP, -2=EXHAUST_DOWN.
APPROXIMATIONS: (1) div OR lacks the 3m member (no 3m data) -> twin boycotts LESS
than live when only 3m diverges. (2) Exhaust uses the 15m leg only (3m missing) ->
twin boycotts MORE than live (live needs both legs). Directions documented.
DIVERGENCES: both live True, vec False until promotion (entry vetoes move baselines).
BIBLE: §43 parity gate; §17 (divergences noted); §18 honest-0.
"""
from __future__ import annotations

import numpy as np

_TFS = ('15m', '1h', '4h', 'D', 'W', 'M')


def _safe(npz, key, n, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        try:
            return v.astype(np.float64)
        except Exception:
            return np.full(n, default, dtype=np.float64)
    return np.full(n, default, dtype=np.float64)


def _present(npz, key, n):
    v = npz.get(key) if hasattr(npz, 'get') else None
    return v is not None and isinstance(v, np.ndarray) and len(v) == n


def wt_div_entry_allow(npz, n, is_long, cfg):
    """Default False -> all allow. True -> veto LONG on any-bear-div, SHORT on any-bull-div."""
    if not bool(getattr(cfg, 'WT_DIV_ENTRY_GATE_ENABLED', False)):
        return np.ones(n, dtype=bool)
    any_bear = np.zeros(n, dtype=bool)
    any_bull = np.zeros(n, dtype=bool)
    for tf in _TFS:
        if not _present(npz, f'wt_divergence_{tf}', n):
            continue
        dv = _safe(npz, f'wt_divergence_{tf}', n, 0.0)
        any_bear = any_bear | (dv < 0)
        any_bull = any_bull | (dv > 0)
    return (~any_bear) if is_long else (~any_bull)


def wt_div_entry_allow_scalar(npz, n, i, is_long, cfg):
    if not bool(getattr(cfg, 'WT_DIV_ENTRY_GATE_ENABLED', False)):
        return True
    for tf in _TFS:
        if not _present(npz, f'wt_divergence_{tf}', n):
            continue
        try:
            dv = float(npz[f'wt_divergence_{tf}'][i])
        except Exception:
            continue
        if is_long and dv < 0:
            return False
        if (not is_long) and dv > 0:
            return False
    return True


def wt_exhaust_entry_allow(npz, n, is_long, cfg):
    """Default False -> all allow. True -> veto when 15m momentum is exhausted in-side."""
    if not bool(getattr(cfg, 'WT_EXHAUST_ENTRY_GATE_ENABLED', False)):
        return np.ones(n, dtype=bool)
    mom = _safe(npz, 'wt_momentum_state_15m', n, 0.0)
    want = 2.0 if is_long else -2.0
    return ~(mom == want)


def wt_exhaust_entry_allow_scalar(npz, n, i, is_long, cfg):
    if not bool(getattr(cfg, 'WT_EXHAUST_ENTRY_GATE_ENABLED', False)):
        return True
    try:
        v = npz.get('wt_momentum_state_15m') if hasattr(npz, 'get') else None
        if v is None or not isinstance(v, np.ndarray) or len(v) != n:
            return True
        mom = float(v[i])
    except Exception:
        return True
    want = 2.0 if is_long else -2.0
    return not (mom == want)
