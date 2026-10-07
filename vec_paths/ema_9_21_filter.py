"""vec_paths/ema_9_21_filter.py — KINDERGARTEN EMA filter REWORKED 2026-08-23 per user.

OLD: 4h EMA200 blanket (bluntly cutting off trades for half the year, 4h200 blocks 35-50%)
NEW per user 2026-08-23: test shorter TFs (15m, 1h, 4h) and 9/21 or EMA/SMA crosses instead of blunt 4h200.
- TF sweep: 15m (scalps), 1h (swings), 4h (trends) — user: shorter TFs
- Cross types: ema9 > ema21 (fast cross), ema > sma (trend), price > ema200 (original) — user: 9/21 or ema/sma
- Sweep via KINDERGARTEN_TF and KINDERGARTEN_CROSS_TYPE in beam (46 groups)
Wired via v8_vec_sweep kindergarten_gate (side-aware) and ez_manage _kindergarten_ema_gate.
"""
from __future__ import annotations
import numpy as np

def _arr(npz, key, n):
    a = npz.get(key)
    if a is None:
        return None
    try:
        arr = np.asarray(a, dtype=np.float32)
        if len(arr) != n:
            if len(arr) < n:
                pad = np.zeros(n - len(arr), dtype=np.float32)
                arr = np.concatenate([pad, arr])
            else:
                arr = arr[-n:]
        return np.nan_to_num(arr, nan=0.0)
    except Exception:
        return None

def score(npz, config):
    """Side-agnostic score for _strength_open_ok: passes if kindergarten EMA determinable."""
    close = _arr(npz, 'close', 0)
    if close is None:
        close = _arr(npz, 'close_3m', 0)
    if close is None:
        close = _arr(npz, 'close_5m', 0)
    if close is None:
        return np.ones(1, dtype=bool)
    n = len(close)
    if not bool(getattr(config, 'KINDERGARTEN_EMA_GATE_ENABLED', False)):
        return np.ones(n, dtype=bool)
    # check determinable for current TF/cross
    tf = str(getattr(config, 'KINDERGARTEN_TF', '1h'))
    cross = str(getattr(config, 'KINDERGARTEN_CROSS_TYPE', 'ema9_21'))
    if cross == 'ema200':
        ema = _arr(npz, f'ema_200_{tf}', n)
        return (ema != 0) if ema is not None else np.ones(n, dtype=bool)
    elif cross == 'ema9_21':
        ema9 = _arr(npz, f'ema_9_{tf}', n)
        ema21 = _arr(npz, f'ema_21_{tf}', n)
        # 2026-09-10 FIX vs B&H: ema_21 missing in stocks NPZ (has ema_9 but no ema_21) → fallback to ema_20/ema_50/ema_200
        if ema21 is None or not np.any(ema21 != 0):
            ema21 = _arr(npz, f'ema_20_{tf}', n)
        if ema21 is None or not np.any(ema21 != 0):
            ema21 = _arr(npz, f'ema_50_{tf}', n)
        if ema9 is None or ema21 is None:
            return np.ones(n, dtype=bool)
        return (ema9 != 0) & (ema21 != 0)
    else:  # ema_sma
        ema = _arr(npz, f'ema_21_{tf}', n)
        sma = _arr(npz, f'sma_50_{tf}', n)
        if ema is None or sma is None:
            ema = _arr(npz, f'ema_200_{tf}', n)
            sma = _arr(npz, f'sma_200_{tf}', n)
        if ema is None or sma is None:
            return np.ones(n, dtype=bool)
        return (ema != 0) & (sma != 0)

