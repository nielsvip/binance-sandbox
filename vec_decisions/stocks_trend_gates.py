"""N5 006 (REVIVED per coordinator D8 2026-10-01; live twin = Agent D hook at queue_trade_action fresh-OPEN choke point, dead evaluate_open stays dead). DEFAULT OFF everywhere (each gate is its own swept switch; effective ON only when its flag is True).
Vector twin of the stock OPEN trend gates (originally in dead evaluate_open / _apply_research_only_live_gates is_entry branch) on the final entry signal (tradier_manage.TradeManager.evaluate_open ~L20591-20800 and
_apply_research_only_live_gates is_entry branch ~L15097-15215). Live applies them to every fresh stock open; the vector only had them inside the OR'd base entry (inert).
Gates (live defaults from config_tradier.py unless noted), all on 15m+ arrays only:
  WT_D_EXHAUST (evaluate_open L20778-20791; UNCONDITIONAL in live, thresholds hardcoded +-60): long blocked when wt1_D>T and wt1_4h>T; short when wt1_D<-T and wt1_4h<-T.
      New sweepable switches WT_D_EXHAUST_GATE_ENABLED (live default True) and WT_D_EXHAUST_THRESHOLD (60) — live needs the config reads (needs_live.csv).
  HTF_ALIGNMENT_ENABLED (+HTF_MIN_ALIGNED, TF_HTF1, TF_HTF3, D_TREND_REQUIRED): htf_cnt of wt1>wt2 on 1h, TF_HTF1, TF_HTF3 >= min; D trend: block long when ha_D==-1, short when ha_D==1.
  STRENGTH_FILTER_ENABLED (+STRENGTH_MIN_SCORE): block when |wt1_1h-wt2_1h| < 0.8*min_score.
  CT_CHOP_4H_GATE_ENABLED: block when choppiness_4h >= CHOP_RANGING_THRESHOLD or > CHOP_TRENDING_THRESHOLD (live quirk: effectively > trending thr).
  CT_15M_MOMENTUM_GATE_ENABLED: wt_momentum_state_15m: long blocked when <=0, short when >=0.
  CT_VOLUME_SURGE_GATE_ENABLED (+CT_REL_VOL_MIN): relative_volume_1h < min blocks.
  CLENOW_ENABLED: close vs sma_200_D (live reads indicators 'sma200_D'); CLENOW_GATE_ENABLED (+CLENOW_GATE_MIN_SCORE): clenow_score < min blocks.
CONFLUENCE_MODE_ENABLED uses 3m/5m indicators -> ignored (no 3m/5m data in the backtest). Returns an allow-mask (True = open allowed) or None when nothing is enabled."""
import numpy as np


def _g(cfg, k, d):
    return getattr(cfg, k, d)


def pass_mask(npz, n, is_long, cfg, close, safe):
    ok = np.ones(n, dtype=bool)
    used = False
    if bool(_g(cfg, 'WT_D_EXHAUST_GATE_ENABLED', False)):
        t = float(_g(cfg, 'WT_D_EXHAUST_THRESHOLD', 60.0))
        w1d, w14 = safe(npz, 'wt1_D', n, 0.0), safe(npz, 'wt1_4h', n, 0.0)
        ex = ((w1d > t) & (w14 > t)) if is_long else ((w1d < -t) & (w14 < -t))
        ok &= ~ex; used = True
    if bool(_g(cfg, 'HTF_ALIGNMENT_ENABLED', True)):
        mn = int(float(_g(cfg, 'HTF_MIN_ALIGNED', 1))); h1 = str(_g(cfg, 'TF_HTF1', '1h')); h3 = str(_g(cfg, 'TF_HTF3', 'D'))
        pairs = [(safe(npz, 'wt1_1h', n, 0.0), safe(npz, 'wt2_1h', n, 0.0)), (safe(npz, f'wt1_{h1}', n, 0.0), safe(npz, f'wt2_{h1}', n, 0.0)), (safe(npz, f'wt1_{h3}', n, 0.0), safe(npz, f'wt2_{h3}', n, 0.0))]
        cnt = sum(((a > b) if is_long else (a < b)).astype(np.int8) for a, b in pairs)
        ok &= cnt >= mn
        if bool(_g(cfg, 'D_TREND_REQUIRED', True)):
            ha = safe(npz, 'ha_D', n, 0.0)
            ok &= ~((ha == -1) if is_long else (ha == 1))
        used = True
    if bool(_g(cfg, 'STRENGTH_FILTER_ENABLED', True)):
        mn = float(_g(cfg, 'STRENGTH_MIN_SCORE', 5.0))
        gap = np.abs(safe(npz, 'wt1_1h', n, 0.0) - safe(npz, 'wt2_1h', n, 0.0))
        ok &= ~(gap < mn * 0.8); used = True
    if bool(_g(cfg, 'CT_CHOP_4H_GATE_ENABLED', False)):
        ch = safe(npz, 'choppiness_4h', n, 50.0)
        ok &= ~((ch >= float(_g(cfg, 'CHOP_RANGING_THRESHOLD', 61.8))) | (ch > float(_g(cfg, 'CHOP_TRENDING_THRESHOLD', 38.2)))); used = True
    if bool(_g(cfg, 'CT_15M_MOMENTUM_GATE_ENABLED', False)):
        m = safe(npz, 'wt_momentum_state_15m', n, 0.0)
        ok &= ~((m <= 0) if is_long else (m >= 0)); used = True
    if bool(_g(cfg, 'CT_VOLUME_SURGE_GATE_ENABLED', False)):
        ok &= ~(safe(npz, 'relative_volume_1h', n, 1.0) < float(_g(cfg, 'CT_REL_VOL_MIN', 1.3))); used = True
    if bool(_g(cfg, 'CLENOW_ENABLED', False)):
        sma = safe(npz, 'sma_200_D', n, 0.0)
        ok &= ~((sma > 0) & (((close < sma) if is_long else (close > sma)))); used = True
    if bool(_g(cfg, 'CLENOW_GATE_ENABLED', False)):
        ok &= ~(safe(npz, 'clenow_score', n, 0.0) < float(_g(cfg, 'CLENOW_GATE_MIN_SCORE', 30.0))); used = True
    return ok if used else None
