"""MOP-UP M 2026-10-04 — vec twin of DD_BOUNCE_ENABLED (C41 wire, VEC_TWIN).

LIVE SOURCE (read-only reference): ez_manage.py::evaluate_augmentation :38961-39023.
  Losing position (pos_amt>0, gain<0) + DD_BOUNCE_ENABLED(False) -> WT bounce
  trigger on D then 4h: wt1 rising (long; falling short) vs prev, higher-WT and
  higher-price vs last aug (both required by default), 4h cooldown between DD
  augments -> AUGMENT dd_qty = pos_amt (1x add). Fires BEFORE the gain gate
  (bypasses gain>=0.3 AND the 120s aug-seconds gate). Crypto only (tradier has
  touch-reads only). Per-symbol state: last_aug_ts/wt/price.
  Prev convention: phantom wt1_X_prev -> same-bar wt2_X (2026-06-03 USER); NPZ has
  no _prev cols so the twin uses the wt2 fallback exactly like live.

TWIN (loop-called, N3-obligatory style): Prepared caches arrays; fires() takes the
  walk state (px, live_pnl, ts, per-position dd-state dict or None) and returns
  (fire, new_state). State inits mirror live exactly (ts 0.0, wt -999/999,
  px 0.0/inf) so the first evaluation passes cooldown/higher legs like live.
BIBLE: §17 (all six knobs default-aligned with config.py:2931-2936); §18 (master
  False -> Prepared unused -> honest-0).
"""
from __future__ import annotations

import numpy as np


def _col(npz, key, n, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


class Prepared:
    def __init__(self, npz, n, is_long, cfg, safe=None):
        self.n = n
        self.is_long = is_long
        g = (lambda k, d=0.0: safe(npz, k, n, d)) if safe is not None else (lambda k, d=0.0: _col(npz, k, n, d))
        self.w1_D = g('wt1_D', 0.0)
        self.w2_D = g('wt2_D', 0.0)
        self.w1_4h = g('wt1_4h', 0.0)
        self.w2_4h = g('wt2_4h', 0.0)
        # phantom-prev -> wt2 fallback (live :38975-38976, :38993-38994)
        self.w1_D_prev = g('wt1_D_prev', None) if 'wt1_D_prev' in (npz.files if hasattr(npz, 'files') else []) else None
        self.w1_4h_prev = g('wt1_4h_prev', None) if 'wt1_4h_prev' in (npz.files if hasattr(npz, 'files') else []) else None
        if self.w1_D_prev is None:
            self.w1_D_prev = self.w2_D
        if self.w1_4h_prev is None:
            self.w1_4h_prev = self.w2_4h


def _knobs(cfg):
    try:
        cd_s = float(getattr(cfg, 'DD_BOUNCE_COOLDOWN_HOURS', 4.0)) * 3600.0
    except Exception:
        cd_s = 4.0 * 3600.0
    return {
        'd_on': bool(getattr(cfg, 'DD_BOUNCE_WT_D_ENABLED', True)),
        'h4_on': bool(getattr(cfg, 'DD_BOUNCE_WT_4H_ENABLED', True)),
        'req_hwt': getattr(cfg, 'DD_BOUNCE_REQUIRE_HIGHER_WT', True),
        'req_hpx': getattr(cfg, 'DD_BOUNCE_REQUIRE_HIGHER_PRICE', True),
        'cd_s': cd_s,
    }


def fires(P, i, px, live_pnl_pct, state, ts_now, cfg):
    """(fire, new_state, trig_tf). state None -> live-equivalent fresh defaults."""
    if not bool(getattr(cfg, 'DD_BOUNCE_ENABLED', False)):
        return False, state, None
    if not (live_pnl_pct < 0):
        return False, state, None
    K = _knobs(cfg)
    if state is None:
        last_ts = 0.0
        last_wt = (-999.0 if P.is_long else 999.0)
        last_px = (0.0 if P.is_long else float('inf'))
    else:
        try:
            last_ts = float(state.get('ts', 0.0))
            last_wt = float(state.get('wt', -999.0 if P.is_long else 999.0))
            last_px = float(state.get('px', 0.0 if P.is_long else float('inf')))
        except Exception:
            return False, state, None
    try:
        now = float(ts_now)
    except Exception:
        return False, state, None
    if not ((now - last_ts) >= K['cd_s']):
        return False, state, None
    req_hwt = K['req_hwt']
    req_hpx = K['req_hpx']
    trig_tf = None
    trig_wt = 0.0
    if K['d_on']:
        w = float(P.w1_D[i]) if i < len(P.w1_D) else 0.0
        wp = float(P.w1_D_prev[i]) if i < len(P.w1_D_prev) else 0.0
        bounce = (w > wp) if P.is_long else (w < wp)
        hwt = ((not req_hwt) or ((w > last_wt) if P.is_long else (w < last_wt)))
        hpx = ((not req_hpx) or ((px > last_px) if P.is_long else (px < last_px)))
        if bounce and hwt and hpx:
            trig_tf, trig_wt = 'D', w
    if trig_tf is None and K['h4_on']:
        w = float(P.w1_4h[i]) if i < len(P.w1_4h) else 0.0
        wp = float(P.w1_4h_prev[i]) if i < len(P.w1_4h_prev) else 0.0
        bounce = (w > wp) if P.is_long else (w < wp)
        hwt = ((not req_hwt) or ((w > last_wt) if P.is_long else (w < last_wt)))
        hpx = ((not req_hpx) or ((px > last_px) if P.is_long else (px < last_px)))
        if bounce and hwt and hpx:
            trig_tf, trig_wt = '4h', w
    if trig_tf is None:
        return False, state, None
    return True, {'ts': now, 'wt': trig_wt, 'px': float(px)}, trig_tf
