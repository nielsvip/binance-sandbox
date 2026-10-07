"""WIRING LANE C — vec twin of tradier BAND_ARROW entry (batch M2b, 2 switches).

LIVE SOURCE (read-only reference): tradier_manage.py::band_arrow_score:10135-10163 —
  master BAND_ARROW_ENABLED (live False); TFS BAND_ARROW_ENTRY_TFS ("D,4h,1h");
  per TF: LONG fires when lrL_slope_TF > BAND_ARROW_SLOPE_DEADBAND (live 0.0) and
  lrL_pct_b_TF < 0.5; SHORT when slope < -deadband and pct_b > 0.5. Any-TF-OR.
  Missing fields -> no fire (fail-closed per TF).
TWIN: entry OR-block B_BAND_ARROW. Default OFF -> no block -> zero behavior change.
BIBLE: §43 parity gate; §17 (defaults aligned to live); §18 honest-0.
"""
from __future__ import annotations

import numpy as np


def _present(npz, key, n):
    v = npz.get(key) if hasattr(npz, 'get') else None
    return v is not None and isinstance(v, np.ndarray) and len(v) == n


def band_arrow_block(npz, n, is_long, cfg):
    """Entry OR-block mask, or None when BAND_ARROW_ENABLED is off (default)."""
    if not bool(getattr(cfg, 'BAND_ARROW_ENABLED', False)):
        return None
    try:
        tfs = [t.strip() for t in str(getattr(cfg, 'BAND_ARROW_ENTRY_TFS', 'D,4h,1h')).split(',') if t.strip()]
    except Exception:
        tfs = ['D', '4h', '1h']
    try:
        deadband = float(getattr(cfg, 'BAND_ARROW_SLOPE_DEADBAND', 0.0))
    except Exception:
        deadband = 0.0
    fire = np.zeros(n, dtype=bool)
    for tf in tfs:
        if not (_present(npz, f'lrL_slope_{tf}', n) and _present(npz, f'lrL_pct_b_{tf}', n)):
            continue
        sl = np.asarray(npz[f'lrL_slope_{tf}'], dtype=np.float64)
        pb = np.asarray(npz[f'lrL_pct_b_{tf}'], dtype=np.float64)
        if is_long:
            fire = fire | ((sl > deadband) & (pb < 0.5))
        else:
            fire = fire | ((sl < -deadband) & (pb > 0.5))
    return fire


def band_arrow_block_scalar(npz, n, i, is_long, cfg):
    """Scalar reference for bar i. Returns bool (False when master off)."""
    if not bool(getattr(cfg, 'BAND_ARROW_ENABLED', False)):
        return False
    try:
        tfs = [t.strip() for t in str(getattr(cfg, 'BAND_ARROW_ENTRY_TFS', 'D,4h,1h')).split(',') if t.strip()]
    except Exception:
        tfs = ['D', '4h', '1h']
    try:
        deadband = float(getattr(cfg, 'BAND_ARROW_SLOPE_DEADBAND', 0.0))
    except Exception:
        deadband = 0.0
    for tf in tfs:
        if not (_present(npz, f'lrL_slope_{tf}', n) and _present(npz, f'lrL_pct_b_{tf}', n)):
            continue
        try:
            sl = float(np.asarray(npz[f'lrL_slope_{tf}'])[i])
            pb = float(np.asarray(npz[f'lrL_pct_b_{tf}'])[i])
        except Exception:
            continue
        if is_long and sl > deadband and pb < 0.5:
            return True
        if (not is_long) and sl < -deadband and pb > 0.5:
            return True
    return False
