"""WIRING LANE C — vec twins for ENTRY TF selection + WT_DC direct entry (batch L1d).

LIVE SOURCES (read-only reference):
  ENTRY_PRIMARY_TF (live '4h' both): tradier_manage.py::calculate_signal_score:17299-17303 —
    mfi_ENTRY_PRIMARY_TF vs RSI_ENTRY_LONG_TRADIER(42)/SHORT(58).
    TWIN (documented): vec mfi_gate keeps its own thresholds (MFI_ENTRY_LONG_MAX 60.0 /
    SHORT_MIN 40.0 — live's RSI_ENTRY_* are a separate gap) and gains the TF selector:
    default '4h' (or OFF/'') -> today's vec mfi_1h column (no change); other TFs ->
    mfi_TF column (missing key -> 50.0, live g() default, which blocks both sides).
    BASELINE DIVERGENCE: live gates on mfi_4h, vec baseline keeps mfi_1h — the knob is
    sweepable (non-default TFs bind); aligning the baseline 1h->4h is a promotion call.
  WT_DC_DIRECT_THRESHOLD (live 20.0 both configs; _cfg fallback -1.0 = disabled):
    tradier_manage.py::_shared_direct_entry_claim:1112-1117 via
    wt_dc_contract.evaluate_wt_dc_direct (inputs wt_d/wt_4h/dc_position_1h/stoch 5m/cross 1h).
    TWIN: new entry block B_WT_DC_DIRECT = (score_entry_multitf_vec >= thr), OR'd into raw
    with sibling blocks. DIVERGENCE: vec default -1.0 (disabled) vs live 20.0 — twin
    inert until promotion; sweep values 10/20/30 per the pre-existing QuickConfig comment.
BIBLE: §43 parity gate; §17 (divergences noted); §18 honest-0.
"""
from __future__ import annotations

import numpy as np


def _safe(npz, key, n, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _sone(npz, key, n, i, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        try:
            return float(v[i])
        except Exception:
            return default
    return default


_BASELINE_TFS = ('4h', 'OFF', 'off', '')


def mfi_primary_tf_gate(npz, n, is_long, cfg, mfi_1h):
    """MFI entry gate with ENTRY_PRIMARY_TF selector. Default '4h' -> mfi_1h (today's vec)."""
    try:
        tf = str(getattr(cfg, 'ENTRY_PRIMARY_TF', '4h') or '4h').strip()
    except Exception:
        tf = '4h'
    if tf in _BASELINE_TFS:
        col = np.asarray(mfi_1h, dtype=np.float64).reshape(-1)
        if len(col) != n:
            return np.ones(n, dtype=bool)
    else:
        col = _safe(npz, f'mfi_{tf}', n, 50.0)
    try:
        lo = float(getattr(cfg, 'MFI_ENTRY_LONG_MAX', 60.0))
    except Exception:
        lo = 60.0
    try:
        hi = float(getattr(cfg, 'MFI_ENTRY_SHORT_MIN', 40.0))
    except Exception:
        hi = 40.0
    return (col < lo) if is_long else (col > hi)


def mfi_primary_tf_gate_scalar(npz, n, i, is_long, cfg, mfi_1h):
    try:
        tf = str(getattr(cfg, 'ENTRY_PRIMARY_TF', '4h') or '4h').strip()
    except Exception:
        tf = '4h'
    if tf in _BASELINE_TFS:
        try:
            v = float(np.asarray(mfi_1h).reshape(-1)[i])
        except Exception:
            return True
    else:
        v = _sone(npz, f'mfi_{tf}', n, i, 50.0)
    try:
        lo = float(getattr(cfg, 'MFI_ENTRY_LONG_MAX', 60.0))
    except Exception:
        lo = 60.0
    try:
        hi = float(getattr(cfg, 'MFI_ENTRY_SHORT_MIN', 40.0))
    except Exception:
        hi = 40.0
    return (v < lo) if is_long else (v > hi)


_WTDC_KEYS = ['wt1_D', 'wt2_D', 'wt1_4h', 'wt2_4h', 'dc_position_1h', 'stoch_k_5m', 'wt_cross_1h']


def wtdc_direct_block(npz, n, is_long, cfg, scorer):
    """B_WT_DC_DIRECT mask, or None when disabled (-1.0). scorer = score_entry_multitf_vec."""
    try:
        thr = float(getattr(cfg, 'WT_DC_DIRECT_THRESHOLD', -1.0))
    except Exception:
        return None
    if thr == -1.0:
        return None
    indic = {k: _safe(npz, k, n, 0.0) for k in _WTDC_KEYS}
    scores = scorer(indic, is_long, n=n)
    return np.asarray(scores >= thr, dtype=bool)
