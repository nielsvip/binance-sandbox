"""OBLIGATORY_REENTRY vector twin (Agent N3, queue n3/3) — crypto, 15m+ parts only.

LIVE SOURCE: ez_reentry.py:evaluate_obligatory_reentry (lines 253-385), called from ez_positions_quick.py:3296/3354 (MANDATORY_REENTRY block: ok => _tier1_forced reentry)
and :16747 (GUARANTEED_REENTRY: not ok => reentry refused). Tiers:
  T1  SMA bounce (price crossed ema_50_15m favourably vs previous close) AND >= OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED aligned TFs   -> size OBLIGATORY_REENTRY_SMA_BOUNCE_SIZE_MULT (1.5)
  T2  3m alignment + HTF  (needs 3m)               -> INERT here (user: no 3m/1m/5m in the backtest)
  T3  exit-price cross + dc_high4_3m break (3m)    -> INERT
  bounce-after-correction family (live reads via vec_decisions/*.py — SHARED predicates): 15m-only members are wired here with the SAME pure predicates:
      REENTRY_15M_DC_BASIS_CROSS_HTF, REENTRY_15M_LRL_PULLBACK_HTF, REENTRY_15M_BB1H_LOW_BOUNCE_HTF  (A/B/C/D/E members need 3m bars/WT: INERT)
  K_15m extreme => size x OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC (long k>BLOCK) / SHORT_K15_LOW_SIZE_FRAC (short k<BLOCK)   [size REDUCTION, never a block]
Aligned-TF count: live counts (3m,15m,1h,4h,D) 'wt_bullish_*'; here (15m,1h,4h,D) = the 3m member is missing (conservative: needs the same threshold from fewer TFs).
Master switch REENTRY_OBLIGATORY_MODEL_ENABLED (default False = baseline unchanged). When ON it adds this fire pathway and (like the tier model) replaces the blanket fire.
"""
from __future__ import annotations

import numpy as np

from vec_decisions import reentry_15m_bb_htf as _B

_TFS = ('15m', '1h', '4h', 'D')


class Prepared:
    def __init__(self, npz, n, is_long, cfg, safe):
        g = lambda k, d=0.0: safe(npz, k, n, d)  # noqa: E731
        sh = lambda a: np.concatenate(([a[0]], a[:-1])) if len(a) else a  # noqa: E731
        self.n = n
        self.is_long = is_long
        self.wt1 = {tf: g(f'wt1_{tf}', 50.0) for tf in _TFS}
        self.wt2 = {tf: g(f'wt2_{tf}', 50.0) for tf in _TFS}
        cnt = np.zeros(n, dtype=np.int16)
        for tf in _TFS:
            a, b = self.wt1[tf], self.wt2[tf]
            neutral = (np.abs(a - 50) < 1e-9) & (np.abs(b - 50) < 1e-9)
            cnt += (((a > b) if is_long else (a < b)) & ~neutral).astype(np.int16)
        self.htf = cnt                                            # aligned-TF count over 15m,1h,4h,D (same neutral rule as live gr count)
        f, t = str(getattr(cfg, 'OBLIGATORY_REENTRY_SMA_FIELD', 'ema_50')), str(getattr(cfg, 'OBLIGATORY_REENTRY_SMA_TF', '15m'))
        self.sma = g(f'{f}_{t}')
        self.sma_prev = sh(self.sma)
        self.close15 = g('close_15m')
        self.close15_prev = sh(self.close15)
        self.k15 = g('stoch_k_15m', 50.0)
        self.bbm = g('bb_middle_15m', 0.0)
        self.bbm_prev = sh(self.bbm)
        self.bbp1h = g('bb_pct_b_1h', 0.5)
        self.bbp1h_prev = sh(self.bbp1h)
        self.bbl1h = g('bb_lower_1h')
        self.lrl = g('lrL_pct_b_15m', 0.5)
        self.lrl_prev = sh(self.lrl)
        self.w1_15, self.w2_15 = self.wt1['15m'], self.wt2['15m']


