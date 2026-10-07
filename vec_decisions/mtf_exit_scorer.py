"""Faithful vectorized twin of ez_manage.evaluate_multi_tf_exit (ez_manage.py:40370-40572,
identical to tradier_manage.py:18191).

This module ports the GAIN-INDEPENDENT scoring of the live multi-TF technical exit into a
vectorized form for the v12_quick sweep engine. It is a live-faithful twin, NOT a proxy:
every component reproduces the exact point values, >= thresholds, TF sets, and cfg gates of
the live scalar function. The gain multipliers (live 40480-81), the MIN_EXIT_GAIN zeroing,
the min(score,100) cap, and the gain-dependent threshold (35/45/55) are LEFT TO THE CALLER
because they depend on the per-bar live_pnl_pct (see the hook snippet at the bottom).

CRITICAL FAITHFULNESS NOTE — MULTIPLIER SPLIT (live 40480-81):
  Live builds the score in two phases:
    phase A (comps 1-9, always-on + WT_DIV_EXIT) -> `base`
    then at 40480-81 multiplies ONLY that running total:
        if gain>3.0 and base>=20: base *= 1.3
        elif gain>1.0 and base>=25: base *= 1.2
    then phase B (comps 11-16, all six default-OFF switch-gated) -> `late`, added AFTER,
    UN-multiplied.
  Therefore the multiplier target is `base`, NOT `base+late`. `score_array_parts` returns
  (base, late, ...) so a caller can reproduce live EXACTLY. `score_array` returns the plain
  sum (base+late) per the requested signature; with default config all six phase-B switches
  are OFF so late==0 and the two agree bit-for-bit. They diverge only when a phase-B switch is
  toggled ON *and* the gain multiplier fires — in that case use `score_array_parts`.

KEY REMAPS (live key ABSENT from NPZ -> real NPZ equivalent, confirmed in
backtest_v8_precompute.py; NOT proxies):
  * WT_DIV_EXIT: live reads wt_peak_value_{tf}/wt_trough_value_{tf} (ABSENT) -> real keys
    wt_peak_{tf}/wt_trough_{tf} (precompute 1188-1189). [per task instruction]
  * STRUCT ha_green_{tf} (ABSENT) -> ha_{tf} (precompute 1256: int8, 1=green/-1=red/0=none).
    Mapped ha_green := 1.0 where ha_{tf}==1 else 0.0, then live compare (<0.5 long / >0.5 short).
    Live-on-NPZ would have read the missing key -> const default 0.5 -> this sub-condition
    NEVER contributes; using ha_{tf} makes it work as live INTENDS. [noted]
  * WT_15M_LH_WAIT: live compares str(wt_peak_structure_15m)=='LH' / wt_trough_structure_15m=='LL'.
    NPZ stores int8 (precompute 1197-1204): peak -1=LH/+1=HH, trough -1=LL/+1=HL. Use == -1.
    Live-on-NPZ str(int8) would be '-1' (never 'LH') so live's 'LH' branch is dead on NPZ and
    its '-1' branch adds +0 (40518-25, a no-op); using == -1 makes it work as INTENDED. [noted]
  * WT_DIVERGENCE_VV: live already handles int (_div_int==-1 BEAR / ==1 BULL); NPZ int8
    (precompute 1206-1211: -1=BEAR/+1=BULL) matches directly.

`g(k,d)` LIVE QUIRK REPRODUCED: live uses g=lambda k,d: float(i.get(k,d) or d). The `or d`
means a stored 0.0 is replaced by the default d (e.g. dc_position 0.0 -> 0.5). `gv()` below
reproduces this exactly (np.where(raw==0.0, default, raw)). Structure/divergence fields are
read raw (no quirk) because live reads them via i.get()/str(), not via g().
"""
import numpy as np


def _cfg(cfg, key, default):
    try:
        return getattr(cfg, key, default)
    except Exception:
        return default


def _make_gv(npz, n, _safe):
    def gv(key, default=0.0):
        raw = _safe(npz, key, n, 0.0)
        if isinstance(default, np.ndarray):
            darr = default.astype(np.float64)
        else:
            darr = np.full(n, float(default), dtype=np.float64)
        return np.where(raw == 0.0, darr, raw)
    return gv


