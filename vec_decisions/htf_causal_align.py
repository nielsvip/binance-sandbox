"""HTF causal alignment for crypto NPZs (AUDIT/001, 2026-10-01).

Crypto indicator NPZs broadcast every 1h/4h/D array to the 15m grid with the row whose LEFT label is <= the 15m label
(backtest_v8_precompute._broadcast_asof_indices lag=1), i.e. at every 15m bar of an hour the array already holds the
COMPLETE hour's value (future highs/lows/closes of that hour). Stocks (tradier mode, lag=2) are causal.

align_store() shifts every HTF-suffixed array (_1h/_4h/_D and _prev/_ant) by ONE completed HTF bar so that at each 15m bar the
value is that of the last COMPLETED HTF bar (never future data). It is applied once per loaded store, only when the store is
detected as leaky (dc_high_1h/4h >= the 15m high on >=99% of bars and >=85% of their own segments; causal stock NPZs show ~93-95% / 30-57%), so an NPZ rebuilt
with the corrected broadcast is never double-lagged. V12_HTF_LEAK_LEGACY=1 keeps the old (leaky) behaviour for comparison.
NPZW/001 (2026-10-01): W (Monday-anchored week) and M (calendar month) arrays are shifted by one completed bucket as well, and the marker
'causal_v3' (builder with fresh+causal W/M) skips everything while the older marker 'causal_v2' (1h/4h/D already causal, W/M still leaky and stale) only gets the W/M shift +
composite recompute. STALE W/M arrays (frozen since the klines W/M files were last written, 2026-08-26) cannot be repaired at load: rebuild the NPZ.
Live crypto sees the FORMING htf candle (partial values); 'completed' is the conservative no-look-ahead bound.
"""
import os
import re
import numpy as np

_PAT = re.compile(r'_(1h|4h|D)(_prev|_ant|_prev2)?$')
_PER = {'1h': 3600, '4h': 14400, 'D': 86400}
_PAT_WM = re.compile(r'_(W|M)(_prev|_ant|_prev2)?$')
MARK = '_htf_causal_align'


def _tsec(ts):
    ts = np.asarray(ts)
    return ts / 1000.0 if len(ts) and float(ts[0]) > 1e11 else ts.astype(float)


def is_leaky(store):
    try:
        h = np.asarray(store['high_15m'])
        ts = _tsec(store['timestamps'])
        n = len(h)
        verdicts = []
        for tf in ('1h', '4h'):
            a = store.get('dc_high_' + tf)
            if a is None:
                continue
            a = np.asarray(a)
            if a.ndim != 1 or len(a) != n or n < 500:
                continue
            ge = float(np.mean(a >= h - 1e-12 * np.abs(h)))
            ch = np.where(np.diff(a) != 0)[0] + 1
            starts = np.r_[0, ch]
            ends = np.r_[ch, n]
            tot = ok = 0
            for s, e in zip(starts, ends):
                if e - s < 2:
                    continue
                tot += 1
                if a[s] >= h[s:e].max() - 1e-9 * abs(a[s]):
                    ok += 1
            verdicts.append(ge >= 0.99 and tot > 20 and ok / tot >= 0.85)
        return bool(verdicts) and all(verdicts)
    except Exception:
        return False


# AUDIT2/001: daily-bar derived keys without an _D suffix (builder compute_tf_arrays 'D' branch, broadcast with the same lag-1 leak)
_D_KEYS = ('clenow_r2', 'clenow_score', 'clenow_slope', 'gk_vol_60_d', 'pk_vol_60_d', 'yz_vol_60_d', 'pct_from_52w_high', 'pct_from_52w_low',
           'sepa_pass', 'sepa_score')
_TFS = ('15m', '1h', '4h', 'D', 'W', 'M')
_TF_DIV_CODE = {'15m': 1, '1h': 2, '4h': 3, 'D': 4, 'W': 5, 'M': 6}