def fires(P: Prepared, i: int, price: float, exit_price: float, prev_close: float, cfg):
    """-> (ok, size_mult, reason, score). Score twins ez_reentry SCORE_TIER1/2 (T1 bounce
    -> TIER1, 15m bounce-family -> TIER2; T2/T3 need 3m -> inert). WIRING LANE C L1f."""
    if not bool(getattr(cfg, 'OBLIGATORY_REENTRY_ENABLED', True)):
        return False, 0.0, '', 0
    L = P.is_long
    if not bool(getattr(cfg, 'OBLIGATORY_REENTRY_LONG_ENABLED' if L else 'OBLIGATORY_REENTRY_SHORT_ENABLED', True)):
        return False, 0.0, '', 0
    sma, smap = float(P.sma[i]), float(P.sma_prev[i])
    if L:
        sma_bounce = sma > 0 and price > sma and (prev_close <= 0 or prev_close < smap)
    else:
        sma_bounce = sma > 0 and price < sma and (prev_close <= 0 or prev_close > smap)
    htf = int(P.htf[i])
    t1 = int(getattr(cfg, 'OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED', 3))
    bounce_mult = float(getattr(cfg, 'OBLIGATORY_REENTRY_SMA_BOUNCE_SIZE_MULT' if L else 'OBLIGATORY_REENTRY_SHORT_SMA_BOUNCE_SIZE_MULT', 1.5))
    default_mult = float(getattr(cfg, 'OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT', 1.0))
    size_mult = None
    reason = ''
    score = 0
    if sma_bounce and htf >= t1:
        size_mult, reason = bounce_mult, 'OBL_T1_SMA_BOUNCE'
        try:
            score = int(getattr(cfg, 'OBLIGATORY_REENTRY_SCORE_TIER1', 40))
        except Exception:
            score = 40
    else:
        bp = float(getattr(cfg, 'REENTRY_15M_BETTER_PCT', 0.002))
        if bool(getattr(cfg, 'REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED', False)) and _B._dc_basis_cross_htf_fires(
                float(P.close15[i]), float(P.close15_prev[i]), float(P.bbm[i]), float(P.bbm_prev[i]), htf, float(P.bbp1h[i]), price, exit_price, L,
                int(getattr(cfg, 'REENTRY_15M_DC_BASIS_CROSS_HTF_MIN_TFS', 2)), bp):
            size_mult, reason = default_mult, 'OBL_15M_BB_BASIS_CROSS_HTF'
        elif bool(getattr(cfg, 'REENTRY_15M_LRL_PULLBACK_HTF_ENABLED', False)) and _B._lrl_pullback_htf_fires(
                float(P.lrl_prev[i]), float(P.lrl[i]), float(P.w1_15[i]), float(P.w2_15[i]), htf, price, exit_price, L,
                int(getattr(cfg, 'REENTRY_15M_LRL_PULLBACK_HTF_MIN_TFS', 2)), bp):
            size_mult, reason = default_mult, 'OBL_15M_LRL_PULLBACK_HTF'
        elif bool(getattr(cfg, 'REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED', False)) and _B._bb1h_low_bounce_htf_fires(
                float(P.bbp1h[i]), float(P.bbp1h_prev[i]), float(P.close15[i]), float(P.bbl1h[i]), float(P.w1_15[i]), float(P.w2_15[i]), htf, price, exit_price, L,
                int(getattr(cfg, 'REENTRY_15M_BB1H_LOW_BOUNCE_HTF_MIN_TFS', 2)), bp):
            size_mult, reason = default_mult, 'OBL_15M_BB1H_LOW_BOUNCE_HTF'
    if size_mult is not None and score == 0 and reason.startswith('OBL_15M'):
        try:
            score = int(getattr(cfg, 'OBLIGATORY_REENTRY_SCORE_TIER2', 30))
        except Exception:
            score = 30
    if size_mult is None:
        return False, 0.0, '', 0
    k = float(P.k15[i])
    if L:
        if k > float(getattr(cfg, 'OBLIGATORY_REENTRY_K15_HIGH_BLOCK', 95.0)):
            size_mult *= float(getattr(cfg, 'OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC', 0.5))
    else:
        if k < float(getattr(cfg, 'OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK', 5.0)):
            size_mult *= float(getattr(cfg, 'OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC', 0.5))
    return True, size_mult, reason, score