def score_array_parts(npz, n, is_long, cfg, _safe, close):
    """Faithful split port. Returns (base, late, min_exit_gain, wt_tfs_override):
      base  = float64[n]  comps 1-9 (WT_DIV_EXIT + always-on) -> the multiplier target (40480-81)
      late  = float64[n]  comps 11-16 (six default-OFF switch-gated), added AFTER the multiplier
      min_exit_gain   = float  (MIN_EXIT_GAIN_PCT)
      wt_tfs_override = bool[n] (WT_EXIT_TFS override, 40555-69)
    NO cap, NO multiplier, NO threshold applied here (caller does, per-bar with live_pnl_pct)."""
    close = np.asarray(close, dtype=np.float64)
    gv = _make_gv(npz, n, _safe)
    base = np.zeros(n, dtype=np.float64)
    late = np.zeros(n, dtype=np.float64)

    # --- 1) WT_DIV_EXIT (gated WT_DIV_EXIT_ENABLED, default False) live 40382-40393 ---
    if bool(_cfg(cfg, 'WT_DIV_EXIT_ENABLED', False)):
        for tf, w in (('1h', 6.0), ('4h', 10.0), ('D', 14.0)):
            wt1 = gv(f'wt1_{tf}', 0.0)
            close_now = gv(f'close_{tf}', close)          # live default = current_price
            close_prev = gv(f'close_{tf}_prev', 0.0)
            if is_long:
                pk = _safe(npz, f'wt_peak_{tf}', n, 0.0)  # remap: wt_peak_value_{tf} -> wt_peak_{tf}
                cond = (pk != 0.0) & (close_prev > 0) & (wt1 < pk * 0.85) & (close_now >= close_prev)
            else:
                tr = _safe(npz, f'wt_trough_{tf}', n, 0.0)
                cond = (tr != 0.0) & (close_prev > 0) & (wt1 > tr * 0.85) & (close_now <= close_prev)
            base += w * cond.astype(np.float64)

    # --- 2) DC_FADE (always-on) live 40394-40400 ---
    for tf, w in (('15m', 4.0), ('1h', 7.0), ('4h', 10.0)):
        dcp = gv(f'dc_position_{tf}', 0.5)
        cond = (dcp < 0.35) if is_long else (dcp > 0.65)
        base += w * cond.astype(np.float64)

    # --- 3) DC_BREAK / DC_BREAK_WAIT (wait gated DC_BREAK_WAIT_WT15_CLOSE_ENABLED, def False) 40401-40422 ---
    dc_wait = bool(_cfg(cfg, 'DC_BREAK_WAIT_WT15_CLOSE_ENABLED', False))
    dcl = gv('dc_low_15m', 0.0)
    dch = gv('dc_high_15m', 0.0)
    wt1_15 = gv('wt1_15m', 0.0)
    wt2_15 = gv('wt2_15m', 0.0)
    if is_long:
        brk = (dcl > 0) & (close < dcl)
        if dc_wait:
            brk = brk & (wt1_15 < wt2_15)   # WAIT: only score on wt-close-long; else blocked (0)
        base += 10.0 * brk.astype(np.float64)
    else:
        brk = (dch > 0) & (close > dch)
        if dc_wait:
            brk = brk & (wt1_15 > wt2_15)
        base += 10.0 * brk.astype(np.float64)

    # --- 4) VEL_DECEL (always-on) live 40423-40432; 5m -> 15m ---
    vel_count = np.zeros(n, dtype=np.float64)
    for tf, thr in (('5m', 1.5), ('15m', 1.0), ('1h', 0.5), ('4h', 0.3)):
        tk = '15m' if tf == '5m' else tf
        vel = gv(f'wt_velocity_{tk}', 0.0)
        cond = (vel < -thr) if is_long else (vel > thr)
        vel_count += cond.astype(np.float64)
    base += np.where(vel_count >= 2, vel_count * 5.0, 0.0)

    # --- 5) STRUCT_BREAK / STRUCT_WEAK (always-on) live 40433-40452; tfs incl 5m -> 15m ---
    struct_total = np.zeros(n, dtype=np.float64)
    for tf in ('5m', '15m', '1h', '4h', 'D'):
        tk = '15m' if tf == '5m' else tf
        loc = np.zeros(n, dtype=np.float64)
        wt1 = gv(f'wt1_{tk}', 0.0)
        wt2 = gv(f'wt2_{tk}', 0.0)
        loc += ((wt1 < wt2) if is_long else (wt1 > wt2)).astype(np.float64)
        kk = gv(f'k_{tk}', 50.0)
        dd = gv(f'd_{tk}', 50.0)
        loc += ((kk < dd) if is_long else (kk > dd)).astype(np.float64)
        dcp = gv(f'dc_position_{tk}', 0.5)
        loc += ((dcp < 0.3) if is_long else (dcp > 0.7)).astype(np.float64)
        hc = _safe(npz, f'ha_{tk}', n, 0.0)               # remap: ha_green_{tf} -> ha_{tf}
        ha = np.where(hc == 1.0, 1.0, 0.0)
        loc += ((ha < 0.5) if is_long else (ha > 0.5)).astype(np.float64)
        struct_total += loc
    base += np.where(struct_total >= 12, 20.0, np.where(struct_total >= 9, 10.0, 0.0))

    # --- 6) BB_REJ (always-on) live 40453-40459 ---
    for tf, w in (('15m', 4.0), ('1h', 6.0), ('4h', 8.0)):
        bb = gv(f'bb_pct_b_{tf}', 0.5)
        cond = (bb < 0.3) if is_long else (bb > 0.7)
        base += w * cond.astype(np.float64)

    # --- 7) STOCH_CROSS (always-on) live 40460-40467; 5m -> 15m ---
    stoch_count = np.zeros(n, dtype=np.float64)
    for tf in ('5m', '15m', '1h'):
        tk = '15m' if tf == '5m' else tf
        kk = gv(f'k_{tk}', 50.0)
        kp = gv(f'k_{tk}_prev', 50.0)
        dd = gv(f'd_{tk}', 50.0)
        dp = gv(f'd_{tk}_prev', 50.0)
        if is_long:
            cond = (kp >= dp) & (kk < dd)
        else:
            cond = (kp <= dp) & (kk > dd)
        stoch_count += cond.astype(np.float64)
    base += np.where(stoch_count >= 2, stoch_count * 4.0, 0.0)

    # --- 8) MFI_EXHST (always-on) live 40468-40472 ---
    mfi1 = gv('mfi_1h', 50.0)
    mfi4 = gv('mfi_4h', 50.0)
    if is_long:
        cond = (mfi1 > 80) & (mfi4 > 70)
    else:
        cond = (mfi1 < 20) & (mfi4 < 30)
    base += 6.0 * cond.astype(np.float64)

    # --- 9) D_LL/HH (always-on) live 40473-40479 ---
    dh = gv('high_D', 0.0)
    dl = gv('low_D', 0.0)
    dhp = gv('high_D_prev', 0.0)
    dlp = gv('low_D_prev', 0.0)
    allpos = (dh > 0) & (dhp > 0) & (dl > 0) & (dlp > 0)
    if is_long:
        cond = allpos & (dh < dhp) & (dl < dlp)
    else:
        cond = allpos & (dh > dhp) & (dl > dlp)
    base += 8.0 * cond.astype(np.float64)

    # ===== live 40480-81 gain multipliers apply to `base` ONLY — done by caller =====

    # --- 11) WT_HTF_DISCOUNT (gated, default False) live 40482-40485 ---
    if bool(_cfg(cfg, 'WT_HTF_DISCOUNT_ENABLED', False)):
        cnt = np.zeros(n, dtype=np.float64)
        for tf in ('1h', '4h', 'D'):
            wt1 = gv(f'wt1_{tf}', 0.0)
            wt2 = gv(f'wt2_{tf}', 0.0)
            cond = (wt1 < wt2) if is_long else (wt1 > wt2)
            cnt += cond.astype(np.float64)
        late += 5.0 * (cnt >= 2).astype(np.float64)

    # --- 12) WT_ACCEL_EXIT (gated, default False) live 40486-40490; 5m -> 15m ---
    if bool(_cfg(cfg, 'WT_ACCEL_EXIT_ENABLED', False)):
        accel_min = int(_cfg(cfg, 'WT_ACCEL_EXIT_MIN_TFS', 2))
        cnt = np.zeros(n, dtype=np.float64)
        _is_trd = str(_cfg(cfg, 'MODE', 'crypto')) == 'tradier'
        for tf in ('5m', '15m', '1h'):
            if tf == '5m' and not _is_trd:
                continue  # lane-D 2026-10-06: crypto live has no 5m feed (ez_manage:41823 g('wt_velocity_5m') -> 0, never counts); no 15m remap
            tk = '15m' if tf == '5m' else tf
            vel = gv(f'wt_velocity_{tk}', 0.0)
            cond = (vel < -1.0) if is_long else (vel > 1.0)
            cnt += cond.astype(np.float64)
        late += np.where(cnt >= accel_min, cnt * 4.0, 0.0)

    # --- 13) WT_MOMENTUM_EXIT (gated, default False) live 40491-40495 ---
    if bool(_cfg(cfg, 'WT_MOMENTUM_EXIT_ENABLED', False)):
        mom_thr = float(_cfg(cfg, 'WT_MOMENTUM_EXIT_THRESHOLD', 20))
        cnt = np.zeros(n, dtype=np.float64)
        for tf in ('15m', '1h', '4h'):
            wt1 = gv(f'wt1_{tf}', 0.0)
            cond = (wt1 < -mom_thr) if is_long else (wt1 > mom_thr)
            cnt += cond.astype(np.float64)
        late += 6.0 * (cnt >= 2).astype(np.float64)

    # --- 14) WT_EXHAUST (gated by nonzero WT_EXHAUST_EXIT_MIN_TFS, default 0) live 40496-40500 ---
    exh_gate = _cfg(cfg, 'WT_EXHAUST_EXIT_MIN_TFS', 0)
    if exh_gate:
        exh_min = int(exh_gate)
        cnt = np.zeros(n, dtype=np.float64)
        for tf in ('15m', '1h', '4h'):
            wt1 = gv(f'wt1_{tf}', 0.0)
            cond = np.abs(wt1) > 80                       # live: same test both sides
            cnt += cond.astype(np.float64)
        late += 5.0 * (cnt >= exh_min).astype(np.float64)

    # --- 15) WT_15M_LH_WAIT (gated, default False) live 40501-40527; str 'LH'/'LL' -> int8 -1 ---
    if bool(_cfg(cfg, 'WT_15M_LH_WAIT_EXIT_ENABLED', False)):
        w1 = gv('wt1_15m', 0.0)
        w2 = gv('wt2_15m', 0.0)
        w1p = gv('wt1_15m_prev', w1)   # absent in NPZ -> defaults to current, exactly as live
        w2p = gv('wt2_15m_prev', w2)
        dcp = gv('dc_position_15m', 0.5)
        bb = gv('bb_pct_b_15m', 0.5)
        if is_long:
            pk_s = _safe(npz, 'wt_peak_structure_15m', n, 0.0)
            cross = (w1 < w2) & (w1p >= w2p)
            wait_ok = (dcp > 0.65) | (bb > 0.70) | cross
            cond = (pk_s == -1.0) & wait_ok
        else:
            tr_s = _safe(npz, 'wt_trough_structure_15m', n, 0.0)
            cross = (w1 > w2) & (w1p <= w2p)
            wait_ok = (dcp < 0.35) | (bb < 0.30) | cross
            cond = (tr_s == -1.0) & wait_ok
        late += 18.0 * cond.astype(np.float64)

    # --- 16) WT_DIVERGENCE_VV_SHORT (gated, default False) live 40528-40547; int8 -1=BEAR/+1=BULL ---
    if bool(_cfg(cfg, 'WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED', False)):
        div = _safe(npz, 'wt_divergence_15m', n, 0.0)
        w1 = gv('wt1_15m', 0.0)
        w2 = gv('wt2_15m', 0.0)
        w1p = gv('wt1_15m_prev', w1)
        w2p = gv('wt2_15m_prev', w2)
        dcp = gv('dc_position_15m', 0.5)
        bb = gv('bb_pct_b_15m', 0.5)
        if is_long:
            cross = (w1 < w2) & (w1p >= w2p)
            wait_ok = (dcp > 0.65) | (bb > 0.70) | cross
            cond = (div == -1.0) & wait_ok
        else:
            cross = (w1 > w2) & (w1p <= w2p)
            wait_ok = (dcp < 0.35) | (bb < 0.30) | cross
            cond = (div == 1.0) & wait_ok
        late += 22.0 * cond.astype(np.float64)

    # --- MIN_EXIT_GAIN_PCT (live 40548-40550) — caller zeroes score where gain < this ---
    min_exit_gain = float(_cfg(cfg, 'MIN_EXIT_GAIN_PCT', 0))

    # --- WT_EXIT_TFS override (live 40555-40569); 5m/3m -> 15m ---
    tfs_str = str(_cfg(cfg, 'TRADIER_WT_EXIT_TFS_TRADIER', '5m+15m+1h+4h+D'))
    wt_min = int(_cfg(cfg, 'TRADIER_WT_EXIT_MIN_TFS_TRADIER', 4))
    wt_parts = [t.strip() for t in tfs_str.replace('+', ',').split(',') if t.strip()]
    against = np.zeros(n, dtype=np.float64)
    for tf in wt_parts:
        tk = '15m' if tf in ('5m', '3m') else tf
        wt1 = gv(f'wt1_{tk}', 0.0)
        wt2 = gv(f'wt2_{tk}', 0.0)
        cond = (wt1 < wt2) if is_long else (wt1 > wt2)
        against += cond.astype(np.float64)
    if wt_min > 0 and len(wt_parts) > 0:
        wt_tfs_override = against >= wt_min
    else:
        wt_tfs_override = np.zeros(n, dtype=bool)

    return base, late, min_exit_gain, wt_tfs_override


def score_array(npz, n, is_long, cfg, _safe, close):
    """Requested signature. Returns (score, min_exit_gain, wt_tfs_override):
      score           = float64[n] = base + late (sum of ALL components, NO multiplier, NO cap)
      min_exit_gain   = float (MIN_EXIT_GAIN_PCT) — caller zeroes score where gain<min_exit_gain
      wt_tfs_override = bool[n] (WT_EXIT_TFS override)
    NOTE: for bit-exact parity when a phase-B switch is ON, use score_array_parts and apply the
    40480-81 multiplier to `base` ONLY (see hook snippet in module docstring / report)."""
    base, late, min_exit_gain, override = score_array_parts(npz, n, is_long, cfg, _safe, close)
    return base + late, min_exit_gain, override