def _recompute_composites(m, n):
    """Cross-TF WT composites exactly as backtest_v8_precompute (WT composite block + GLOBAL/CROSS-TF pass), from the store's (aligned) components."""
    tfs = [t for t in _TFS if any(('%s_%s' % (p, t)) in m for p in ('wt_bullish', 'wt1', 'wt_score', 'wt_cross_bull'))]
    if not tfs:
        return 0
    def z8():
        return np.zeros(n, dtype=np.int8)
    bull = z8(); bear = z8(); bcc = z8(); brc = z8(); osc = z8(); obc = z8(); vup = z8(); vdn = z8(); ris = z8(); fal = z8()
    hh = z8(); hl = z8(); ll = z8(); abd = z8(); aed = z8()
    cl = np.zeros(n, dtype=np.float32); cs = np.zeros(n, dtype=np.float32)
    lh = z8(); bull_code = z8(); bear_code = z8()
    for tf in tfs:
        b = m.get('wt_bullish_' + tf)
        if b is not None:
            bull += (np.asarray(b) > 0).astype(np.int8); bear += (np.asarray(b) <= 0).astype(np.int8)
        cb = m.get('wt_cross_bull_' + tf)
        if cb is not None:
            bcc += np.asarray(cb).astype(np.int8)
        cr = m.get('wt_cross_bear_' + tf)
        if cr is not None:
            brc += np.asarray(cr).astype(np.int8)
        w1 = m.get('wt1_' + tf)
        if w1 is not None:
            w1 = np.asarray(w1, dtype=np.float64)
            osc += (w1 < -53).astype(np.int8); obc += (w1 > 53).astype(np.int8)
        vel = m.get('wt_velocity_' + tf)
        if vel is not None:
            vel = np.asarray(vel, dtype=np.float64)
            vup += (vel > 0).astype(np.int8); vdn += (vel < 0).astype(np.int8)
        rs = m.get('wt_cross_rising_' + tf)
        if rs is not None:
            ris += np.asarray(rs).astype(np.int8); fal += (1 - np.asarray(rs)).astype(np.int8)
        ps = m.get('wt_peak_structure_' + tf)
        if ps is not None:
            ps = np.asarray(ps)
            hh += (ps == 1).astype(np.int8)
            if tf in ('1h', '4h', 'D'):
                lh += (ps == -1).astype(np.int8)
        ts_s = m.get('wt_trough_structure_' + tf)
        if ts_s is not None:
            ts_s = np.asarray(ts_s)
            hl += (ts_s == 1).astype(np.int8); ll += (ts_s == -1).astype(np.int8)
        dv = m.get('wt_divergence_' + tf)
        if dv is not None:
            dv = np.asarray(dv)
            abd = np.maximum(abd, (dv > 0).astype(np.int8)); aed = np.maximum(aed, (dv < 0).astype(np.int8))
            code = _TF_DIV_CODE.get(tf, 0)
            bull_code = np.where(dv > 0, code, bull_code).astype(np.int8)
            bear_code = np.where(dv < 0, code, bear_code).astype(np.int8)
        sc = m.get('wt_score_' + tf)
        if sc is not None:
            s = np.asarray(sc, dtype=np.float64)
            w = {'15m': 2, '1h': 3, '4h': 4, 'D': 5, 'W': 2, 'M': 1}.get(tf, 1)
            cl += np.maximum(0, s) * w; cs += np.maximum(0, -s) * w
    cl = cl.astype(np.float32); cs = cs.astype(np.float32)
    new = {'wt_bull_alignment': bull, 'wt_bear_alignment': bear, 'wt_bull_cross_count': bcc, 'wt_bear_cross_count': brc,
           'wt_oversold_tf_count': osc, 'wt_overbought_tf_count': obc, 'wt_velocity_up_count': vup, 'wt_velocity_down_count': vdn,
           'wt_rising_cross_count': ris, 'wt_falling_cross_count': fal, 'wt_hh_count': hh, 'wt_hl_count': hl, 'wt_ll_count': ll,
           'wt_any_bull_div': abd, 'wt_any_bear_div': aed, 'wt_composite_long': cl, 'wt_composite_short': cs,
           'wt_composite_delta': (cl - cs).astype(np.float32),
           'wt_composite_bias': np.where(cl > cs, 1, np.where(cs > cl, -1, 0)).astype(np.int8),
           'wt_lh_count': lh, 'wt_strongest_bull_div_tf': bull_code, 'wt_strongest_bear_div_tf': bear_code}
    c = 0
    for k, v in new.items():
        if k in m:
            m[k] = v; c += 1
    return c


