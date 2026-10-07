"""vec_paths.exit_top_fade — vector twin for EXIT_TOP/TP fade family (2026-08-18)

Side-aware TOP for LONG mirrors BREAKDOWN for SHORT, vetoed by structural_exit_permitted twin.
Mirrors wt_dc_delta.DeltaTracker oscillator block and tradier_manage.evaluate_stop live block.
"""
import numpy as np

def _safe(npz, key, n, default=None):
    if key in npz:
        arr = npz[key]
        if hasattr(arr, '__len__') and len(arr) >= n:
            return np.asarray(arr[:n], dtype=float)
        elif np.isscalar(arr):
            return np.full(n, float(arr), dtype=float)
    return np.full(n, float(default) if default is not None else 0.0, dtype=float)

def structural_permitted_vec(npz, n, is_long, close, ltf="5m"):
    # Twin of wt_dc_delta.structural_exit_permitted vectorized
    hi = _safe(npz, f'high_{ltf}', n); hip = _safe(npz, f'high_{ltf}_prev', n)
    lo = _safe(npz, f'low_{ltf}', n); lop = _safe(npz, f'low_{ltf}_prev', n)
    op = _safe(npz, f'open_{ltf}', n); cl = _safe(npz, f'close_{ltf}', n)
    h1 = _safe(npz, 'high_1h', n); h1p = _safe(npz, 'high_1h_prev', n)
    l1 = _safe(npz, 'low_1h', n); l1p = _safe(npz, 'low_1h_prev', n)
    h4 = _safe(npz, 'high_4h', n); h4p = _safe(npz, 'high_4h_prev', n)
    l4 = _safe(npz, 'low_4h', n); l4p = _safe(npz, 'low_4h_prev', n)
    px = close
    have = (hip.sum() > 0) or (h1p.sum() > 0) or (h4p.sum() > 0)
    if is_long:
        rising = (cl > op) | (hi > hip) | (px > hip)
        ltf_collapse = (hi < hip) & (lo < lop) & (px < lop)
        htf = ((h1 < h1p) & (l1 < l1p)) | ((h4 < h4p) & (l4 < l4p))
    else:
        rising = (cl < op) | (lo < lop) | (px < lop)
        ltf_collapse = (hi > hip) & (lo > lop) & (px > hip)
        htf = ((h1 > h1p) & (l1 > l1p)) | ((h4 > h4p) & (l4 > l4p))
    permitted = (ltf_collapse | htf) & (~rising) if have else np.zeros(n, dtype=bool)
    return permitted

