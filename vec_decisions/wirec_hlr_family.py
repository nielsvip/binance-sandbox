"""WIRING LANE C — vec twins of the HLR rally detector + HLR reentry size floor (batch L1f).

LIVE SOURCES (read-only reference, ez_positions_quick.py):
  HLR_RALLY (:3071-3101, entry scoring): per-TF (3m/15m/1h/4h/D/W) structure
    (higher-low long / lower-high short on low/high vs prev, >0-guarded) + near-SMA
    (|px-sma200|/sma <= HLR_SMA_BAND_PCT 0.03) -> +PTS (near) or PTS*OFF_PTS_FRAC
    (off); size mult max(SZ, off*OFF_SZ_FRAC) capped HLR_SZ_MAX; _hlr_bypass when
    pts_add >= HLR_BYPASS_MIN_TF_WEIGHT (25) — bypasses the 'SHIT IDEA' WAIT veto.
  TWIN: entry OR-block firing when any TF has (structure & near-SMA & pts>=bypass_min).
    At defaults every near-SMA TF passes (min PTS 25 = bypass_min). Off-SMA frac points
    do NOT fire the block (conservative: live may still cross threshold; documented).
    3m TF dropped (no 3m data). Gated on HLR_RALLY_ENABLED — DIVERGENCE: live True,
    vec False until promotion (sole vec consumer; verified no other reader).
  HLR_TOP_EXIT registry (:3720-3745): velocity/exhaust/div/peak conditions per TF
    (1h/4h/D/W) -> tfs list + mult=max(1H/4H/D/W mults); store {qty,FULL pos qty),mult,
    ts} when len(tfs)>=HLR_TOP_MIN_TFS(2) with an HTF (4h/D/W) member.
  HLR reentry floor (:16359-16370): on reentry sizing, if record and age<HLR_REENTRY_MAX_AGE_S
    (14400) and qty>0 and px>0: qty=min(max(qty,prev_qty*mult),START*HLR_SZ_MAX/px); one-shot del.
  TWIN: loop records (bar_ts, exited_qty, mult) at vec HLR exits (_qr_fire) and applies the
    floor at reentry opens. Inert at defaults because _qr_fire requires the sanction
    (HLR_TOP_EXIT_LIVE_SANCTIONED False) — no records exist until promotion.
    APPROXIMATIONS: momentum/divergence/peak string states missing from NPZs -> those
    disjuncts False (fail-closed on mult); velocities via _safe default 0 (fail-closed:
    long vel<0-thr / short vel>0 never true at 0... except 1h short: vel>abs(-1.0)=1.0,
    false at 0 ✓; 4h/D/W long vel<0 false at 0 ✓, short vel>0 false ✓).
BIBLE: §43 parity gate; §17 (rally-master divergence noted); §18 honest-0.
"""
from __future__ import annotations

import numpy as np

_RALLY_TFS = ('15m', '1h', '4h', 'D', 'W')


