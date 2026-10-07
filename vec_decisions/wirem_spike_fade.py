"""MOP-UP M 2026-10-04 — vec twin of CRYPTO_SPIKE_FADE entry-detect (B12).

LIVE SOURCE (read-only reference): ez_manage.py::crypto_spike_fade_loop :54585-54720
  (BC_162 contrarian spike fade). Entry-detect per symbol tick:
    ret = (px - oldest_px)/oldest_px*100 over ~30min buffer (min age 300s);
    |ret| >= CRYPTO_SPIKE_FADE_THRESHOLD_PCT (live 10.0);
    SHORT (fade pump): pump_fading (3m LH+LL: high<high_prev & low<low_prev)
      OR price_below_prev_high (px < high_3m_prev);
    LONG (fade dump): dump_reversing (low>low_prev & high>high_prev)
      OR price_above_prev_low (px > low_3m_prev).
  (K-exhaustion knob is read but never applied in live; not twinned.)
  Daemon lifecycle (price buffers, 900s cooldown, max 6 positions, order queue)
  is OUT OF SCOPE per lane-B recipe — detect-only twin.

TWIN: per-bar vector mask (crypto only; tradier has stub reads only).
  30min lookback = 10 bars on the 3m grid; min 2-bar age mirrors live's 300s
  minimum. Returns None when CRYPTO_SPIKE_FADE_ENABLED is off.
  APPROX: live uses tick buffers (partial windows down to 300s); the twin uses
  full 10-bar closes — conservative (fewer, cleaner spikes).
BIBLE: §17 (threshold default 10.0 == live config.py:2509); §43 entry OR-block.
"""
from __future__ import annotations

import numpy as np

_LOOKBACK = 10
_MIN_AGE_BARS = 2


def _col(npz, key, n, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def spike_fade_block(npz, n, is_long, cfg):
    """Entry OR-block mask, or None when the master is off / stocks mode."""
    try:
        if not bool(getattr(cfg, 'CRYPTO_SPIKE_FADE_ENABLED', True)):
            return None
        if str(getattr(cfg, 'MODE', 'crypto')) == 'tradier':
            return None
        thr = float(getattr(cfg, 'CRYPTO_SPIKE_FADE_THRESHOLD_PCT', 10.0))
    except Exception:
        return None
    px = _col(npz, 'close_3m', n, 0.0)
    if not np.any(px > 0):
        px = _col(npz, 'close', n, 0.0)
    hi = _col(npz, 'high_3m', n, 0.0)
    lo = _col(npz, 'low_3m', n, 0.0)
    hi_p = _col(npz, 'high_3m_prev', n, 0.0)
    lo_p = _col(npz, 'low_3m_prev', n, 0.0)
    out = np.zeros(n, dtype=bool)
    for i in range(n):
        if i < _MIN_AGE_BARS:
            continue
        ref = max(i - _LOOKBACK, 0)  # bars 2-9: partial window (live min-age analog)
        base = px[ref]
        if not (base > 0) or not (px[i] > 0):
            continue
        ret = (px[i] - base) / base * 100.0
        if is_long:
            if not (ret <= -thr):
                continue
            dump_rev = (lo[i] > 0 and lo_p[i] > 0 and lo[i] > lo_p[i]
                        and hi[i] > 0 and hi_p[i] > 0 and hi[i] > hi_p[i])
            above_lo = (px[i] > lo_p[i]) if lo_p[i] > 0 else False
            if dump_rev or above_lo:
                out[i] = True
        else:
            if not (ret >= thr):
                continue
            pump_fade = (hi[i] > 0 and hi_p[i] > 0 and hi[i] < hi_p[i]
                         and lo[i] > 0 and lo_p[i] > 0 and lo[i] < lo_p[i])
            below_hi = (px[i] < hi_p[i]) if hi_p[i] > 0 else False
            if pump_fade or below_hi:
                out[i] = True
    return out


def spike_fade_block_scalar(npz, n, i, is_long, cfg):
    """Scalar reference for bar i (same predicate). Returns bool."""
    m = spike_fade_block(npz, n, is_long, cfg)
    if m is None:
        return False
    return bool(m[i]) if 0 <= i < len(m) else False
