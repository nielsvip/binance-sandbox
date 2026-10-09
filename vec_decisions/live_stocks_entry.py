"""LIVE_STOCKS_ENTRY — vector twin of the fresh-OPEN source stack of tradier_manage.process_position (Agent C2, queue item 003).

Live opens a flat stock key through ONE of these sources (first hit wins), each with its own gate; the vec engine used a permissive OR of the legacy
B02..B15/B_PULL*/B_KG blocks with the live gates ANDed inside _base_entry (inert), so WT_DC_HTF_GATE / WT_DC_HTF_GATE_MODE / BB_PULLBACK_GATE_* never moved a trade.
This builds the signal from the live sources only (15m+ computable):
  BB4H_BREAKOUT_LADDER stage 0 (tradier_manage.py:11938; long only, price <= STOCK_MAX_NOTIONAL_USD, first bar of a bb_pct_b_4h <=1 -> >1 cross; goes straight to
    queue_trade_action, NOT through the veto stack -> no extra_ok),
  WT_DC (12825; blocks['B_WT_DC_LIVE'] already carries its own HTF/BB-pullback/align/dc_pos gates when the stack is on),
  RSI2 13126 / CONNORS 13138 / STOCH 13084 / WT_ENTRY 13113 / K_ZONE 13099 / BOUNCE deep-turn 13040, donchian 13062 / SATOSHIT 12480 / BB_PCTB (blocks present only
    when their switch is enabled) , RZ_BREAKOUT 12884, LR_PCTB_D 12894, WT_TOP_ENTRY shared-direct claim 1385 — all through the veto stack (extra_ok).
Excluded on purpose: legacy B02..B15, B_PULL*, B_REENTRY2, B_BBSQUEEZE*, kindergarten OR (no live fresh-OPEN consumer), STRUCTURE_FLIP (a reentry: needs a prior exit),
GR_HTF_DIRECT (dead at defaults: score_min 23 > reachable n_tfs*2), 3m/5m-dependent legs (inert by rule).
"""
from __future__ import annotations

import numpy as np

SOURCE_BLOCKS = (
    'B_BOUNCE_DEEP_TURN', 'B_BOUNCE_DONCHIAN', 'B_STOCH_ENTRY', 'B_WT_ENTRY', 'B_RSI2_ENTRY', 'B_CONNORS_ENTRY',
    'B_KZONE_TRADIER', 'B_SATOSHIT_ENTRY', 'B_BBPCTB', 'B_WT_TOP',
)


def _arr(npz, key, n, default=0.0):
    if key in npz:
        a = np.asarray(npz[key], dtype=float)
        if len(a) >= n:
            return a[:n]
    return np.full(n, default, dtype=float)


def bb4h_stage0(npz, n, cfg, is_long, close):
    if not is_long or not bool(getattr(cfg, 'BB4H_BREAKOUT_LADDER_ENABLED', True)):
        return np.zeros(n, dtype=bool)
    bb = _arr(npz, 'bb_pct_b_4h', n, 0.5)
    if 'bb_pct_b_4h_prev' in npz:
        prev = _arr(npz, 'bb_pct_b_4h_prev', n, 0.5)
    else:
        prev = np.concatenate(([0.5], bb[:-1]))
    cond = (prev <= 1.0) & (bb > 1.0)
    edge = cond & ~np.concatenate(([False], cond[:-1]))
    cap = float(getattr(cfg, 'BB4H_BREAKOUT_LADDER_STOCK_MAX_NOTIONAL_USD', 2000.0) or 0.0)
    return edge & (close > 0) & (close <= cap)


def _str_arr(npz, key, n):
    if key in npz:
        a = np.asarray(npz[key]).astype(str)
        if len(a) >= n:
            return a[:n]
    return np.full(n, '', dtype=str)


