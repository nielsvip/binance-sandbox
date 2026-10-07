"""ENTRY_DC pass mask — verbatim vec predicate (moved from v12:9066-9088, not rewritten).

Staged by lane entry-crypto 2026-10-03 (entry_dc_final_signal.diff, preferred
alternative): the inline block in compute_entry_signals constrained only
extra_ok→_base_entry and was bypassed by downstream OR-blocks (T2: binds
5.8-7.8% of bars, fp-identical = honest-zero). This module lets the same
predicate apply to the FINAL fresh-entry signal + watchdog opens.
"""
import numpy as np


def pass_mask(npz, n, is_long, cfg, close, safe):
    try:
        raw = str(getattr(cfg, 'ENTRY_DC_TF', 'OFF') or 'OFF').strip()
        if raw.upper() == 'OFF' or raw == '':
            return None
        tfs = [p.strip() for p in raw.replace('+', ',').replace('|', ',').replace(' ', ',').split(',') if p.strip() and p.strip().upper() != 'OFF']
        if not tfs:
            return None
        buf = float(getattr(cfg, 'ENTRY_DC_BUFFER_PCT', 0.10) or 0.10) / 100.0
        ok = np.zeros(n, dtype=bool)
        for etf in tfs:
            norm = {"5m": "3m"}.get(etf, etf)
            lo = safe(npz, f"dc_low_{norm}", n, 0)
            hi = safe(npz, f"dc_high_{norm}", n, 0)
            if np.all(lo == 0) and np.all(hi == 0):
                continue
            if is_long:
                ok = ok | ((lo > 0) & (close >= lo * (1 + buf))) | ((hi > 0) & (close >= hi * (1 + buf)))
            else:
                ok = ok | ((hi > 0) & (close <= hi * (1 - buf))) | ((lo > 0) & (close <= lo * (1 - buf)))
        return np.asarray(ok, dtype=bool) if np.any(ok) else None
    except Exception:
        return None
