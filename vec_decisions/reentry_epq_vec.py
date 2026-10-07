"""REENTRY_EPQ — vector twin of the crypto reentry evaluators in ez_positions_quick.py (Agent N3, queue n3/4), 15m+ parts only.

LIVE SOURCES (ez_positions_quick.py):
  * process_single_reentry_evaluation_epq (17030-17300): size tiers on reentry_amount (DIP/BREAKOUT/EXTENDED, 17109-17119), DC BREAKOUT FAST-PATH reentry
    (REENTRY2_DC_BREAK_*: dc_high_1h / dc_high_15m breakout by 0.1%, K/WT filter on REENTRY2_DC_BREAK_FILTER_TF, gated by LEGACY_DC_BREAKOUT_REENTRY), 
    everything below the `MANDATORY_PRICE_CROSS_EPQ_ENABLED` early return (config default False => LEGACY_*/GUARANTEED_*/QUICK_RECOVERY/FULL_DC blocks are UNREACHABLE live).
  * evaluate_reentry_epq (16862-17120): guards MIN_GAP / SYMGATE / RALLY_K15M, blocks B15/B04/B11/B02/B12/B14/B10 (already vector entry blocks), B16 SMA200 PULLBACK
    (sma_200_1h proximity, 1h WT, k_1h/k_4h, size x3.0 strong / x1.5 weak by wt_velocity_1h).
  * MANDATORY_PRICE_CROSS_EPQ (17265+): when the master is ON: price crossed exit => forced reentry x0.55 (recent/extended) or x1.0, vetoed when 15m+1h+4h ALL against
    (PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED; the bar-turn / HA bypass legs need 3m => INERT, HA 1h/15m bypass is wired).
3m legs (dc_high_3m cross, K/WT filter on 3m TF) are INERT: a TF with no data passes the filter exactly like live (`_dc_kdata_re` False => True).
Master REENTRY_EPQ_MODEL_ENABLED (default False => baseline unchanged); also replaces the blanket fire (like the other N3 models).
"""
from __future__ import annotations

import numpy as np


class Prepared:
    def __init__(self, npz, n, is_long, cfg, safe):
        g = lambda k, d=0.0: safe(npz, k, n, d)  # noqa: E731
        self.n, self.is_long = n, is_long
        self.dch1, self.dcl1 = g('dc_high_1h'), g('dc_low_1h')
        self.dch15, self.dcl15 = g('dc_high_15m'), g('dc_low_15m')
        self.k15, self.d15 = g('k_15m', 50.0), g('d_15m', 50.0)
        self.k1h, self.k4h = g('k_1h', 50.0), g('k_4h', 50.0)
        self.w1 = {tf: g(f'wt1_{tf}') for tf in ('15m', '1h', '4h')}
        self.w2 = {tf: g(f'wt2_{tf}') for tf in ('15m', '1h', '4h')}
        self.vel1h = g('wt_velocity_1h')
        self.ha = {tf: g(f'ha_{tf}') for tf in ('15m', '1h', '4h')}
        self.sma1h = g('sma_200_1h')
        ftf = str(getattr(cfg, 'REENTRY2_DC_BREAK_FILTER_TF', '3m') or '3m')
        self.fk, self.fd = g(f'k_{ftf}'), g(f'd_{ftf}')
        self.fw1, self.fw2 = g(f'wt1_{ftf}'), g(f'wt2_{ftf}')


def rally_k15_blocked(P, i, cfg, fresh_cross=False):
    cap = float(getattr(cfg, 'REENTRY_RALLY_K15M_MAX', 100.0) or 100.0)
    if cap >= 100.0 or fresh_cross:
        return False
    k = float(P.k15[i])
    return (k >= cap) if P.is_long else (k <= 100.0 - cap)


def size_tier_mult(P, i, px, exit_px, cfg):
    """live 17109-17119: EXTENDED (k_1h beyond threshold) | DIP (price below exit long / above short) | BREAKOUT."""
    L = P.is_long
    thr = float(getattr(cfg, 'REENTRY_SIZE_EXTENDED_K1H', 90.0))
    k = float(P.k1h[i])
    ext = (L and k > thr) or ((not L) and k < (100.0 - thr))
    dip = exit_px > 0 and ((L and px < exit_px) or ((not L) and px > exit_px))
    if ext:
        return float(getattr(cfg, 'REENTRY_SIZE_EXTENDED_MULT', 1.0))
    if dip:
        return float(getattr(cfg, 'REENTRY_SIZE_DIP_MULT', 2.0))
    return float(getattr(cfg, 'REENTRY_SIZE_BREAKOUT_MULT', 1.5))