def compute_exit_top_fade(npz, n, is_long, cfg, close=None):
    """Compute fade-family exit signals. Returns bool array, already vetoed."""
    if getattr(cfg, '_G0_PURE_BH', False):
        return np.zeros(n, dtype=bool)
    if close is None:
        close = _safe(npz, 'close', n)
    out = np.zeros(n, dtype=bool)
    # MFI flip
    if getattr(cfg, 'MFI_FLIP_EXIT_ENABLED', False):
        mfi = _safe(npz, 'mfi_1h', n, 50)
        thr_l = float(getattr(cfg, 'MFI_FLIP_EXIT_LONG_THRESHOLD', 70.0))
        thr_s = float(getattr(cfg, 'MFI_FLIP_EXIT_SHORT_THRESHOLD', 30.0))
        if is_long:
            out = out | (mfi > thr_l)
        else:
            out = out | (mfi < thr_s)
    # RSI
    if is_long and getattr(cfg, 'RSI_EXIT_LONG_TRADIER', 0) and getattr(cfg, 'RSI_EXIT_LONG_TRADIER', 0) > 0:
        rsi = _safe(npz, 'rsi_1h', n, 50)
        thr = float(getattr(cfg, 'RSI_EXIT_LONG_TRADIER', 85.0))
        out = out | (rsi >= thr)
    if not is_long and getattr(cfg, 'RSI_EXIT_SHORT_TRADIER', 100) < 100:
        rsi = _safe(npz, 'rsi_1h', n, 50)
        thr = float(getattr(cfg, 'RSI_EXIT_SHORT_TRADIER', 15.0))
        out = out | (rsi <= thr)
    # RSI2 / Connors
    rsi2 = None
    for key in ('rsi2_5m', 'connors_rsi_5m', 'rsi_2_1h', 'connors_rsi_1h'):
        if key in npz:
            rsi2 = _safe(npz, key, n, 50)
            break
    if rsi2 is not None:
        use_tr = getattr(cfg, 'TRADIER_RSI2_ENABLED', False)
        if is_long:
            thr = float(getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_LONG', 90.0) if use_tr else getattr(cfg, 'RSI2_EXIT_THRESHOLD_LONG', 70.0))
            out = out | (rsi2 >= thr)
        else:
            thr = float(getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_SHORT', 10.0) if use_tr else getattr(cfg, 'RSI2_EXIT_THRESHOLD_SHORT', 30.0))
            out = out | (rsi2 <= thr)
    # Stoch cross 1h
    if getattr(cfg, 'STOCH_CROSS_1H_EXIT_ENABLED', False):
        k = _safe(npz, 'stoch_k_1h', n, 50); d = _safe(npz, 'stoch_d_1h', n, 50)
        kp = np.roll(k, 1); kp[0]=k[0]
        if is_long:
            out = out | ((kp >= d) & (k < d) & (kp >= 70))
        else:
            out = out | ((kp <= d) & (k > d) & (kp <= 30))
    # WT_DC scorer proxy
    if getattr(cfg, 'WT_DC_EXIT_ENABLED', False):
        # use wt_score if present else wt1
        wt_score = _safe(npz, 'wt_score_1h', n, 0)
        thr = float(getattr(cfg, 'WT_DC_EXIT_THRESHOLD', 25.0))
        if is_long:
            out = out | (wt_score < -thr)
        else:
            out = out | (wt_score > thr)
    # WT veto
    if getattr(cfg, 'WT_EXIT_VETO_ENABLED_TRADIER', False):
        tfs_str = str(getattr(cfg, 'WT_EXIT_TFS_TRADIER', '5m+15m+1h+4h+D'))
        tfs = [t.strip() for t in tfs_str.replace('+',',').split(',') if t.strip()]
        min_tfs = int(getattr(cfg, 'WT_EXIT_MIN_TFS_TRADIER', 4))
        against = np.zeros(n, dtype=int)
        for tf in tfs:
            w1 = _safe(npz, f'wt1_{tf}', n, 0); w2 = _safe(npz, f'wt2_{tf}', n, 0)
            if is_long:
                against += (w1 < w2).astype(int)
            else:
                against += (w1 > w2).astype(int)
        out = out | (against >= min_tfs)
    # MTF GR
    if getattr(cfg, 'MTF_GR_EXIT_GATE_ENABLED', False):
        min_tfs = int(getattr(cfg, 'MTF_GR_EXIT_MIN_TFS', 3))
        wt1_1h = _safe(npz, 'wt1_1h', n, 0); wt2_1h = _safe(npz, 'wt2_1h', n, 0)
        wt1_4h = _safe(npz, 'wt1_4h', n, 0); wt2_4h = _safe(npz, 'wt2_4h', n, 0)
        wt1_D = _safe(npz, 'wt1_D', n, 0); wt2_D = _safe(npz, 'wt2_D', n, 0)
        if is_long:
            cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
            ltf = _safe(npz, 'wt1_5m', n, 0) < _safe(npz, 'wt2_5m', n, 0)
        else:
            cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
            ltf = _safe(npz, 'wt1_5m', n, 0) > _safe(npz, 'wt2_5m', n, 0)
        out = out | ((cnt >= min_tfs) & ltf)
    # GR HTF direct
    if getattr(cfg, 'GR_HTF_DIRECT_EXIT_ENABLED', False):
        thr = float(getattr(cfg, 'GR_HTF_DIRECT_EXIT_SCORE', 12.0))
        rsi_1h = _safe(npz, 'rsi_1h', n, 50); rsi_4h = _safe(npz, 'rsi_4h', n, 50); rsi_D = _safe(npz, 'rsi_D', n, 50)
        wt1_1h = _safe(npz, 'wt1_1h', n, 0); wt2_1h = _safe(npz, 'wt2_1h', n, 0)
        wt1_4h = _safe(npz, 'wt1_4h', n, 0); wt2_4h = _safe(npz, 'wt2_4h', n, 0)
        wt1_D = _safe(npz, 'wt1_D', n, 0); wt2_D = _safe(npz, 'wt2_D', n, 0)
        if is_long:
            s = (wt1_1h < wt2_1h).astype(int)+(rsi_1h<50).astype(int)+(wt1_4h < wt2_4h).astype(int)+(rsi_4h<50).astype(int)+(wt1_D < wt2_D).astype(int)+(rsi_D<50).astype(int)
        else:
            s = (wt1_1h > wt2_1h).astype(int)+(rsi_1h>50).astype(int)+(wt1_4h > wt2_4h).astype(int)+(rsi_4h>50).astype(int)+(wt1_D > wt2_D).astype(int)+(rsi_D>50).astype(int)
        out = out | (s >= thr)
    # Formation — LH+LL for LONG top, HH+HL for SHORT breakdown
    fam_any = any([getattr(cfg, 'FORMATION_HEAD_SHOULDERS_EXIT_ENABLED', False),
                   getattr(cfg, 'FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED', False),
                   getattr(cfg, 'FORMATION_WEDGE_EXIT_ENABLED', False),
                   getattr(cfg, 'FORMATION_TRIANGLE_EXIT_ENABLED', False),
                   getattr(cfg, 'FORMATION_FLAG_PENNANT_EXIT_ENABLED', False),
                   getattr(cfg, 'FORMATION_CUP_HANDLE_EXIT_ENABLED', False),
                   getattr(cfg, 'FORMATION_TREND_STRUCTURE_EXIT_ENABLED', False)])
    if fam_any:
        h1 = _safe(npz, 'high_1h', n); hp = _safe(npz, 'high_1h_prev', n)
        l1 = _safe(npz, 'low_1h', n); lp = _safe(npz, 'low_1h_prev', n)
        if hp.sum()>0:
            if is_long:
                out = out | ((h1 < hp) & (l1 < lp))
            else:
                out = out | ((h1 > hp) & (l1 > lp))
    # Regime — WT velocity reversal gated by regime; gain floor enforced by simulator, here just signal
    adx = _safe(npz, 'adx_1h', n, 20); wv = _safe(npz, 'wt_velocity_1h', n, 0)
    trending = adx >= 25
    if is_long:
        out = out | ((trending & (wv < -1.0)) | ((~trending) & (wv < -0.5)))
    else:
        out = out | ((trending & (wv > 1.0)) | ((~trending) & (wv > 0.5)))
    # Structural veto — NOT rising AND (LTF collapse OR LH+LL)
    if getattr(cfg, 'STRUCTURAL_EXIT_GATE_ENABLED', True):
        permitted = structural_permitted_vec(npz, n, is_long, close)
        out = out & permitted
    return out