def kindergarten_gate(npz, is_long: bool):
    """Side-aware kindergarten: shorter TFs + 9/21 or EMA/SMA crosses.

    TF from config KINDERGARTEN_TF (15m/1h/4h), cross from KINDERGARTEN_CROSS_TYPE:
    - ema200: price >/< ema200_TF (original)
    - ema9_21: ema9 >/< ema21_TF (fast cross)
    - ema_sma: ema21 >/< sma50_TF
    Falls back across TFs if missing.
    """
    import numpy as np
    # Config-driven TF and cross
    try:
        import config as cfg_mod
        tf_cfg = str(getattr(cfg_mod, 'KINDERGARTEN_TF', '1h'))
        cross_cfg = str(getattr(cfg_mod, 'KINDERGARTEN_CROSS_TYPE', 'ema9_21'))
    except:
        tf_cfg='1h'; cross_cfg='ema9_21'
    # Also check per-symbol override via global config instance (v8_vec_sweep sets via _apply_per_task_overrides)
    try:
        import v8_vec_sweep as v8
        if hasattr(v8, '_current_config'):
            tf_cfg = str(getattr(v8._current_config, 'KINDERGARTEN_TF', tf_cfg))
            cross_cfg = str(getattr(v8._current_config, 'KINDERGARTEN_CROSS_TYPE', cross_cfg))
    except: pass
    # Try per-indicator cache override (backtest sets via config object)
    # Use the most recent config object if available via indicator_cache
    tfs_to_try = []
    # Ensure shorter TFs first: 15m,1h,4h as user requested
    ordered_tfs = []
    for tf in [tf_cfg]:
        if tf not in ordered_tfs: ordered_tfs.append(tf)
    for tf in ['15m','1h','4h']:
        if tf not in ordered_tfs: ordered_tfs.append(tf)
    for tf in ['5m','3m']:
        if tf not in ordered_tfs: ordered_tfs.append(tf)

    for tf in ordered_tfs:
        close_key = 'close' if 'close' in npz else ('close_3m' if 'close_3m' in npz else 'close_5m')
        close = _arr(npz, close_key, 0)
        if close is None:
            continue
        n = len(close)
        if cross_cfg == 'ema200':
            ema = _arr(npz, f'ema_200_{tf}', n)
            if ema is None or not np.any(ema != 0):
                continue
            if is_long:
                return np.where(ema != 0, close > ema, True)
            else:
                return np.where(ema != 0, close < ema, True)
        elif cross_cfg == 'ema9_21':
            ema9 = _arr(npz, f'ema_9_{tf}', n)
            ema21 = _arr(npz, f'ema_21_{tf}', n)
            # 2026-09-10 FIX: fallback ema_21 → ema_20/ema_50 when missing (stocks NPZ)
            if ema21 is None or not np.any(ema21 != 0):
                ema21 = _arr(npz, f'ema_20_{tf}', n)
            if ema21 is None or not np.any(ema21 != 0):
                ema21 = _arr(npz, f'ema_50_{tf}', n)
            if ema9 is None or ema21 is None or not np.any((ema9 != 0) & (ema21 != 0)):
                # final fallback: ema_9 missing on AAPL → use price vs sma_200/ema_200
                if is_long:
                    c = _arr(npz, f'close_{tf}', n)
                    s = _arr(npz, f'sma_200_{tf}', n) or _arr(npz, f'ema_200_{tf}', n)
                    if c is not None and s is not None and np.any(s != 0):
                        return np.where(s != 0, c > s, True)
                else:
                    c = _arr(npz, f'close_{tf}', n)
                    s = _arr(npz, f'sma_200_{tf}', n) or _arr(npz, f'ema_200_{tf}', n)
                    if c is not None and s is not None and np.any(s != 0):
                        return np.where(s != 0, c < s, True)
                continue
            if is_long:
                return np.where((ema9 != 0) & (ema21 != 0), ema9 > ema21, True)
            else:
                return np.where((ema9 != 0) & (ema21 != 0), ema9 < ema21, True)
        else:  # ema_sma
            ema = _arr(npz, f'ema_21_{tf}', n)
            sma = _arr(npz, f'sma_50_{tf}', n)
            if ema is None or sma is None:
                ema = _arr(npz, f'ema_50_{tf}', n)
                sma = _arr(npz, f'sma_50_{tf}', n)
            if ema is None or sma is None or not np.any((ema != 0) & (sma != 0)):
                continue
            if is_long:
                return np.where((ema != 0) & (sma != 0), ema > sma, True)
            else:
                return np.where((ema != 0) & (sma != 0), ema < sma, True)
    close = _arr(npz, 'close', 0)
    if close is None:
        close = _arr(npz, 'close_3m', 0)
    if close is None:
        close = _arr(npz, 'close_5m', 0)
    if close is None:
        return np.ones(1, dtype=bool)
    return np.ones(len(close), dtype=bool)
