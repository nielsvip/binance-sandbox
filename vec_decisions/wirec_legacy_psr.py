"""WIRING LANE C — vec twins of LEGACY PSR reentry pathways (batch L1e, 5 switches).

LIVE SOURCE (read-only reference): ez_manage.py::process_single_reentry_evaluation
  QUICK_RECOVERY (:42806-42851): last_red set + atr>0 + REENTRY2_QUICK_RECOVERY_ENABLED(True)
    + min_since < QUICK_RECOVERY_WINDOW_MIN(120); long px>last_red+atr_3m & k_3m>d_3m
    (short mirrored); knob LEGACY_REENTRY_PSR_QUICK_RECOVERY (live True).
  K_DC_CROSSOVER long (:43065-43100) / short (:43162-43200): (3m|15m K-over-DC trigger)
    + k15 R d15 + k3m R d3m + k15<70 & k3m<70 (long; short: >30) + not invalidated
    + LEGACY_PROC_SINGLE_REENTRY (live False) + LEGACY_REENTRY_PSR_K_DC_CROSSOVER
    (live False) + check_reentry_delta_tolerant.
  FULL_DC (:43217-43237): (stoch_cross_3m & beyond dc_basis_15m) | dc_basis_crossover_3m,
    not invalidated, notional<START (vacuous at entry: pos None -> 0), knob (live True).
  DC_BOUNCE (:43246-43358): last_red set + DC>0 + hours<8 + near-2% + (bounce ±0.2% |
    cross basis_15m) + trend (long dc_hi_1h rising / short dc_lo_1h falling), knob
    (live True). Size x1.5 when dc_low_15m rising (both sides, :43338).
  Invalidation machine (:43026-43060 long / :43118-43154 short): below-DC sets True,
    trigger revalidates (short additionally requires k15<d15 & k15>30 on revalidate).

TWIN (loop-called, N3 obligatory-style): Prepared caches arrays; fires_all() tries the
four pathways in live order with (exit_px, min_since_exit) from the sim loop and carries
the invalidation flag across bars. All five knobs default False in vec.
APPROXIMATIONS (documented): (1) NO 3M DATA — every 3m term uses its 15m proxy
(atr_15m/k_15m/d_15m/dc_*_15m/basis_15m cross; K3M_FLOOR precedent); the 3m stoch halves
of K_DC collapse onto the 15m halves. (2) Delta gate: pass-through at default
(DELTA_REENTRY_FILTER_ENABLED False); when ON, 4h-WT-alignment part only (live
delta_tracker unmodelable in vec). (3) QR/loop-amount size caps need the loop amount —
twinned as mult 1.0 (DCB keeps the 1.5 boost). (4) Invalidation updates on evaluated
(flat) bars only. (5) Short-side K_DC trigger defs mirrored from long per :43105-43160.
DIVERGENCES: QR/FULL_DC/DC_BOUNCE live True, vec False until promotion.
BIBLE: §43 parity gate; §17 (divergences noted); §18 honest-0.
"""
from __future__ import annotations

import numpy as np