def _safe(npz, key, n, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def rally_block(npz, n, is_long, cfg):
    """Entry OR-block mask, or None when HLR_RALLY_ENABLED is off (vec default)."""
    if not bool(getattr(cfg, 'HLR_RALLY_ENABLED', False)):
        return None
    try:
        band = float(getattr(cfg, 'HLR_SMA_BAND_PCT', 0.03))
    except Exception:
        band = 0.03
    try:
        bypass_min = float(getattr(cfg, 'HLR_BYPASS_MIN_TF_WEIGHT', 25))
    except Exception:
        bypass_min = 25.0
    px = _safe(npz, 'close_15m', n, 0.0)
    fire = np.zeros(n, dtype=bool)
    for tf in _RALLY_TFS:
        tag = tf.upper().replace('M', 'm').replace('H', 'h')
        try:
            pts = float(getattr(cfg, f'HLR_PTS_{tag}', getattr(cfg, f'HLR_PTS_{tf}', 25)))
        except Exception:
            pts = 25.0
        if pts < bypass_min:
            continue
        lo = _safe(npz, f'low_{tf}', n, 0.0)
        hi = _safe(npz, f'high_{tf}', n, 0.0)
        lop = _safe(npz, f'low_{tf}_prev', n, 0.0)
        hip = _safe(npz, f'high_{tf}_prev', n, 0.0)
        sma = _safe(npz, f'sma_200_{tf}', n, 0.0)
        if is_long:
            struct = (lo > 0) & (lop > 0) & (lo > lop)
        else:
            struct = (hi > 0) & (hip > 0) & (hi < hip)
        near = (sma > 0) & (np.abs(px - sma) / np.maximum(sma, 1e-12) <= band)
        fire = fire | (struct & near)
    return fire


def rally_block_scalar(npz, n, i, is_long, cfg):
    """Scalar reference for bar i. Returns bool (False when master off)."""
    if not bool(getattr(cfg, 'HLR_RALLY_ENABLED', False)):
        return False
    try:
        band = float(getattr(cfg, 'HLR_SMA_BAND_PCT', 0.03))
    except Exception:
        band = 0.03
    try:
        bypass_min = float(getattr(cfg, 'HLR_BYPASS_MIN_TF_WEIGHT', 25))
    except Exception:
        bypass_min = 25.0

    def one(key, default=0.0):
        v = npz.get(key) if hasattr(npz, 'get') else None
        if v is not None and isinstance(v, np.ndarray) and len(v) == n:
            try:
                return float(v[i])
            except Exception:
                return default
        return default

    px = one('close_15m')
    for tf in _RALLY_TFS:
        tag = tf.upper().replace('M', 'm').replace('H', 'h')
        try:
            pts = float(getattr(cfg, f'HLR_PTS_{tag}', getattr(cfg, f'HLR_PTS_{tf}', 25)))
        except Exception:
            pts = 25.0
        if pts < bypass_min:
            continue
        lo, hi = one(f'low_{tf}'), one(f'high_{tf}')
        lop, hip = one(f'low_{tf}_prev'), one(f'high_{tf}_prev')
        sma = one(f'sma_200_{tf}')
        if is_long:
            struct = (lo > 0) and (lop > 0) and (lo > lop)
        else:
            struct = (hi > 0) and (hip > 0) and (hi < hip)
        if not struct:
            continue
        if (sma > 0) and (abs(px - sma) / max(sma, 1e-12) <= band):
            return True
    return False


def _vel(npz, key, n, i):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        try:
            return float(v[i])
        except Exception:
            return 0.0
    return 0.0


def hlr_exit_mult(npz, n, i, is_long, cfg):
    """Recompute the HLR registry mult + HTF-ok at bar i. Returns (mult, htfs_ok, n_tfs)."""
    try:
        v1 = float(getattr(cfg, 'HLR_TOP_VEL_1H_THRESH', -1.0))
    except Exception:
        v1 = -1.0
    try:
        v4 = float(getattr(cfg, 'HLR_TOP_VEL_4H_THRESH', 0.0))
    except Exception:
        v4 = 0.0
    try:
        vd = float(getattr(cfg, 'HLR_TOP_VEL_D_THRESH', 0.0))
    except Exception:
        vd = 0.0
    vel1h = _vel(npz, 'wt_velocity_1h', n, i)
    vel4h = _vel(npz, 'wt_velocity_4h', n, i)
    velD = _vel(npz, 'wt_velocity_D', n, i)
    velW = _vel(npz, 'wt_velocity_W', n, i)
    tfs = []
    mult = 1.0
    if (is_long and vel1h < v1) or ((not is_long) and vel1h > abs(v1)):
        tfs.append('1h')
        try:
            mult = max(mult, float(getattr(cfg, 'HLR_REENTRY_MULT_1H', 1.5)))
        except Exception:
            pass
    ok4 = ((is_long and vel4h < v4) or ((not is_long) and vel4h > 0))
    if ok4:
        tfs.append('4h')
        try:
            mult = max(mult, float(getattr(cfg, 'HLR_REENTRY_MULT_4H', 2.0)))
        except Exception:
            pass
    okD = ((is_long and velD < vd) or ((not is_long) and velD > 0))
    if okD:
        tfs.append('D')
        try:
            mult = max(mult, float(getattr(cfg, 'HLR_REENTRY_MULT_D', 2.5)))
        except Exception:
            pass
    okW = ((is_long and velW < 0) or ((not is_long) and velW > 0))
    if okW:
        tfs.append('W')
        try:
            mult = max(mult, float(getattr(cfg, 'HLR_REENTRY_MULT_W', 3.0)))
        except Exception:
            pass
    try:
        need = int(float(getattr(cfg, 'HLR_TOP_MIN_TFS', 2)))
    except Exception:
        need = 2
    htfs_ok = any(t in ('4h', 'D', 'W') for t in tfs)
    return mult, bool(htfs_ok and len(tfs) >= need), len(tfs)


def apply_reentry_floor(record, qty0, px, ts_now, cfg):
    """record = (ts_exit, prev_qty, mult) or None. Returns (qty, record_or_None)."""
    if record is None:
        return qty0, None
    try:
        ts_exit, prev_qty, mult = record
        max_age = float(getattr(cfg, 'HLR_REENTRY_MAX_AGE_S', 14400.0))
    except Exception:
        return qty0, None
    if not (prev_qty > 0 and px > 0) or not ((ts_now - float(ts_exit)) < max_age):
        return qty0, record  # live lets expired records rot (never matches, never deleted)
    try:
        start = float(getattr(cfg, 'START_POSITION_SIZE', 500.0))
    except Exception:
        start = 500.0
    try:
        szmax = float(getattr(cfg, 'HLR_SZ_MAX', 10.0))
    except Exception:
        szmax = 10.0
    target = float(prev_qty) * float(mult)
    cap = start * szmax / px
    return min(max(float(qty0), target), cap), None  # one-shot, like live `del`
