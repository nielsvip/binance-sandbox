"""WIRING LANE C — vec twins of _batch1 live entry-gate predicates (lane C batch L1a).

LIVE SOURCES (read-only reference, NOT edited here):
  crypto : ez_manage.py::_batch1_template_live_gate (called+honored from
           check_entry_vetting:2750-2752) — ATR_LONG_WINDOW (~6622), ATR_TRAIL_FILTER_TF
           (~6628, membership in _B1_REAL_TFS=('1h','4h','D','W')), ATR_TRAIL_SWEEP_ENABLED (~6635)
  stocks : tradier_manage.py::_batch1_template_live_gate_tradier:5631-5649
           (no in-repo call site — dead on stocks side; documented approximation below)

TWINNED PREDICATES (allow-mask True = entry allowed):
  ATR_LONG_WINDOW      : default 100 -> inert. Else veto bars with atr_1h<=0.5.
                        (tradier predicate; the crypto close<=thr/0.0-default form
                        contradicts config default 100 and would veto every live entry
                        including missing-close -> not live-effective, NOT twinned.)
  ATR_TRAIL_FILTER_TF  : default '15m' -> inert. Else (tf in 1h/4h/D/W) require WT
                        alignment on that TF (LONG wt1>wt2 / SHORT wt1<wt2).
                        (crypto membership form; tradier's `!=15m` form differs only for
                        exotic TF strings.)
  ATR_TRAIL_SWEEP_ENABLED: default False -> inert. True -> veto bars with atr_1h<=0.5.
                        (identical in both live files.)
  CIRCUIT_SHARPE_GATES_FILTER_TF (MOP-UP M 2026-10-04): default '15m' -> inert.
                        Else (tf in 1h/4h/D/W) require WT alignment on that TF
                        (LONG wt1>wt2 / SHORT wt1<wt2). Live: ez batch1 veto
                        :6894-6900 (crypto only; tradier batch1 dead per L1a note).

BIBLE: §43 parity gate (same-NPZ twin-vs-live, trade ratio 0.80-1.25, gain <0.5pp/<15%);
§17 curated config parity (vec default == live default); §18 honest-0 (default == inert).
Each mask returns all-True at the config default -> zero behavior change until promotion.
"""
from __future__ import annotations

import numpy as np

_B1_REAL_TFS = ('1h', '4h', 'D', 'W')
_ATR_VETO_LEVEL = 0.5


