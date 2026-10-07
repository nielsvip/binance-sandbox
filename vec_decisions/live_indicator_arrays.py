"""Per-bar array twins of the LIVE crypto indicator functions (ez_indicators) for the NPZ builder (PARITY 2026-10-06).

USER: "live != vec disparity must be impossible". backtest_v8_precompute.compute_tf_arrays built crypto arrays with the STOCK
WaveTrend parameters and hand-rolled intelligence loops that are not what ez_ computes live. crypto_overrides(df, tf) returns, for
every bar j, exactly the value ez_indicators.IndicatorCalculator.compute would emit on the prefix df[:j+1] (no look-ahead), under the
builder's array names and integer encodings:
  WaveTrend           ez_indicators.wavetrend (WT_TF_PARAMS)                       -> wt1/wt2/wt_score/wt_bullish
  WT intelligence     ez_indicators.wavetrend_intelligence (3-bar pivots confirmed by the next bar, velocity lag 3, 200-bar
                      percentile/zscore, zero-cross wave phase, recent-cross memory)  -> wt_cross*, wt_peak*/trough*, structures,
                      divergence (+strength), velocity/acceleration/momentum_state, percentile/zscore/extreme, wave_phase, signal
  Bollinger           ez_indicators.bb_auto_tune PER BAR (sigma sweep 1.5..3.5 over the trailing 100 bars, bands of the previous
                      bar's 20-window vs this bar's high/low; final bands on the window ending at this bar). The builder's old
                      full-series sigma pick was a LOOK-AHEAD.  bb_width in live units: (upper-lower)/mid*100.
  relative_volume     ez_indicators.relative_volume(df, 20): volume[j-1] / mean(volume[j-21:j-1])  (last completed bar)
  candle_body_ratio   |c-o| / |c_prev-o_prev| (round 3; prev body 0 -> 1.0)
  ha / ha_streak      ez_indicators.heikin_ashi / ha_streak_count (non-recursive HA open = (prev HA close + prev (o+c)/2)/2)
Codes: wt_cross/structures/signal/wave_phase -1/0/1, momentum_state -2..2 (EXHAUST_DOWN..EXHAUST_UP), divergence BULL 1 / HIDDEN_BULL 2 /
BEAR -1 / HIDDEN_BEAR -2 (sign = live family), None -> 0 (values) / 999 (cross_bars_ago)."""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

_MOM = {"EXHAUST_DOWN": -2, "IMPULSE_DOWN": -1, "IMPULSE_UP": 1, "EXHAUST_UP": 2}


def _last_idx(mask: np.ndarray) -> np.ndarray:
    """per position j: index of the last True at or before j (-1 if none)."""
    n = len(mask)
    idx = np.where(mask, np.arange(n), -1)
    return np.maximum.accumulate(idx) if n else idx