def _col(npz, key, n, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _prev(a):
    if len(a) == 0:
        return a
    return np.concatenate(([a[0]], a[:-1]))


class Prepared:
    def __init__(self, npz, n, is_long, cfg, safe=None):
        self.n = n
        self.is_long = is_long
        g = (lambda k, d=0.0: safe(npz, k, n, d)) if safe is not None else (lambda k, d=0.0: _col(npz, k, n, d))
        self.k = g('k_15m', 50.0)
        self.d = g('d_15m', 50.0)
        self.kp = _prev(self.k)
        self.dp = _prev(self.d)
        self.px = g('close_15m', 0.0)
        self.lo15 = g('dc_low_15m', 0.0)
        self.hi15 = g('dc_high_15m', 0.0)
        self.lo1h = g('dc_low_1h', 0.0)
        self.hi1h = g('dc_high_1h', 0.0)
        self.lo15p = _prev(self.lo15)
        self.hi15p = _prev(self.hi15)
        self.lo1hp = _prev(self.lo1h)
        self.hi1hp = _prev(self.hi1h)
        self.basis15 = g('dc_basis_15m', 0.0)
        self.atr = g('atr_15m', 0.0)
        self.w1_4h = g('wt1_4h', 50.0)
        self.w2_4h = g('wt2_4h', 50.0)


def _stoch_cross(P, i):
    k, d, kp, dp = (float(P.k[i]), float(P.d[i]), float(P.kp[i]), float(P.dp[i]))
    if P.is_long:
        return (k >= d) and (kp < dp)
    return (k <= d) and (kp > dp)


def _kdc_trigger(P, i, px):
    """15m K-over/under-DC trigger (3m half dropped — no 3m data)."""
    if P.is_long:
        return bool(_stoch_cross(P, i) and float(P.lo15[i]) > 0 and px > float(P.lo15[i]))
    return bool(_stoch_cross(P, i) and float(P.hi15[i]) > 0 and px < float(P.hi15[i]))


def _below_dc(P, i, px):
    if P.is_long:
        return bool(float(P.lo15[i]) > 0 and px <= float(P.lo15[i]))
    return bool(float(P.hi15[i]) > 0 and px >= float(P.hi15[i]))


def _delta_ok(P, i, cfg):
    if not bool(getattr(cfg, 'DELTA_REENTRY_FILTER_ENABLED', False)):
        return True
    a, b = float(P.w1_4h[i]), float(P.w2_4h[i])
    return (a > b) if P.is_long else (a < b)


def fires_qr(P, i, px, last_red_px, min_since, cfg):
    """(ok, mult). Needs exit context from the loop."""
    if not bool(getattr(cfg, 'LEGACY_REENTRY_PSR_QUICK_RECOVERY', False)):
        return False, 1.0
    if not bool(getattr(cfg, 'REENTRY2_QUICK_RECOVERY_ENABLED', True)):
        return False, 1.0
    try:
        win = float(getattr(cfg, 'QUICK_RECOVERY_WINDOW_MIN', 120.0))
    except Exception:
        win = 120.0
    if not (last_red_px > 0) or not (min_since < win):
        return False, 1.0
    atr = float(P.atr[i])
    if not (atr > 0):
        return False, 1.0
    k, d = float(P.k[i]), float(P.d[i])
    if P.is_long:
        ok = (px > last_red_px + atr) and (k > d)
    else:
        ok = (px < last_red_px - atr) and (k < d)
    return bool(ok), 1.0


def fires_kdc(P, i, px, inv_in, cfg):
    """(ok, mult, inv_out). inv uses PRE-update state for fire (live :43002 vs :43065)."""
    trig = _kdc_trigger(P, i, px)
    below = _below_dc(P, i, px)
    if below:
        inv_out = True
    else:
        inv_out = bool(inv_in)
    if trig and inv_in:
        if P.is_long:
            inv_out = False
        else:
            k, d = float(P.k[i]), float(P.d[i])
            if (k < d) and (k > 30):  # short revalidate extras (:43142-43149)
                inv_out = False
    if not (bool(getattr(cfg, 'LEGACY_PROC_SINGLE_REENTRY', False))
            and bool(getattr(cfg, 'LEGACY_REENTRY_PSR_K_DC_CROSSOVER', False))):
        return False, 1.0, inv_out
    if inv_in:
        return False, 1.0, inv_out
    if not trig:
        return False, 1.0, inv_out
    k, d = float(P.k[i]), float(P.d[i])
    if P.is_long:
        if not ((k >= d) and (k < 70)):
            return False, 1.0, inv_out
    else:
        if not ((k <= d) and (k > 30)):
            return False, 1.0, inv_out
    if not _delta_ok(P, i, cfg):
        return False, 1.0, inv_out
    return True, 1.0, inv_out


def fires_fulldc(P, i, inv_in, cfg):
    """(ok, mult). Notional gate vacuous at entry (pos None -> 0 < START)."""
    if not bool(getattr(cfg, 'LEGACY_REENTRY_PSR_FULL_DC', False)):
        return False, 1.0
    if inv_in:
        return False, 1.0
    px = float(P.px[i])
    b = float(P.basis15[i])
    beyond = (b > 0) and ((px > b) if P.is_long else (px < b))
    cond1 = bool(_stoch_cross(P, i) and beyond)
    # dc_basis_crossover_3m proxy: price crosses the 15m basis favourably
    pxp = float(P.px[i - 1]) if i > 0 else px
    if P.is_long:
        cond2 = bool(b > 0 and pxp <= b and px > b)
    else:
        cond2 = bool(b > 0 and pxp >= b and px < b)
    return bool(cond1 or cond2), 1.0


def _within2(a, b):
    return (b > 0) and (abs(a - b) / b < 0.02)


def fires_dcbounce(P, i, px, last_red_px, hours_since, cfg):
    """(ok, mult). Full DC_BOUNCE predicate with exit context."""
    if not bool(getattr(cfg, 'LEGACY_REENTRY_PSR_DC_BOUNCE', False)):
        return False, 1.0
    if not (last_red_px > 0):
        return False, 1.0
    lo15, hi15 = float(P.lo15[i]), float(P.hi15[i])
    lo1h, hi1h = float(P.lo1h[i]), float(P.hi1h[i])
    if P.is_long:
        if not (hi15 > 0 and hi1h > 0):
            return False, 1.0
    else:
        if not (lo15 > 0 and lo1h > 0):
            return False, 1.0
    if not (hours_since < 8.0):
        return False, 1.0
    if P.is_long:
        near = _within2(last_red_px, hi15) or _within2(last_red_px, hi1h)
    else:
        # live :43262-43272 precedence: A if C1 else (B if C2 else False)
        a = _within2(last_red_px, lo15)
        b = _within2(last_red_px, lo1h)
        near = a if lo15 > 0 else (b if lo1h > 0 else False)
    if not near:
        return False, 1.0
    if P.is_long:
        b_lo1h = (lo1h > 0) and (lo1h * 0.998 <= px <= lo1h * 1.002)
        b_lo15 = (lo15 > 0) and (lo15 * 0.998 <= px <= lo15 * 1.002)
        bounce = b_lo1h or b_lo15
    else:
        b_hi1h = (hi1h > 0) and (hi1h * 0.998 <= px <= hi1h * 1.002)
        b_hi15 = (hi15 > 0) and (hi15 * 0.998 <= px <= hi15 * 1.002)
        bounce = b_hi1h or b_hi15
    b15 = float(P.basis15[i])
    cross_basis = (b15 > 0) and ((px >= b15) if P.is_long else (px <= b15))
    if not (bounce or cross_basis):
        return False, 1.0
    if P.is_long:
        trend_ok = hi1h > float(P.hi1hp[i])
    else:
        trend_ok = lo1h < float(P.lo1hp[i])
    if not trend_ok:
        return False, 1.0
    mult = 1.5 if (lo15 > float(P.lo15p[i])) else 1.0
    return True, mult


def fires_all(P, i, px, exit_px, min_since, inv_in, cfg):
    """Live order QR -> KDC -> FULLDC -> DCB. Returns (fired, mult, inv_out)."""
    ok, m = fires_qr(P, i, px, exit_px, min_since, cfg)
    if ok:
        return True, m, bool(inv_in)
    ok, m, inv_out = fires_kdc(P, i, px, inv_in, cfg)
    if ok:
        return True, m, inv_out
    ok, m = fires_fulldc(P, i, inv_in, cfg)
    if ok:
        return True, m, inv_out
    ok, m = fires_dcbounce(P, i, px, exit_px, min_since / 60.0, cfg)
    if ok:
        return True, m, inv_out
    return False, 1.0, bool(inv_out)