def live_veto(npz, n, is_long, cfg):
    """Twin of the post-entry veto stack tradier_manage.py:13212-13300 (ENTRY_SCORE is applied on the WT_DC score in q001; FILTER_TF vetoes are the
    generic_filter_tf entry masks downstream). The vec `extra_ok` is NOT this: it ANDs ~20 legacy invented gates (STOCH_CROSS_ENTRY_TRADIER, COMBINED_STOCH_GATE,
    RSI/MFI entry gates...) that live models as entry SOURCES or not at all."""
    ok = np.ones(n, dtype=bool)
    if bool(getattr(cfg, 'K_ZONE_VETO_ENABLED_TRADIER', False)):
        k4 = _arr(npz, 'stoch_k_4h', n, 50.0)
        if is_long:
            ok = ok & ~(k4 >= float(getattr(cfg, 'K_ZONE_LONG_THRESHOLD_TRADIER', 100) or 100))
        else:
            ok = ok & ~(k4 <= float(getattr(cfg, 'K_ZONE_SHORT_THRESHOLD_TRADIER', 0) or 0))
    if bool(getattr(cfg, 'MI_ENTRY_ENABLED_TRADIER', False)):
        trough = _str_arr(npz, 'wt_trough_structure_4h', n)
        peak = _str_arr(npz, 'wt_peak_structure_4h', n)
        mom = _str_arr(npz, 'wt_momentum_state_4h', n)
        div = _str_arr(npz, 'wt_divergence_4h', n)
        if is_long:
            mi = (trough == 'HL') | (mom == 'EXHAUST_DOWN') | (div == 'BULL')
        else:
            mi = (peak == 'LH') | (mom == 'EXHAUST_UP') | (div == 'BEAR')
        ok = ok & mi
    # tradier_manage.py:13335-13362 ENTRY_BOTTOM veto gates (fail-open)
    if bool(getattr(cfg, 'LONG_STOCH_CHASE_BLOCK', False)):
        k1 = _arr(npz, 'k_1h', n, 50.0) if 'k_1h' in npz else _arr(npz, 'stoch_k_1h', n, 50.0)
        ha = _arr(npz, 'ha_streak_1h', n, 0.0)
        ok = ok & ~((k1 > 70) & (ha > 2)) if is_long else ok & ~((k1 < 30) & (ha < -2))
    if bool(getattr(cfg, 'RSI_ENTRY_GATE_ENABLED', False)):
        r = _arr(npz, 'rsi_1h', n, 50.0)
        ok = ok & ~(r > float(getattr(cfg, 'RSI_ENTRY_MAX_LONG', 37.0))) if is_long else ok & ~(r < float(getattr(cfg, 'RSI_ENTRY_MIN_SHORT', 63.0)))
    if bool(getattr(cfg, 'DC_ENTRY_VETO_ENABLED_TRADIER', False)):
        th = float(getattr(cfg, 'DC_POSITION_ENTRY_THRESHOLD', 0.25) or 0.25)
        d1, d4 = _arr(npz, 'dc_position_1h', n, 0.5), _arr(npz, 'dc_position_4h', n, 0.5)
        ok = ok & ((d1 < th) | (d4 < th)) if is_long else ok & ((d1 > 1.0 - th) | (d4 > 1.0 - th))
    if bool(getattr(cfg, 'WT_COMPOSITE_VETO_ENABLED_TRADIER', False)) and bool(getattr(cfg, 'WT_COMPOSITE_SCORING_ENABLED_TRADIER', False)):
        al = _arr(npz, 'wt_bull_alignment' if is_long else 'wt_bear_alignment', n, 0.0)
        co = _arr(npz, 'wt_composite_long' if is_long else 'wt_composite_short', n, 0.0)
        ok = ok & ~((al < 2) | (co < -20.0))
    # COMBINED_STOCH_GATE_TRADIER (13356) reads k_5m: no 5m data in the backtest -> inert by rule (user: no 1m/3m/5m)
    return ok


def build(npz, n, is_long, cfg, blocks, extra_ok, close):
    """Return (signal, per_source_counts). `extra_ok` is accepted for call compatibility and ignored (see live_veto)."""
    extra_ok = live_veto(npz, n, is_long, cfg)
    counts = {}
    veto = np.zeros(n, dtype=bool)
    wt = blocks.get('B_WT_DC_LIVE')
    if wt is not None:
        veto = veto | wt
        counts['WT_DC'] = int(wt.sum())
    for name in SOURCE_BLOCKS:
        b = blocks.get(name)
        if b is not None:
            veto = veto | b
            counts[name] = int(b.sum())
    try:
        if bool(getattr(cfg, 'RZ_BREAKOUT_ENTRY_ENABLED', False)):
            import vec_decisions.process_position_stocks__alt_entries as _alt
            r = _alt.check_rz_breakout_vec(cfg, _arr(npz, 'bb_pct_b_1h', n, 0.5), is_long)
            veto = veto | r
            counts['RZ_BREAKOUT'] = int(r.sum())
        if bool(getattr(cfg, 'LR_PCTB_D_LONG_ENTRY_ENABLED', False)):
            import vec_decisions.process_position_stocks__alt_entries as _alt
            r = _alt.check_lr_pctb_d_long_vec(cfg, _arr(npz, 'lr_pct_b_D', n, 0.5), is_long)
            veto = veto | r
            counts['LR_PCTB_D'] = int(r.sum())
    except Exception:
        pass
    sig = veto & extra_ok
    bb4 = bb4h_stage0(npz, n, cfg, is_long, close)
    counts['BB4H_STAGE0'] = int(bb4.sum())
    return sig | bb4, counts