def _prev_of(last: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """per position j: index of the True before last[j] (-1 if none)."""
    n = len(mask)
    lst = _last_idx(mask)
    out = np.full(n, -1, dtype=np.int64)
    ok = last > 0
    out[ok] = lst[last[ok] - 1]
    return out


def _confirmed_pivots(x: np.ndarray, up: bool) -> np.ndarray:
    """live 3-bar pivot mask; pivot k is visible from bar k+1 on (live computes masks on the prefix, last bar never a pivot)."""
    n = len(x)
    m = np.zeros(n, dtype=bool)
    if n >= 3:
        if up:
            m[1:-1] = (x[1:-1] > x[:-2]) & (x[1:-1] > x[2:])
        else:
            m[1:-1] = (x[1:-1] < x[:-2]) & (x[1:-1] < x[2:])
    return m


def _visible_last_two(mask: np.ndarray):
    """for each bar j: (last, prev) pivot index among pivots k <= j-1 (confirmation needs bar k+1 <= j)."""
    n = len(mask)
    last_any = _last_idx(mask)
    last = np.full(n, -1, dtype=np.int64)
    last[1:] = last_any[:-1]
    prev = np.full(n, -1, dtype=np.int64)
    ok = last > 0
    prev[ok] = last_any[last[ok] - 1]
    return last, prev


def wt_intelligence_arrays(wt1: np.ndarray, wt2: np.ndarray, close: np.ndarray, high: np.ndarray, low: np.ndarray, tf: str) -> Dict[str, np.ndarray]:
    w1 = np.asarray(wt1, dtype=np.float64)
    w2 = np.asarray(wt2, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    n = len(w1)
    ar = np.arange(n)
    out: Dict[str, np.ndarray] = {}
    out[f"wt1_{tf}"] = w1.astype(np.float32)
    out[f"wt2_{tf}"] = w2.astype(np.float32)
    out[f"wt_score_{tf}"] = (w1 - w2).astype(np.float32)
    out[f"wt_bullish_{tf}"] = (w1 > w2).astype(np.int8)
    # --- crosses + recent-cross memory ---
    ca = np.zeros(n, dtype=bool)
    cb = np.zeros(n, dtype=bool)
    if n >= 2:
        ca[1:] = (w1[1:] > w2[1:]) & (w1[:-1] <= w2[:-1])
        cb[1:] = (w1[1:] < w2[1:]) & (w1[:-1] >= w2[:-1])
    la, lb = _last_idx(ca), _last_idx(cb)
    pa, pb = _prev_of(la, ca), _prev_of(lb, cb)
    bull = la > lb
    has = (la >= 0) | (lb >= 0)
    last = np.where(bull, la, lb)
    prev = np.where(bull, pa, pb)
    direction = np.where(has, np.where(bull, 1, -1), 0).astype(np.int8)
    bars_ago = np.where(has, ar - last, 999)
    val = np.where(has, w1[np.maximum(last, 0)], 0.0)
    pval = np.where(has & (prev >= 0), w1[np.maximum(prev, 0)], 0.0)
    rising = np.where(has & (prev >= 0), np.where(bull, val > pval, val < pval), False)
    xc = np.where(has, c[np.maximum(last, 0)], 0.0)
    xcp = np.where(has & (prev >= 0), c[np.maximum(prev, 0)], 0.0)
    out[f"wt_cross_{tf}"] = direction
    out[f"wt_cross_value_{tf}"] = val.astype(np.float32)
    out[f"wt_cross_prev_value_{tf}"] = pval.astype(np.float32)
    out[f"wt_cross_rising_{tf}"] = rising.astype(np.int8)
    out[f"wt_cross_bull_{tf}"] = ((direction == 1) & (bars_ago == 0)).astype(np.int8)
    out[f"wt_cross_bear_{tf}"] = ((direction == -1) & (bars_ago == 0)).astype(np.int8)
    out[f"wt_crossover_value_{tf}"] = xc.astype(np.float32)
    out[f"wt_crossover_value_{tf}_prev"] = xcp.astype(np.float32)
    out[f"wt_cross_bars_ago_{tf}"] = bars_ago.astype(np.int32)
    # --- WT structure (pivots confirmed by the next bar) ---
    pk_last, pk_prev = _visible_last_two(_confirmed_pivots(w1, True))
    tr_last, tr_prev = _visible_last_two(_confirmed_pivots(w1, False))
    pk = np.where(pk_last >= 0, w1[np.maximum(pk_last, 0)], np.nan)
    pkp = np.where(pk_prev >= 0, w1[np.maximum(pk_prev, 0)], np.nan)
    tr = np.where(tr_last >= 0, w1[np.maximum(tr_last, 0)], np.nan)
    trp = np.where(tr_prev >= 0, w1[np.maximum(tr_prev, 0)], np.nan)
    out[f"wt_peak_{tf}"] = np.nan_to_num(pk).astype(np.float32)
    out[f"wt_peak_prev_{tf}"] = np.nan_to_num(pkp).astype(np.float32)
    out[f"wt_trough_{tf}"] = np.nan_to_num(tr).astype(np.float32)
    out[f"wt_trough_prev_{tf}"] = np.nan_to_num(trp).astype(np.float32)
    have_pk = ~np.isnan(pk) & ~np.isnan(pkp)
    have_tr = ~np.isnan(tr) & ~np.isnan(trp)
    ps = np.where(have_pk, np.where(pk > pkp, 1, -1), 0).astype(np.int8)
    ts_ = np.where(have_tr, np.where(tr > trp, 1, -1), 0).astype(np.int8)
    out[f"wt_peak_structure_{tf}"] = ps
    out[f"wt_trough_structure_{tf}"] = ts_
    out[f"wt_structure_{tf}"] = np.where(ps != 0, ps, ts_).astype(np.int8)
    # --- divergence (price pivots on high/low, same confirmation rule) ---
    pp_last, pp_prev = _visible_last_two(_confirmed_pivots(h, True))
    pt_last, pt_prev = _visible_last_two(_confirmed_pivots(lo, False))
    have_pp = (pp_last >= 0) & (pp_prev >= 0)
    have_pt = (pt_last >= 0) & (pt_prev >= 0)
    p_pk, p_pkp = h[np.maximum(pp_last, 0)], h[np.maximum(pp_prev, 0)]
    p_tr, p_trp = lo[np.maximum(pt_last, 0)], lo[np.maximum(pt_prev, 0)]
    div = np.zeros(n, dtype=np.int8)
    strength = np.zeros(n, dtype=np.float64)
    tr_ok = have_pt & have_tr
    with np.errstate(invalid="ignore", divide="ignore"):
        bull_reg = tr_ok & (p_tr < p_trp) & (tr > trp)
        bull_hid = tr_ok & ~bull_reg & (p_tr > p_trp) & (tr < trp)
        s_bull = np.minimum(1.0, (np.abs(p_trp - p_tr) / (np.abs(p_trp) + 1e-10) + np.abs(tr - trp) / (np.abs(trp) + 1e-10)) / 2.0)
        s_bhid = np.minimum(1.0, np.abs(trp - tr) / (np.abs(trp) + 1e-10))
        none_yet = ~(bull_reg | bull_hid)
        pk_ok = none_yet & have_pp & have_pk
        bear_reg = pk_ok & (p_pk > p_pkp) & (pk < pkp)
        bear_hid = pk_ok & ~bear_reg & (p_pk < p_pkp) & (pk > pkp)
        s_bear = np.minimum(1.0, (np.abs(p_pk - p_pkp) / (np.abs(p_pkp) + 1e-10) + np.abs(pkp - pk) / (np.abs(pkp) + 1e-10)) / 2.0)
        s_hhid = np.minimum(1.0, np.abs(pk - pkp) / (np.abs(pkp) + 1e-10))
    div[bull_reg] = 1; strength[bull_reg] = s_bull[bull_reg]
    div[bull_hid] = 2; strength[bull_hid] = s_bhid[bull_hid]
    div[bear_reg] = -1; strength[bear_reg] = s_bear[bear_reg]
    div[bear_hid] = -2; strength[bear_hid] = s_hhid[bear_hid]
    out[f"wt_divergence_{tf}"] = div
    out[f"wt_divergence_strength_{tf}"] = np.round(strength, 4).astype(np.float32)
    # --- velocity / acceleration / momentum (lag 3) ---
    lag = 3
    vel = np.zeros(n)
    velp = np.zeros(n)
    if n > lag:
        vel[lag:] = w1[lag:] - w1[:-lag]
    if n > 2 * lag:
        velp[2 * lag:] = w1[lag:-lag] - w1[:-2 * lag]
    acc = vel - velp
    rising_v = vel > 0
    mom = np.where(rising_v & (acc > 0), _MOM["IMPULSE_UP"], np.where(rising_v, _MOM["EXHAUST_UP"], np.where(acc < 0, _MOM["IMPULSE_DOWN"], _MOM["EXHAUST_DOWN"])))
    out[f"wt_velocity_{tf}"] = np.round(vel, 4).astype(np.float32)
    out[f"wt_acceleration_{tf}"] = np.round(acc, 4).astype(np.float32)
    out[f"wt_momentum_state_{tf}"] = mom.astype(np.int8)
    # --- adaptive bands: trailing window min(200, j+1) incl. current ---
    pct = np.zeros(n)
    zs = np.zeros(n)
    for j in range(n):
        win = w1[max(0, j - 199): j + 1]
        pct[j] = float(np.sum(win <= w1[j]) / len(win) * 100.0)
        sd = float(np.std(win))
        zs[j] = (w1[j] - float(np.mean(win))) / sd if sd > 1e-10 else 0.0
    out[f"wt_percentile_{tf}"] = np.round(pct, 2).astype(np.float32)
    out[f"wt_zscore_{tf}"] = np.round(zs, 4).astype(np.float32)
    out[f"wt_extreme_{tf}"] = (np.abs(zs) > 2.0).astype(np.int8)
    # --- wave phase ---
    zc = np.zeros(n, dtype=bool)
    if n >= 2:
        zc[1:] = (w1[1:] * w1[:-1]) < 0
    phase = np.zeros(n, dtype=np.int8)
    pk_any = ~np.isnan(pk) & ~np.isnan(pkp)
    tr_any = ~np.isnan(tr) & ~np.isnan(trp)
    with np.errstate(invalid="ignore"):
        ph_pk = np.where(np.abs(pk) > np.abs(pkp), 1, -1)
        ph_tr = np.where(np.abs(tr) > np.abs(trp), 1, -1)
    phase = np.where(zc, 0, np.where(pk_any, ph_pk, np.where(tr_any, ph_tr, 0))).astype(np.int8)
    out[f"wt_wave_phase_{tf}"] = phase
    # --- legacy signal ---
    sig = np.zeros(n, dtype=np.int8)
    if n >= 2:
        pw1, pw2 = np.r_[w1[0], w1[:-1]], np.r_[w2[0], w2[:-1]]
        sig = np.where((pw1 <= pw2) & (w1 > w2) & (w1 < -50), 1, np.where((pw1 >= pw2) & (w1 < w2) & (w1 > 50), -1, 0)).astype(np.int8)
    out[f"wt_signal_{tf}"] = sig
    return out


def bb_auto_tune_arrays(high: np.ndarray, low: np.ndarray, close: np.ndarray, tf: str, length: int = 20, lookback: int = 100, touch_pct: float = 0.002) -> Dict[str, np.ndarray]:
    """ez_indicators.bb_auto_tune evaluated on every prefix (live uses it when len(df) >= 120, else bb_features 2.0)."""
    h = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = len(c)
    s = pd.Series(c)
    mid = s.rolling(length).mean().to_numpy()
    sd = s.rolling(length).std(ddof=0).to_numpy()
    mults = np.arange(15, 36) / 10.0
    # band of the window ending at k-1 vs high/low of bar k (live: _rolling_mid[i] vs high_arr[i])
    mid_p = np.r_[np.nan, mid[:-1]]
    sd_p = np.r_[np.nan, sd[:-1]]
    up_t = np.zeros((len(mults), n))
    lo_t = np.zeros((len(mults), n))
    for m_i, m in enumerate(mults):
        u = mid_p + m * sd_p
        l_ = mid_p - m * sd_p
        with np.errstate(invalid="ignore"):
            up_t[m_i] = ((h >= u * (1 - touch_pct)) & (h <= u * (1 + touch_pct))).astype(float)
            lo_t[m_i] = ((lo <= l_ * (1 + touch_pct)) & (lo >= l_ * (1 - touch_pct))).astype(float)
    cu = np.concatenate([np.zeros((len(mults), 1)), np.cumsum(up_t, axis=1)], axis=1)
    cl = np.concatenate([np.zeros((len(mults), 1)), np.cumsum(lo_t, axis=1)], axis=1)
    best = np.full(n, 2.0)
    for j in range(n):
        nn = j + 1
        if nn < 120 or nn < length + 10:
            continue
        lb = min(lookback, nn - length)
        a, b = j - lb + 1, j + 1
        ut = cu[:, b] - cu[:, a]
        lt = cl[:, b] - cl[:, a]
        tot = ut + lt
        bal = np.minimum(ut, lt) / np.maximum(np.maximum(ut, lt), 1)
        score = tot * (0.5 + 0.5 * bal)
        bm, bs = 2.0, 0.0
        for k in range(len(mults)):
            if score[k] > bs:
                bs = score[k]; bm = mults[k]
        best[j] = bm
    upper = mid + best * sd
    lower = mid - best * sd
    bw = upper - lower
    with np.errstate(invalid="ignore", divide="ignore"):
        pb = np.where(bw > 0, (c - lower) / bw, 0.5)
        pb = np.round(np.clip(pb, 0.0, 1.0), 4)
        mid2 = (upper + lower) / 2.0
        width = np.where(mid2 > 0, np.round((upper - lower) / mid2 * 100.0, 3), 0.0)
    return {f"bb_upper_{tf}": upper.astype(np.float32), f"bb_lower_{tf}": lower.astype(np.float32), f"bb_pct_b_{tf}": pb.astype(np.float32),
            f"bb_width_{tf}": width.astype(np.float32)}


def candle_ha_relvol_arrays(o: np.ndarray, h: np.ndarray, l_: np.ndarray, c: np.ndarray, v: np.ndarray, tf: str, rel_len: int = 20) -> Dict[str, np.ndarray]:
    o, h, l_, c, v = (np.asarray(x, dtype=np.float64) for x in (o, h, l_, c, v))
    n = len(c)
    body = np.abs(c - o)
    body_prev = np.r_[body[0], body[:-1]] if n else body
    with np.errstate(invalid="ignore", divide="ignore"):
        cbr = np.where(body_prev > 0, np.round(body / body_prev, 3), 1.0)
    rv = np.full(n, np.nan)
    if n >= rel_len + 1:
        cs = np.r_[0.0, np.cumsum(v)]
        j = np.arange(rel_len, n)
        den = (cs[j] - cs[j - rel_len]) / rel_len
        with np.errstate(invalid="ignore", divide="ignore"):
            rv[j] = np.where(den != 0, v[j - 1] / den, np.nan)
    ha_c = (o + h + l_ + c) / 4.0
    so = (o + c) / 2.0
    ha_o = np.r_[np.nan, (ha_c[:-1] + so[:-1]) / 2.0] if n else ha_c
    color = np.where(ha_c >= ha_o, 1, -1).astype(np.int8)
    if n:
        color[0] = 1 if ha_c[0] >= (c[0] + o[0]) / 2.0 else -1
    streak = np.zeros(n, dtype=np.int8)
    for j in range(n):
        if j + 1 < 3:
            continue
        s = 0
        for idx in range(j, max(j + 1 - 21, 1), -1):
            col = color[idx]
            if s == 0:
                s = col
            elif (s > 0 and col > 0) or (s < 0 and col < 0):
                s += col
            else:
                break
        streak[j] = s
    return {f"candle_body_ratio_{tf}": cbr.astype(np.float32), f"relative_volume_{tf}": rv.astype(np.float32), f"ha_{tf}": color, f"ha_streak_{tf}": streak}


def causal_detect_divergence(price_arr, ind_arr, lookback: int = 5, decay: int = 10, match_window: int = 3):
    """ez_indicators.detect_divergence as LIVE sees it: at bar j it runs on the prefix [0..j], so an indicator pivot i only exists when
    i + lookback <= j. The builder's single full-array call matched indicator pivots up to ic+match_window whose confirmation lies
    after the signal bar (a look-ahead). Returns (reg_bull, reg_bear, hid_bull, hid_bear) int8, identical to per-prefix live calls."""
    from ez_indicators import find_pivots_arr as _fp
    p = np.asarray(price_arr, dtype=np.float64)
    q = np.asarray(ind_arr, dtype=np.float64)
    n = len(p)
    rb = np.zeros(n, dtype=np.int8); be = np.zeros(n, dtype=np.int8); hb = np.zeros(n, dtype=np.int8); hbe = np.zeros(n, dtype=np.int8)
    p_ph, p_pl = _fp(p, lookback)
    i_ph, i_pl = _fp(q, lookback)
    for p_piv, i_piv, lows in ((p_pl, i_pl, True), (p_ph, i_ph, False)):
        pp = np.where(p_piv)[0]
        ii = np.where(i_piv)[0]
        for k in range(1, len(pp)):
            ic, ip = pp[k], pp[k - 1]
            confirm = ic + lookback
            if confirm >= n:
                continue
            for j in range(confirm, min(n, confirm + decay)):
                vis = ii[ii + lookback <= j]
                c_match = vis[(vis >= ic - match_window) & (vis <= ic + match_window)]
                p_match = vis[(vis >= ip - match_window) & (vis <= ip + match_window)]
                if len(c_match) == 0 or len(p_match) == 0:
                    continue
                ic_i = c_match[np.argmin(np.abs(c_match - ic))]
                ip_i = p_match[np.argmin(np.abs(p_match - ip))]
                if lows:
                    if p[ic] < p[ip] and q[ic_i] > q[ip_i]: rb[j] = 1
                    if p[ic] > p[ip] and q[ic_i] < q[ip_i]: hb[j] = 1
                else:
                    if p[ic] > p[ip] and q[ic_i] < q[ip_i]: be[j] = 1
                    if p[ic] < p[ip] and q[ic_i] > q[ip_i]: hbe[j] = 1
    return rb, be, hb, hbe


def divergence_flag_arrays(df: pd.DataFrame, tf: str, wt1: np.ndarray = None) -> Dict[str, np.ndarray]:
    """live div_* flags on prefixes: detect_divergence(close, wt1) and (close, live 14-bar MFI recompute). Crypto live feeds the
    tradier_indicators (stock-param) wt1 (ez_indicators ~2372); stocks live feeds its own wt1 (tradier_indicators ~1835)."""
    _dd = causal_detect_divergence
    out: Dict[str, np.ndarray] = {}
    n = len(df)
    if n < 30:
        return out
    cl = df["close"].values.astype(np.float64)
    try:
        if wt1 is None:
            from tradier_indicators import wavetrend as _tr_wt
            tw1, _ = _tr_wt(df, timeframe=tf)
            wt1 = None if tw1 is None else tw1.values
        if wt1 is not None and len(wt1) == n:
            rb, be, hb, hbe = _dd(cl, np.asarray(wt1, dtype=np.float64), lookback=5, decay=10)
            out.update({f"div_reg_bull_wt_{tf}": np.asarray(rb, dtype=np.int8), f"div_reg_bear_wt_{tf}": np.asarray(be, dtype=np.int8),
                        f"div_hid_bull_wt_{tf}": np.asarray(hb, dtype=np.int8), f"div_hid_bear_wt_{tf}": np.asarray(hbe, dtype=np.int8)})
    except Exception:
        pass
    try:
        hs, ls, cs = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        vs = df["volume"].astype(float) if "volume" in df.columns else pd.Series(np.ones(n), index=df.index)
        tp = (hs + ls + cs) / 3.0
        rmf = tp * vs
        pm = rmf.where(tp.diff() > 0, 0).rolling(14, min_periods=1).sum()
        nm = rmf.where(tp.diff() < 0, 0).rolling(14, min_periods=1).sum()
        mfi = 100.0 - (100.0 / (1.0 + pm / nm.replace(0, 1e-10)))
        rb, be, hb, hbe = _dd(cl, mfi.values.astype(np.float64), lookback=5, decay=10)
        out.update({f"div_reg_bull_mfi_{tf}": np.asarray(rb, dtype=np.int8), f"div_reg_bear_mfi_{tf}": np.asarray(be, dtype=np.int8),
                    f"div_hid_bull_mfi_{tf}": np.asarray(hb, dtype=np.int8), f"div_hid_bear_mfi_{tf}": np.asarray(hbe, dtype=np.int8)})
    except Exception:
        pass
    return out


def dc_width_live(dc_high: np.ndarray, dc_low: np.ndarray, tf: str) -> Dict[str, np.ndarray]:
    """live: round((dc_high - dc_low) / dc_low * 100, 4) when dc_low > 0 and dc_high > dc_low (else live leaves the key unset -> 0)."""
    dh = np.asarray(dc_high, dtype=np.float64)
    dl = np.asarray(dc_low, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        w = np.where((dl > 0) & (dh > dl), np.round((dh - dl) / dl * 100.0, 4), 0.0)
    return {f"dc_width_{tf}": w.astype(np.float32)}


def atr_rank_live(high: np.ndarray, low: np.ndarray, tf: str, win: int = 50) -> Dict[str, np.ndarray]:
    """live detect_bar_patterns: atr_rank = #(h-l in the last min(50, j+1) bars strictly < current) / window (round 3);
    bar_vol_regime from the unrounded rank (<0.25 low -1, >0.75 high +1). The builder used rolling rank(pct) - 1/win (approximation)."""
    r = np.asarray(high, dtype=np.float64) - np.asarray(low, dtype=np.float64)
    n = len(r)
    rank = np.zeros(n)
    for j in range(n):
        w = r[max(0, j - win + 1): j + 1]
        rank[j] = float(np.sum(w < r[j])) / max(len(w), 1)
    reg = np.where(rank < 0.25, -1, np.where(rank > 0.75, 1, 0)).astype(np.int8)
    # live compression_ratio: ranges_5 = max(h-l, 1e-10) of the last min(5, j) bars (newest first); ratio = newest / oldest of that list
    # when it has >= 3 entries, else 1.0; round 3.
    rr = np.maximum(r, 1e-10)
    comp = np.ones(n)
    for j in range(n):
        m = min(5, j)  # live: range(1, min(6, n)) with n = j+1 -> min(5, j) entries
        if m >= 3:
            comp[j] = rr[j] / rr[j - m + 1]
    return {f"bar_atr_rank_{tf}": np.round(rank, 3).astype(np.float32), f"bar_vol_regime_{tf}": reg,
            f"bar_compression_ratio_{tf}": np.round(comp, 3).astype(np.float32)}


def adx_live(df: pd.DataFrame, tf: str, length: int = 14, tfs=("1h", "4h")) -> Dict[str, np.ndarray]:
    """ez_indicators.adx_value on every prefix (pandas ewm adjust=False is causal -> the full-series value at j equals the prefix value);
    live emits adx only for 1h/4h and only when len(df) >= 2*length+1."""
    from ez_indicators import atr_series as _atr
    n = len(df)
    if tf not in tfs or n < length * 2 + 1:
        return {}
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    plus_dm = high.diff().clip(lower=0.0)
    minus_dm = (-low.diff()).clip(lower=0.0)
    mask = plus_dm < minus_dm
    plus_dm = plus_dm.where(~mask, 0.0)
    minus_dm = minus_dm.where(mask, 0.0)
    atr = _atr(df, length)
    alpha = 1.0 / float(length)
    plus_di = 100.0 * (plus_dm.ewm(alpha=alpha, adjust=False, min_periods=length).mean() / atr.replace(0.0, np.nan))
    minus_di = 100.0 * (minus_dm.ewm(alpha=alpha, adjust=False, min_periods=length).mean() / atr.replace(0.0, np.nan))
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = ((plus_di - minus_di).abs() / di_sum) * 100.0
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=length).mean().to_numpy(copy=True)
    adx[: length * 2] = np.nan
    return {f"adx_{tf}": np.nan_to_num(adx, nan=0.0).astype(np.float32)}


def crypto_overrides(df: pd.DataFrame, tf: str, built: Dict[str, np.ndarray] = None) -> Dict[str, np.ndarray]:
    """all live-twin arrays for one crypto TF frame (builder hook: out.update(crypto_overrides(df, tf, out)) when MODE == 'crypto')."""
    from ez_indicators import wavetrend as _ez_wt
    out: Dict[str, np.ndarray] = {}
    w1s, w2s = _ez_wt(df, timeframe=tf)
    n = len(df)
    if w1s is not None and len(w1s) == n:
        out.update(wt_intelligence_arrays(w1s.values, w2s.values, df["close"].values, df["high"].values, df["low"].values, tf))
    out.update(bb_auto_tune_arrays(df["high"].values, df["low"].values, df["close"].values, tf))
    vol = df["volume"].values if "volume" in df.columns else np.ones(n)
    out.update(candle_ha_relvol_arrays(df["open"].values, df["high"].values, df["low"].values, df["close"].values, vol, tf))
    out.update(divergence_flag_arrays(df, tf))
    out.update(atr_rank_live(df["high"].values, df["low"].values, tf))
    out.update(adx_live(df, tf))
    if built is not None and f"dc_high_{tf}" in built and f"dc_low_{tf}" in built:
        out.update(dc_width_live(built[f"dc_high_{tf}"], built[f"dc_low_{tf}"], tf))
    return out


def bb_fixed_arrays(close: np.ndarray, tf: str, length: int = 20, mult: float = 2.0) -> Dict[str, np.ndarray]:
    """stocks live (tradier_indicators ~1964): bb_features(close, 20, 2.0) per bar — fixed sigma, %B clipped to [0,1] (unrounded),
    bb_width = round((u-l)/mid*100, 3). The builder used bb_auto_tune with a FULL-SERIES sigma (look-ahead and the wrong method for stocks)."""
    c = pd.Series(np.asarray(close, dtype=np.float64))
    mid = c.rolling(length).mean().to_numpy()
    sd = c.rolling(length).std(ddof=0).to_numpy()
    up = mid + mult * sd
    lo = mid - mult * sd
    bw = up - lo
    cv = c.to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        pb = np.where(bw > 0, (cv - lo) / bw, 0.5)
        pb = np.clip(pb, 0.0, 1.0)
        m2 = np.where((up + lo) > 0, (up + lo) / 2.0, 0.0)
        width = np.where(m2 > 0, np.round((up - lo) / m2 * 100.0, 3), 0.0)
    return {f"bb_upper_{tf}": up.astype(np.float32), f"bb_lower_{tf}": lo.astype(np.float32), f"bb_pct_b_{tf}": pb.astype(np.float32),
            f"bb_width_{tf}": width.astype(np.float32)}


def stock_overrides(df: pd.DataFrame, tf: str, built: Dict[str, np.ndarray] = None) -> Dict[str, np.ndarray]:
    """all live-twin arrays for one STOCK TF frame (tradier_indicators semantics; builder hook when MODE == 'tradier').
    Live tradier emits no wt_crossover_value_* (crypto-only field) -> builder values kept for those names."""
    from tradier_indicators import wavetrend as _tr_wt
    out: Dict[str, np.ndarray] = {}
    n = len(df)
    w1s, w2s = _tr_wt(df, timeframe=tf)
    w1 = None
    if w1s is not None and len(w1s) == n:
        w1 = w1s.values
        wi = wt_intelligence_arrays(w1s.values, w2s.values, df["close"].values, df["high"].values, df["low"].values, tf)
        for k in (f"wt_crossover_value_{tf}", f"wt_crossover_value_{tf}_prev"):
            wi.pop(k, None)
        out.update(wi)
    if tf in ("5m", "15m", "1h", "4h", "D"):
        out.update(bb_fixed_arrays(df["close"].values, tf))
        out.update(adx_live(df, tf, tfs=("5m", "15m", "1h", "4h", "D")))
    vol = df["volume"].values if "volume" in df.columns else np.ones(n)
    out.update(candle_ha_relvol_arrays(df["open"].values, df["high"].values, df["low"].values, df["close"].values, vol, tf))
    out.update(divergence_flag_arrays(df, tf, wt1=w1))
    out.update(atr_rank_live(df["high"].values, df["low"].values, tf))
    if built is not None and f"dc_high_{tf}" in built and f"dc_low_{tf}" in built:
        out.update(dc_width_live(built[f"dc_high_{tf}"], built[f"dc_low_{tf}"], tf))
    return out