def _wm_key(ts, tf):
    """Integer bucket id per bar: W = Monday-anchored week (1970-01-05 was a Monday), M = calendar month."""
    t = np.asarray(ts, dtype=np.float64)
    if tf == 'W':
        return np.floor((t - 345600.0) / 604800.0).astype(np.int64)
    return t.astype('int64').astype('datetime64[s]').astype('datetime64[M]').astype(np.int64)


def _shift_by_key(a, key):
    b = a.copy()
    starts = np.r_[0, np.where(np.diff(key) != 0)[0] + 1]
    n = len(a)
    for i, s_ in enumerate(starts):
        if s_ == 0:
            continue
        e = starts[i + 1] if i + 1 < len(starts) else n
        b[s_:e] = a[s_ - 1]
    return b


def _shift_wm(store, out, ts):
    n = len(ts)
    c = 0
    for k, a in store.items():
        m = _PAT_WM.search(k)
        if not m:
            continue
        a = np.asarray(a)
        if a.ndim != 1 or len(a) != n:
            continue
        out[k] = _shift_by_key(a, _wm_key(ts, m.group(1)))
        c += 1
    return c


def align_store(store, sym='', mode=''):
    if os.environ.get('V12_HTF_LEAK_LEGACY', '') == '1':
        return store
    if not isinstance(store, dict) or store.get(MARK) is not None:
        return store
    # NPZB 2026-10-01: NPZs rebuilt with the causal broadcast (backtest_v8_precompute, mode crypto) carry htf_align='causal_v2': never lag them again
    try:
        _mk = store.get('htf_align')
        _mks = str(np.asarray(_mk).ravel()[0]) if _mk is not None else ''
        if _mks == 'causal_v3':
            return store  # builder with fresh + causal 1h/4h/D/W/M: never lag again
        if _mks == 'causal_v2':
            # 1h/4h/D already causal; W/M still leaky (and possibly stale): shift W/M one completed bucket and recompute the cross-TF composites
            if 'timestamps' not in store:
                return store
            ts2 = _tsec(store['timestamps'])
            out2 = dict(store)
            c2 = _shift_wm(store, out2, ts2)
            rec2 = _recompute_composites(out2, len(ts2))
            out2[MARK] = 'v2_wm_only:%d_wm_arrays+%d_composites' % (c2, rec2)
            return out2
    except Exception:
        pass
    if 'timestamps' not in store or not is_leaky(store):
        return store
    ts = _tsec(store['timestamps'])
    n = len(ts)
    out = dict(store)
    shifted = 0
    for k, a in store.items():
        m = _PAT.search(k)
        if not m:
            continue
        a = np.asarray(a)
        if a.ndim != 1 or len(a) != n:
            continue
        key = (ts // _PER[m.group(1)]).astype(np.int64)
        b = a.copy()
        starts = np.r_[0, np.where(np.diff(key) != 0)[0] + 1]
        for i, s in enumerate(starts):
            if s == 0:
                continue
            e = starts[i + 1] if i + 1 < len(starts) else n
            b[s:e] = a[s - 1]
        out[k] = b
        shifted += 1
    # AUDIT2/001 (2026-10-01): the NPZ builder also writes keys WITHOUT an htf suffix that are built from the daily bar (broadcast with the same
    # lag-1 leak) or from several broadcast TFs (cross-TF composites). Shift the daily ones by one completed D bar and recompute the cross-TF
    # composites from the (now causal) components with the builder's exact formulas.
    shifted_d = 0
    for k in _D_KEYS:
        a = out.get(k)
        if a is None:
            continue
        a = np.asarray(a)
        if a.ndim != 1 or len(a) != n:
            continue
        key = (ts // 86400).astype(np.int64)
        b = a.copy()
        starts = np.r_[0, np.where(np.diff(key) != 0)[0] + 1]
        for i, s in enumerate(starts):
            if s == 0:
                continue
            e = starts[i + 1] if i + 1 < len(starts) else n
            b[s:e] = a[s - 1]
        out[k] = b
        shifted_d += 1
    shifted_wm = _shift_wm(store, out, ts)
    recomputed = _recompute_composites(out, n)
    out[MARK] = 'completed_prev_htf_bar:%d_arrays+%d_daily+%d_wm+%d_composites' % (shifted, shifted_d, shifted_wm, recomputed)
    return out
