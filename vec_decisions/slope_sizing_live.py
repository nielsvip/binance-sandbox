"""SLOPE_SIZING_LIVE — vector twin of the live band/stdev slope sizing multiplier (Agent UNW-V, queue UNWV/001).

LIVE SOURCES (same maths, venue-specific plumbing):
  STOCKS  tradier_manage.py calculate_position_size (~28332-28370, `_cfg_auto('BAND_SLOPE_SIZING_V2_ENABLED') or STDEV_SLOPE_SIZING_ENABLED`):
          STDEV on  -> max = {D:STDEV_SLOPE_SIZING_D_MAX, 4h:..4H_MAX, 1h:..1H_MAX, 15m:..15M_MAX}[BAND_SLOPE_SIZING_V2_TF] (else BAND_SLOPE_SIZING_V2_MAX), min = BAND_SLOPE_SIZING_V2_MIN
          mode bottom_to_top : m = 1 + (max-1)*edge_full ; slope_to_top : edge>=0.5 -> max else 1+(max-1)*(2*edge) ; other : 1 + DEPTH_GAIN*(edge-0.5)*2
          edge = (1-lrL_pct_b_TF) long / lrL_pct_b_TF short ; slope_day = lrL_slope_TF * {1h:6.5, 4h:1.625, D:1, 15m:26}
          sn = min(|slope_day|/SLOPE_NORM_PCT_DAY, 1) ; m *= (1+0.5*sn) if slope favours the side else max(0.5, 1-0.5*sn) ; m = clamp(m, min, max)
  CRYPTO  ez_manage.py execute_trade_action central chokepoint (~26993-27012, every non-QUICK crypto open): BAND_SLOPE_SIZING_V2_ENABLED only,
          DEPTH_GAIN formula (no stdev map / no mode), slope_day = lrL_slope_TF * {1h:24, 4h:6, D:1} (default 6), clamp(BAND_SLOPE_SIZING_V2_MIN, ..._MAX).
          (ez_positions_quick.calculate_dynamic_quantity has the stdev/mode variant but it is the QUICK path, dead by ABLATION_DISABLE_QUICK_ENTRY.)
Reads only 15m+ NPZ arrays (lrL_pct_b_*, lrL_slope_*): no 3m/5m.
"""
import numpy as np

_STOCK_DAY = {'1h': 6.5, '4h': 1.625, 'D': 1.0, '15m': 26.0}
_CRYPTO_DAY = {'1h': 24.0, '4h': 6.0, 'D': 1.0}


def _arr(npz, key, n, default):
    a = npz.get(key) if hasattr(npz, 'get') else None
    if a is None:
        return np.full(n, default, dtype=np.float64)
    a = np.asarray(a, dtype=np.float64)
    if a.shape[0] < n:
        a = np.concatenate([a, np.full(n - a.shape[0], default)])
    a = a[:n].copy()
    a[~np.isfinite(a)] = default
    return a


def mult(npz, n, is_long, cfg):
    """Per-bar multiplier array (ones if the live sizing is off or its NPZ arrays are absent)."""
    one = np.ones(n, dtype=np.float64)
    tradier = str(getattr(cfg, 'MODE', 'crypto')) == 'tradier'
    band_on = bool(getattr(cfg, 'BAND_SLOPE_SIZING_V2_ENABLED', False))
    stdev_on = bool(getattr(cfg, 'STDEV_SLOPE_SIZING_ENABLED', False))
    if tradier:
        if not (band_on or stdev_on):
            return one
    elif not band_on:
        return one
    tf = str(getattr(cfg, 'BAND_SLOPE_SIZING_V2_TF', 'D' if tradier else '4h'))
    if f'lrL_pct_b_{tf}' not in npz or f'lrL_slope_{tf}' not in npz:
        return one
    pb = _arr(npz, f'lrL_pct_b_{tf}', n, 0.5)
    sl = _arr(npz, f'lrL_slope_{tf}', n, 0.0)
    bmin = float(getattr(cfg, 'BAND_SLOPE_SIZING_V2_MIN', 0.5))
    bmax = float(getattr(cfg, 'BAND_SLOPE_SIZING_V2_MAX', 2.5))
    gain = float(getattr(cfg, 'BAND_SLOPE_SIZING_V2_DEPTH_GAIN', 1.0))
    norm = max(1e-9, float(getattr(cfg, 'BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY', 1.0)))
    edge = (1.0 - pb) if is_long else pb
    if tradier:
        day = _STOCK_DAY.get(tf, 1.0)
        if stdev_on:
            smap = {'D': float(getattr(cfg, 'STDEV_SLOPE_SIZING_D_MAX', 10.0)), '4h': float(getattr(cfg, 'STDEV_SLOPE_SIZING_4H_MAX', 4.0)),
                    '1h': float(getattr(cfg, 'STDEV_SLOPE_SIZING_1H_MAX', 2.0)), '15m': float(getattr(cfg, 'STDEV_SLOPE_SIZING_15M_MAX', 1.5))}
            bmax = smap.get(tf, bmax)
        mode = str(getattr(cfg, 'STDEV_SLOPE_SIZING_MODE', 'slope_to_top'))
        if mode == 'bottom_to_top':
            m = 1.0 + (bmax - 1.0) * edge
        elif mode == 'slope_to_top':
            m = np.where(edge >= 0.5, bmax, 1.0 + (bmax - 1.0) * (edge * 2.0))
        else:
            m = 1.0 + gain * (edge - 0.5) * 2.0
    else:
        day = _CRYPTO_DAY.get(tf, 6.0)
        m = 1.0 + gain * (edge - 0.5) * 2.0
    slope_day = sl * day
    sn = np.minimum(np.abs(slope_day) / norm, 1.0)
    fav = (slope_day > 0) if is_long else (slope_day < 0)
    m = m * np.where(fav, 1.0 + 0.5 * sn, np.maximum(0.5, 1.0 - 0.5 * sn))
    m = np.maximum(bmin, np.minimum(bmax, m))
    return np.where(np.isfinite(m), m, 1.0)