def dc_breakout(P, i, px, cfg):
    """-> bool: REENTRY2 DC BREAKOUT fast-path (1h always, 15m if ALLOW_15M); 3m leg inert."""
    if not bool(getattr(cfg, 'LEGACY_DC_BREAKOUT_REENTRY', True)):
        return False
    L = P.is_long
    buf = 0.001
    allow15 = bool(getattr(cfg, 'REENTRY2_DC_BREAK_ALLOW_15M', True))
    req_k = bool(getattr(cfg, 'REENTRY2_DC_BREAK_REQUIRE_K_FILTER', True))
    req_wt = bool(getattr(cfg, 'REENTRY2_DC_BREAK_REQUIRE_WT_FILTER', False))
    fk, fd, fw1, fw2 = float(P.fk[i]), float(P.fd[i]), float(P.fw1[i]), float(P.fw2[i])
    kdata, wtdata = (abs(fk) > 1e-9 or abs(fd) > 1e-9), (abs(fw1) > 1e-9 or abs(fw2) > 1e-9)
    if L:
        brk = (P.dch1[i] > 0 and px > P.dch1[i] * (1 + buf)) or (allow15 and P.dch15[i] > 0 and px > P.dch15[i] * (1 + buf))
        kok = (fk > fd) if (req_k and kdata) else True
        wok = (fw1 > fw2) if (req_wt and wtdata) else True
    else:
        brk = (P.dcl1[i] > 0 and px < P.dcl1[i] * (1 - buf)) or (allow15 and P.dcl15[i] > 0 and px < P.dcl15[i] * (1 - buf))
        kok = (fk < fd) if (req_k and kdata) else True
        wok = (fw1 < fw2) if (req_wt and wtdata) else True
    return bool(brk and kok and wok)


def b16_sma200_pullback(P, i, px, cfg):
    """-> (fires, size_mult)."""
    if not bool(getattr(cfg, 'REENTRY_B16_SMA200_PULLBACK_ENABLED', True)):
        return False, 1.0
    sma = float(P.sma1h[i])
    if not (sma > 0 and px > 0):
        return False, 1.0
    if abs(px - sma) / sma > float(getattr(cfg, 'REENTRY_B16_SMA200_PROX_PCT', 0.005)):
        return False, 1.0
    L = P.is_long
    w1, w2 = float(P.w1['1h'][i]), float(P.w2['1h'][i])
    k1, k4 = float(P.k1h[i]), float(P.k4h[i])
    wt_ok = (w1 > w2) if L else (w1 < w2)
    k_ok = (k1 > 35 and k4 > 35) if L else (k1 < 65 and k4 < 65)
    if not (wt_ok and k_ok):
        return False, 1.0
    v = float(P.vel1h[i])
    strong = (L and v > 1.0) or ((not L) and v < -1.0)
    return True, float(getattr(cfg, 'REENTRY_B16_SIZE_MULT_STRONG', 3.0)) if strong else float(getattr(cfg, 'REENTRY_B16_SIZE_MULT_WEAK', 1.5))


def mandatory_price_cross(P, i, px, exit_px, mins_since_exit, cfg):
    """-> (fires, mult). Needs MANDATORY_PRICE_CROSS_EPQ_ENABLED (live default False)."""
    if not bool(getattr(cfg, 'MANDATORY_PRICE_CROSS_EPQ_ENABLED', False)) or exit_px <= 0:
        return False, 1.0
    L = P.is_long
    if not ((L and px >= exit_px) or ((not L) and px <= exit_px)):
        return False, 1.0
    if bool(getattr(cfg, 'PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED', True)):
        def _bull(tf, kk): return (kk > 50 and P.ha[tf][i] == 1 and P.w1[tf][i] > P.w2[tf][i])
        def _bear(tf, kk): return (kk < 50 and P.ha[tf][i] == -1 and P.w1[tf][i] < P.w2[tf][i])
        b15 = float(P.k15[i]) > 55 and P.ha['15m'][i] == 1 and P.w1['15m'][i] > P.w2['15m'][i]
        r15 = float(P.k15[i]) < 45 and P.ha['15m'][i] == -1 and P.w1['15m'][i] < P.w2['15m'][i]
        against_short = (not L) and b15 and _bull('1h', float(P.k1h[i])) and _bull('4h', float(P.k4h[i]))
        against_long = L and r15 and _bear('1h', float(P.k1h[i])) and _bear('4h', float(P.k4h[i]))
        if against_short or against_long:
            ha_bypass = bool(getattr(cfg, 'PRICE_CROSSED_HTF_AGAINST_VETO_HA_BYPASS', True))
            ha_lift = (P.ha['1h'][i] == (1 if L else -1)) or (P.ha['15m'][i] == (1 if L else -1))
            if not (ha_bypass and ha_lift):
                return False, 1.0
    mult = 0.55 if (mins_since_exit < 60 or float(P.k15[i]) > 70 or float(P.k1h[i]) > 70) else 1.0
    return True, mult
