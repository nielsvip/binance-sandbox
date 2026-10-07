"""WIRING LANE C — vec twins of execute_now augment/entry guards (lane C batch L1b).

LIVE SOURCES (read-only reference):
  MTF_FILTER_STRONG_BUY_QUICK_BYPASS (default True): ez_manage.py::execute_now:30319-30337 —
    fresh opens whose reason/action carries STRONG_BUY/QUICK_OPEN/TRADEABLE_KEYS_MANDATORY/
    WT_3M_FORCE_OPEN/DC_BREAKOUT/DC_HIGH_3M/DC_LOW_3M/SCALP_V3_OPEN/BAR_BREAK/
    MOMENTUM_SMA15M_WATCHDOG/LR_BAND/MTF_ARROW tokens bypass the MTF armed-state veto.
    APPROXIMATION (documented): live veto is a stateful in-memory armed flag (restart-wiped,
    unmodelable in vec); vec models the knob as strictness of the direction proxy
    (mtf_armed_ok suspend): True (default) -> today's vec, no change; False -> require
    1h AND 4h WT alignment (both, not OR). Delta direction matches live (False = fewer).
  RECENT_REDUCTION_GUARD_ENABLED: ez_manage.py::execute_now:31918-31945 — augment/re-add
    blocked when a reduce fired within RECENT_REDUCTION_GUARD_WINDOW_S (crypto 300s /
    stocks 450s) unless Donchian breakout (dc_high4_3m> *1.001 long / dc_low4_3m *0.999
    short, fallback dc_*_1h_prev; no level -> fail-CLOSED; exception -> fail-open) or a
    PRICE_CROSS/DAEMON/GUARANTEED/OBLIGATORY reason bypass.
    DIVERGENCE (documented): live default True (config.py:882, enabled 2026-06-03);
    vec default False per crash mandate (inert until promotion) — §17 gap to close then.
    APPROXIMATIONS: (1) vec reduce_sig covers modeled reduces only (live stamps every
    reduce incl. manual) -> vec blocks LESS; (2) no reason tags in vec -> price-cross
    bypass absent -> vec blocks MORE for those; (3) bar-time window vs wall-clock.
    USE_4BAR key dc_high4_3m missing from NPZs -> live's own 1h_prev fallback applies.

BIBLE: §43 parity gate; §17 curated parity (companions default to live values; ENABLED
divergence noted above); §18 honest-0 (defaults inert).
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


def _thr_cfg(cfg, name, default):
    # Plain getattr: every switch here has an explicit QuickConfig field, so an
    # explicit False (e.g. bypass OFF) is meaningful and must NOT fall back.
    return getattr(cfg, name, default)


# ---------------- MTF_FILTER_STRONG_BUY_QUICK_BYPASS ----------------
def mtf_bypass_strict_allow(npz, n, is_long, cfg):
    """Default True -> all allow (today's vec). False -> require 1h AND 4h WT alignment."""
    if bool(_thr_cfg(cfg, 'MTF_FILTER_STRONG_BUY_QUICK_BYPASS', True)):
        return np.ones(n, dtype=bool)
    w1h1 = _safe(npz, 'wt1_1h', n, 0.0)
    w1h2 = _safe(npz, 'wt2_1h', n, 0.0)
    w4h1 = _safe(npz, 'wt1_4h', n, 0.0)
    w4h2 = _safe(npz, 'wt2_4h', n, 0.0)
    if is_long:
        return (w1h1 > w1h2) & (w4h1 > w4h2)
    return (w1h1 < w1h2) & (w4h1 < w4h2)


def mtf_bypass_strict_allow_scalar(npz, n, i, is_long, cfg):
    if bool(_thr_cfg(cfg, 'MTF_FILTER_STRONG_BUY_QUICK_BYPASS', True)):
        return True
    a = _sone(npz, 'wt1_1h', n, i, 0.0)
    b = _sone(npz, 'wt2_1h', n, i, 0.0)
    c = _sone(npz, 'wt1_4h', n, i, 0.0)
    d = _sone(npz, 'wt2_4h', n, i, 0.0)
    if is_long:
        return (a > b) and (c > d)
    return (a < b) and (c < d)


# ---------------- RECENT_REDUCTION_GUARD ----------------
def _guard_bars(ts, n, window_s):
    try:
        t = np.asarray(ts, dtype=np.float64).reshape(-1)
        if len(t) != n or n < 2:
            return 1
        dt = float(np.median(np.diff(t)))
        if not np.isfinite(dt) or dt <= 0:
            return 1
        return max(1, int(np.ceil(float(window_s) / dt)))
    except Exception:
        return 1


def _lvl_key(use_4bar, is_long):
    if is_long:
        return 'dc_high4_3m' if use_4bar else 'dc_high_3m'
    return 'dc_low4_3m' if use_4bar else 'dc_low_3m'


def _fb_key(is_long):
    return 'dc_high_1h_prev' if is_long else 'dc_low_1h_prev'


def recent_reduction_guard_allow(npz, n, is_long, cfg, reduce_sig, ts):
    """Default False -> all allow. True -> block augments within WINDOW_S of a reduce bar
    unless Donchian breakout. reduce_sig: bool array len n. ts: timestamps array."""
    if not bool(_thr_cfg(cfg, 'RECENT_REDUCTION_GUARD_ENABLED', False)):
        return np.ones(n, dtype=bool)
    try:
        rs = np.asarray(reduce_sig, dtype=bool).reshape(-1)
        if len(rs) != n:
            return np.ones(n, dtype=bool)
    except Exception:
        return np.ones(n, dtype=bool)
    if not np.any(rs):
        return np.ones(n, dtype=bool)
    window_s = float(_thr_cfg(cfg, 'RECENT_REDUCTION_GUARD_WINDOW_S', 300.0) or 300.0)
    use_4bar = bool(_thr_cfg(cfg, 'RECENT_REDUCTION_GUARD_USE_4BAR', True))
    w = _guard_bars(ts, n, window_s)
    px = _safe(npz, 'close_15m', n, 0.0)
    lvl = _safe(npz, _lvl_key(use_4bar, is_long), n, 0.0)
    fb = _safe(npz, _fb_key(is_long), n, 0.0)
    lvl = np.where(lvl > 0, lvl, fb)
    if is_long:
        breakout = (px > 0) & (lvl > 0) & (px > lvl * 1.001)
    else:
        breakout = (px > 0) & (lvl > 0) & (px < lvl * 0.999)
    # in_window[i]: a reduce fired in bars [i-w+1, i] (same-bar reduce blocks too, like live)
    cs = np.concatenate(([0], np.cumsum(rs.astype(np.int64))))
    idx = np.arange(n)
    lo = np.clip(idx - w + 1, 0, n)
    in_window = (cs[idx + 1] - cs[lo]) > 0
    return ~(in_window & ~breakout)


def recent_reduction_guard_allow_scalar(npz, n, i, is_long, cfg, reduce_sig, ts):
    if not bool(_thr_cfg(cfg, 'RECENT_REDUCTION_GUARD_ENABLED', False)):
        return True
    try:
        rs = np.asarray(reduce_sig, dtype=bool).reshape(-1)
        if len(rs) != n:
            return True
    except Exception:
        return True
    window_s = float(_thr_cfg(cfg, 'RECENT_REDUCTION_GUARD_WINDOW_S', 300.0) or 300.0)
    use_4bar = bool(_thr_cfg(cfg, 'RECENT_REDUCTION_GUARD_USE_4BAR', True))
    w = _guard_bars(ts, n, window_s)
    lo = max(0, i - w + 1)
    if not bool(np.any(rs[lo:i + 1])):
        return True
    px = _sone(npz, 'close_15m', n, i, 0.0)
    lvl = _sone(npz, _lvl_key(use_4bar, is_long), n, i, 0.0)
    if lvl <= 0:
        lvl = _sone(npz, _fb_key(is_long), n, i, 0.0)
    if px <= 0 or lvl <= 0:
        return False  # fail-closed, like live
    if is_long:
        return bool(px > lvl * 1.001)
    return bool(px < lvl * 0.999)