def _safe(npz, key, n, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _thr_cfg(cfg, name, default):
    """Config read that treats False/None (AUTO_WIRED filler) as unset -> default."""
    v = getattr(cfg, name, default)
    if v is False or v is None:
        return default
    return v


# ---------------- ATR_LONG_WINDOW ----------------
def atr_long_window_allow(npz, n, is_long, cfg):
    """Vector twin. Default 100 -> all allow."""
    try:
        thr = float(_thr_cfg(cfg, 'ATR_LONG_WINDOW', 100))
    except Exception:
        return np.ones(n, dtype=bool)
    if abs(thr - 100) <= 1e-9:
        return np.ones(n, dtype=bool)
    atr = _safe(npz, 'atr_1h', n, 1.0)
    return ~(atr <= _ATR_VETO_LEVEL)


def _sone(npz, key, n, i, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        try:
            return float(v[i])
        except Exception:
            return default
    return default


def atr_long_window_allow_scalar(npz, n, i, is_long, cfg):
    """Scalar reference: same predicate for bar i. Returns bool."""
    try:
        thr = float(_thr_cfg(cfg, 'ATR_LONG_WINDOW', 100))
    except Exception:
        return True
    if abs(thr - 100) <= 1e-9:
        return True
    return not (_sone(npz, 'atr_1h', n, i, 1.0) <= _ATR_VETO_LEVEL)


# ---------------- ATR_TRAIL_FILTER_TF ----------------
def atr_trail_filter_tf_allow(npz, n, is_long, cfg):
    """Vector twin. Default '15m' -> all allow."""
    tf = str(_thr_cfg(cfg, 'ATR_TRAIL_FILTER_TF', '15m') or '15m')
    if tf not in _B1_REAL_TFS:
        return np.ones(n, dtype=bool)
    w1 = _safe(npz, f'wt1_{tf}', n, 0.0)
    w2 = _safe(npz, f'wt2_{tf}', n, 0.0)
    # live falls back to 15m arrays when the TF key is absent from indicators
    if f'wt1_{tf}' not in npz:
        w1 = _safe(npz, 'wt1_15m', n, 0.0)
    if f'wt2_{tf}' not in npz:
        w2 = _safe(npz, 'wt2_15m', n, 0.0)
    return (w1 > w2) if is_long else (w1 < w2)


def atr_trail_filter_tf_allow_scalar(npz, n, i, is_long, cfg):
    tf = str(_thr_cfg(cfg, 'ATR_TRAIL_FILTER_TF', '15m') or '15m')
    if tf not in _B1_REAL_TFS:
        return True
    has1, has2 = f'wt1_{tf}' in npz, f'wt2_{tf}' in npz
    w1 = _sone(npz, f'wt1_{tf}' if has1 else 'wt1_15m', n, i, 0.0)
    w2 = _sone(npz, f'wt2_{tf}' if has2 else 'wt2_15m', n, i, 0.0)
    return (w1 > w2) if is_long else (w1 < w2)


# ---------------- ATR_TRAIL_SWEEP_ENABLED ----------------
def atr_trail_sweep_allow(npz, n, is_long, cfg):
    """Vector twin. Default False -> all allow."""
    if not bool(_thr_cfg(cfg, 'ATR_TRAIL_SWEEP_ENABLED', False)):
        return np.ones(n, dtype=bool)
    atr = _safe(npz, 'atr_1h', n, 1.0)
    return ~(atr <= _ATR_VETO_LEVEL)


def atr_trail_sweep_allow_scalar(npz, n, i, is_long, cfg):
    if not bool(_thr_cfg(cfg, 'ATR_TRAIL_SWEEP_ENABLED', False)):
        return True
    return not (_sone(npz, 'atr_1h', n, i, 1.0) <= _ATR_VETO_LEVEL)


# ---------------- CIRCUIT_SHARPE_GATES_FILTER_TF (MOP-UP M 2026-10-04) ----------------
def circuit_sharpe_gates_filter_tf_allow(npz, n, is_long, cfg):
    """Vector twin of the ez batch1 veto (:6894-6900). Default '15m' -> all allow."""
    tf = str(_thr_cfg(cfg, 'CIRCUIT_SHARPE_GATES_FILTER_TF', '15m') or '15m')
    if tf not in _B1_REAL_TFS:
        return np.ones(n, dtype=bool)
    w1 = _safe(npz, f'wt1_{tf}', n, 0.0)
    w2 = _safe(npz, f'wt2_{tf}', n, 0.0)
    # live falls back to 15m arrays when the TF key is absent from indicators
    if f'wt1_{tf}' not in npz:
        w1 = _safe(npz, 'wt1_15m', n, 0.0)
    if f'wt2_{tf}' not in npz:
        w2 = _safe(npz, 'wt2_15m', n, 0.0)
    return (w1 > w2) if is_long else (w1 < w2)


def circuit_sharpe_gates_filter_tf_allow_scalar(npz, n, i, is_long, cfg):
    tf = str(_thr_cfg(cfg, 'CIRCUIT_SHARPE_GATES_FILTER_TF', '15m') or '15m')
    if tf not in _B1_REAL_TFS:
        return True
    has1, has2 = f'wt1_{tf}' in npz, f'wt2_{tf}' in npz
    w1 = _sone(npz, f'wt1_{tf}' if has1 else 'wt1_15m', n, i, 0.0)
    w2 = _sone(npz, f'wt2_{tf}' if has2 else 'wt2_15m', n, i, 0.0)
    return (w1 > w2) if is_long else (w1 < w2)
