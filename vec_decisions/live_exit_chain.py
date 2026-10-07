# -*- coding: utf-8 -*-
"""live_exit_chain (Agent C wave 2, batch b2c, 2026-10-01) — numpy/stateful twin of the LIVE CRYPTO technical-exit chain
ez_positions_quick.check_exit_candidates_for_account -> process_single_exit, lines 14109-14830 (the code that decides hard_exit_reason, then
STALE_PRICE_ABORT (14720-14735) and the CLOSE-vs-REDUCE split (14770-14800)).

Live semantics mirrored (precedence = code order; first hit wins):
  PARABOLIC_EXIT (14141) · STRUCTURAL_RANGE_SHIFT_EXIT (14172) · KEY_LEVEL_CRASH sev>=3 (14283) · BREAKEVEN_GAIN_EROSION (14544, config OFF)
  · DC4/DC LOW|HIGH 3M GAIN_EROSION_STOP (BREAKEVEN_DC_LOW4_ENABLED, 14571; HTF_EXIT_VETO skips it) · WT_CROSS_EXIT (14602)
  · STDEV_REJECT_EXIT (14641, config OFF) · PEAK_GIVEBACK_PROTECTION (14652) · AUGMENTED_DC_BREAK_REDUCE_TO_MIN (14696)
Post-chain: STALE_PRICE_ABORT (fresh price == bar price in the sim): a hit with gain < -0.01 is dropped unless the reason contains BREAKEVEN / DC_LOW4_3M /
DC_HIGH4_3M / PEAK_GIVEBACK or (LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK) WT_CROSS_EXIT / STDEV_BREAKOUT / STRUCTURAL_RANGE_SHIFT / PARABOLIC_EXIT.
CLOSE-vs-REDUCE: reason containing CLOSE|STOP|KILL = full close; else REDUCE of 15%/25% (0<=gain<1: <0.5 / >=0.5), 50% (1<=gain<3), else all-but-dust.
Not modeled (documented, NOT fabricated): K1M_EXTREME_REVERSE (needs k_1m, no 1m array; default OFF), TREND_REVERSAL_EXIT (TREND_ACCOUNTS=[] in config),
STDEV_BREAKOUT_EXIT (default OFF), KEY_LEVEL severity-2 HEDGE, key-level monitor signal file, hedge states, delta_tracker bull_accel in PARABOLIC (assumed True).
USER ORDER 2026-10-01 (b2c2): NO 3m/1m/5m data in the vector system. Every 3m/1m-dependent term is IGNORED (switch stays, exit is INERT in vec: 'needs 3m, ignored') — NOT approximated.
INERT in vec (need 3m): PARABOLIC_EXIT, STRUCTURAL_RANGE_SHIFT_EXIT (its cascade requires the 3m WT cross), DC4/DC LOW|HIGH 3M stop (BREAKEVEN_DC_LOW4_ENABLED),
AUGMENTED_DC_BREAK_REDUCE_TO_MIN, DC_BASIS_3M_REDUCE, MOMENTUM_TP branches B/C/D/E/F/G, R1_DC_LOW4_3M_EMERGENCY (also forced OFF by the user), K1M_EXTREME_REVERSE.
LIVE for 15m+: WT_CROSS_EXIT (3m veto ignored = no suppression), KEY_LEVEL_CRASH, PEAK_GIVEBACK, HTF_EXIT_VETO, BREAKEVEN_GAIN_EROSION, STDEV_REJECT, R2, MOMENTUM_TP branch A.
(historic b2c text below describes the removed 15m floors and is kept only as record)
3m FLOORS (REMOVED in b2c2) (the NPZ has NO 3m/1m arrays and no 3m kline history older than ~3.4 days exists anywhere): every '3m' structure was mapped to the nearest 15m quantity:
  dc_low4_3m/dc_high4_3m (4x3m = 12 min channel)  -> low_15m_prev / high_15m_prev (prior 15m bar extreme, 15 min window)
  dc_low_3m/dc_high_3m  (20x3m = 60 min channel)  -> dc_low4_15m / dc_high4_15m shifted by one bar (4x15m = 60 min, prior bars only)
  low_3m < low_3m_prev (3m structure crack)       -> low_15m < low_15m_prev
  wt 3m cross                                     -> wt 15m (WT_CROSS_EXIT 3m veto becomes inactive: a 15m WT cannot be 'with' the position while the 15m confirm is 'against')
If an NPZ ever carries the real *_3m arrays they are preferred automatically (key names dc_low4_3m, dc_high4_3m, dc_low_3m, dc_high_3m, low_3m, low_3m_prev, wt1_3m, wt2_3m).
Prior-bar convention: NPZ dc_* arrays INCLUDE the current bar, so close < dc_low[i] is impossible (measured 0.0000 on ZENUSDT, 0.7% for <=); breach tests therefore use the previous bar's channel
(same as live, where the indicator is a cached snapshot and the tick price moves below it)."""
import numpy as np

_BAND_TF = {"dc_1h": ("dc_high_1h", "dc_low_1h"), "dc_4h": ("dc_high_4h", "dc_low_4h"), "dc_D": ("dc_high_D", "dc_low_D"),
            "bb_1h": ("bb_upper_1h", "bb_lower_1h"), "bb_4h": ("bb_upper_4h", "bb_lower_4h"), "bb_D": ("bb_upper_D", "bb_lower_D")}


def _f(cfg, k, d):
    v = getattr(cfg, k, d)
    try:
        return float(v)
    except Exception:
        return float(d)


def _b(cfg, k, d):
    v = getattr(cfg, k, d)
    return bool(d) if v is None else bool(v)


def _shift(a):
    out = np.empty_like(a)
    out[0] = a[0]
    out[1:] = a[:-1]
    return out


def prepare(npz, n, is_long, cfg, close, safe):
    P = {"n": n, "is_long": is_long, "floors": []}
    has = lambda k: (k in npz) and getattr(npz.get(k), "__len__", None) is not None and len(npz.get(k)) == n
    # --- 3m structures: IGNORED (b2c2). Arrays kept all-zero so every 3m-dependent exit is inert. ---
    z = np.zeros(n)
    P["dc4_lvl"] = z
    P["dc20_lvl"] = z
    P["crack"] = np.zeros(n, dtype=bool)
    P["wt3_1"], P["wt3_2"] = z, z
    P["wt3_is_floor"] = False
    w = {tf: (safe(npz, f"wt1_{tf}", n, 0.0), safe(npz, f"wt2_{tf}", n, 0.0)) for tf in ("15m", "1h", "4h", "D")}
    P["w"] = w
    P["k15"], P["k15p"] = safe(npz, "k_15m", n, 50.0), safe(npz, "k_15m_prev", n, 50.0)
    P["k1h"], P["k1hp"] = safe(npz, "k_1h", n, 50.0), safe(npz, "k_1h_prev", n, 50.0)
    # --- HTF veto (live 14520/14545): >= MIN_ALIGNED of {1h,4h,D} WT with the position and |gain| <= MAX_LOSS ---
    al = np.zeros(n, dtype=np.int8)
    for tf in ("1h", "4h", "D"):
        a, b = w[tf]
        al = al + ((a > b) if is_long else (a < b)).astype(np.int8)
    P["htf_aligned"] = al
    # --- parabolic ---
    P["para_lvl"] = np.zeros(n)
    # --- SRS band levels ---
    tf = str(getattr(cfg, "STRUCTURAL_RANGE_SHIFT_TF", "dc_4h") or "dc_4h")
    hk, lk = _BAND_TF.get(tf, ("dc_high_4h", "dc_low_4h"))
    P["srs_tf"] = tf
    P["srs_hi"], P["srs_lo"] = safe(npz, hk, n, 0.0), safe(npz, lk, n, 0.0)
    # --- key level crash: prior-bar channels of 15m/1h/4h/D ---
    kl = np.zeros(n, dtype=np.int8)
    for t in ("15m", "1h", "4h", "D"):
        lv = safe(npz, f"dc_low_{t}_prev" if is_long else f"dc_high_{t}_prev", n, 0.0)
        kl = kl + (((close < lv) if is_long else (close > lv)) & (lv > 0)).astype(np.int8)
    P["keylevel_sev"] = kl
    # --- stdev reject ---
    tfr = str(getattr(cfg, "STDEV_REJECT_EXIT_TF", "D") or "D")
    P["pb"] = safe(npz, f"bb_pct_b_{tfr}", n, 0.5)
    P["pbp"] = _shift(P["pb"])
    P["vel1h"] = safe(npz, "wt_velocity_1h", n, 0.0)
    P["close"] = np.asarray(close, dtype=float)
    return P


def step(P, cfg, i, px, st):
    """st: gain(%), peak(%), age_min, entry_price, reentered(bool), was_augmented(bool). Returns None or (reason, is_full_close)."""
    is_long = P["is_long"]
    gain, peak, age = float(st["gain"]), float(st["peak"]), float(st["age_min"])
    grace_m = _f(cfg, "REENTRY_GRACE_MINUTES", 30.0) if st.get("reentered") else 3.0
    in_grace = (age < grace_m) and not st.get("was_augmented", False)
    reason = None
    # 1. PARABOLIC_EXIT (no switch in live — always on)
    if not in_grace and _b(cfg, "PARABOLIC_EXIT_ENABLED", True):
        k15, lvl = P["k15"][i], P["para_lvl"][i]
        if is_long and k15 > 90 and lvl > 0 and px > lvl and P["crack"][i]:
            reason = f"PARABOLIC_EXIT_LONG_k15={k15:.0f}"
        elif (not is_long) and k15 < 10 and lvl > 0 and px < lvl and P["crack"][i]:
            reason = f"PARABOLIC_EXIT_SHORT_k15={k15:.0f}"
    # 2. STRUCTURAL_RANGE_SHIFT_EXIT
    if False and reason is None and not in_grace and _b(cfg, "STRUCTURAL_RANGE_SHIFT_EXIT", False):   # needs the 3m WT cross -> ignored
        entry, hi, lo = float(st.get("entry_price", 0.0)), P["srs_hi"][i], P["srs_lo"][i]
        if entry > 0 and hi > 0 and lo > 0:
            band = _f(cfg, "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS", 100.0) / 10000.0
            khi, klo = _f(cfg, "STRUCTURAL_RANGE_SHIFT_K_HIGH", 75.0), _f(cfg, "STRUCTURAL_RANGE_SHIFT_K_LOW", 25.0)
            w1h, w15 = P["w"]["1h"], P["w"]["15m"]
            if is_long and entry > hi:
                ok = abs(px - hi) / hi <= band and P["k1h"][i] >= khi and P["k1h"][i] < P["k1hp"][i] and w1h[0][i] < w1h[1][i] \
                    and P["k15"][i] >= khi and P["k15"][i] < P["k15p"][i] and w15[0][i] < w15[1][i] and P["wt3_1"][i] < P["wt3_2"][i]
                if ok:
                    reason = f"STRUCTURAL_RANGE_SHIFT_LONG_CASCADE_{P['srs_tf']}"
            elif (not is_long) and entry > lo:
                ok = abs(px - lo) / lo <= band and P["k1h"][i] <= klo and P["k1h"][i] > P["k1hp"][i] and w1h[0][i] > w1h[1][i] \
                    and P["k15"][i] <= klo and P["k15"][i] > P["k15p"][i] and w15[0][i] > w15[1][i] and P["wt3_1"][i] > P["wt3_2"][i]
                if ok:
                    reason = f"STRUCTURAL_RANGE_SHIFT_SHORT_CASCADE_{P['srs_tf']}"
    # 3. KEY_LEVEL_CRASH (severity >= 3 -> close/reduce; severity 2 = hedge, not modeled). STRICT_NO_LOSS_ACCOUNTS is empty in config.
    if reason is None and _b(cfg, "KEY_LEVEL_CRASH_ENABLED", True) and P["keylevel_sev"][i] >= 3:
        reason = f"KEY_LEVEL_{'CRASH' if is_long else 'BREAKOUT'}_S{int(P['keylevel_sev'][i])}_DC_{'LOW' if is_long else 'HIGH'}_BROKEN"
    # HTF veto (live 14520)
    htf_veto = False
    if _b(cfg, "HTF_EXIT_VETO_ENABLED", True) and abs(gain) <= _f(cfg, "HTF_EXIT_VETO_MAX_LOSS_PCT", 2.0):
        htf_veto = P["htf_aligned"][i] >= int(_f(cfg, "HTF_EXIT_VETO_MIN_ALIGNED", 2))
    # 4. BREAKEVEN_GAIN_EROSION (config False)
    if reason is None and not in_grace and _b(cfg, "BREAKEVEN_GAIN_EROSION_ENABLED", False):
        req = _b(cfg, "BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT", True)
        comm = _f(cfg, "COMMISSION_BUFFER_PCT", 0.10)
        mn = _f(cfg, "BREAKEVEN_GAIN_EROSION_MIN_GAIN", comm)
        up = max(mn + 0.5, 0.02)
        win = (mn <= gain < up) if req else (gain < 0.02)
        trend_veto = _b(cfg, "TREND_REGIME_VETO_ENABLED", True) and P["htf_aligned"][i] == 3
        if age >= _f(cfg, "BREAKEVEN_GRACE_MINUTES", 15.0) and win and not trend_veto:
            hbf = _b(cfg, "HARD_BREAKEVEN_FLOOR_ENABLED", True) and peak >= _f(cfg, "HARD_BREAKEVEN_MIN_PEAK_PCT", 0.5)
            if htf_veto and not hbf:
                pass
            elif _b(cfg, "HARD_BREAKEVEN_FLOOR_ENABLED", True) and peak < _f(cfg, "HARD_BREAKEVEN_MIN_PEAK_PCT", 0.5):
                pass
            else:
                reason = "BREAKEVEN_GAIN_EROSION_STOP"
    # 5. DC4 / DC structural stop (default ON)
    if reason is None and not in_grace and _b(cfg, "BREAKEVEN_DC_LOW4_ENABLED", True):
        mode = str(getattr(cfg, "BREAKEVEN_DC_FIELD_MODE", "DC4") or "DC4").upper()
        lvl = P["dc20_lvl"][i] if mode == "DC" else P["dc4_lvl"][i]
        if lvl > 0 and ((is_long and px < lvl) or ((not is_long) and px > lvl)) and not htf_veto:
            tag = "DC" if mode == "DC" else "DC4"
            reason = f"{tag}_{'LOW' if is_long else 'HIGH'}_3M_GAIN_EROSION_STOP"
    # 6. WT_CROSS_EXIT (default ON) — live requires position.gain > 0.015
    if reason is None and not in_grace and _b(cfg, "WT_CROSS_EXIT_ENABLED", True) and gain > 0.015:
        losers, winners = _b(cfg, "WT_CROSS_EXIT_APPLIES_TO_LOSERS", True), _b(cfg, "WT_CROSS_EXIT_APPLIES_TO_WINNERS", True)
        if age >= _f(cfg, "WT_CROSS_EXIT_MIN_AGE_MINUTES", 2.0) and ((losers and gain < 0) or (winners and gain >= 0)):
            a1, b1 = P["w"]["1h"][0][i], P["w"]["1h"][1][i]
            a15, b15 = P["w"]["15m"][0][i], P["w"]["15m"][1][i]
            if a1 != 0 or b1 != 0:
                have15 = (a15 != 0 or b15 != 0)
                flipped = (a1 < b1) if is_long else (a1 > b1)
                conf = ((a15 < b15) if is_long else (a15 > b15)) if have15 else True
                if flipped and (not _b(cfg, "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True) or conf):
                    a3, b3 = P["wt3_1"][i], P["wt3_2"][i]
                    have3 = (a3 != 0 or b3 != 0)
                    with3 = have3 and ((a3 > b3) if is_long else (a3 < b3))
                    if not (with3 and age < _f(cfg, "WT_CROSS_EXIT_3M_VETO_MAX_AGE", 30.0)):
                        reason = f"WT_CROSS_EXIT_1h_{'bear' if is_long else 'bull'}"
    # 7. STDEV_REJECT_EXIT (config OFF)
    if reason is None and not in_grace and _b(cfg, "STDEV_REJECT_EXIT_ENABLED", False):
        zone, ret = _f(cfg, "STDEV_REJECT_EXIT_ZONE", 0.80), _f(cfg, "STDEV_REJECT_EXIT_RETURN", 0.65)
        pb, pbp, vel = P["pb"][i], P["pbp"][i], P["vel1h"][i]
        if (is_long and pbp >= zone and pb < ret and vel < 0) or ((not is_long) and pbp <= 1.0 - zone and pb > 1.0 - ret and vel > 0):
            reason = "STDEV_REJECT_EXIT"
    # 8. PEAK_GIVEBACK_PROTECTION (default ON, but hard-zero and drop-trigger are both OFF in config -> dormant at defaults)
    if reason is None and not in_grace and _b(cfg, "PEAK_GIVEBACK_PROTECTION_ENABLED", True):
        if peak >= _f(cfg, "PEAK_GIVEBACK_MIN_PEAK_PCT", 0.5):
            if _b(cfg, "PEAK_GIVEBACK_HARD_ZERO_ENABLED", True) and gain < 0.08:
                reason = "PEAK_GIVEBACK_GAIN_EROSION_STOP"
            elif _b(cfg, "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED", False) and gain < peak - _f(cfg, "PEAK_GIVEBACK_DROP_PCT", 1.0):
                reason = "PEAK_GIVEBACK_GAIN_EROSION_STOP"
    # 9. AUGMENTED_DC_BREAK_REDUCE_TO_MIN: live 'is_augmented' = positionAmt > 1.2 * (2*MIN_POSITION_SIZE/price) is true for any real position
    if reason is None and not in_grace and _b(cfg, "AUGMENTED_DC_BREAK_ENABLED", True):
        lvl = P["dc20_lvl"][i]
        if lvl > 0 and ((is_long and px < lvl) or ((not is_long) and px > lvl)) and gain > 0.1:
            reason = f"AUGMENTED_DC_BREAK_REDUCE_TO_MIN_{gain:.2f}%"
    if reason is None:
        return None
    # STALE_PRICE_ABORT (fresh price == bar price)
    if gain < -0.01 and not _b(cfg, "LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED", False):
        be_like = any(t in reason for t in ("BREAKEVEN", "DC_LOW4_3M", "DC_HIGH4_3M", "PEAK_GIVEBACK"))
        tech = any(t in reason for t in ("WT_CROSS_EXIT", "STDEV_BREAKOUT", "STRUCTURAL_RANGE_SHIFT", "PARABOLIC_EXIT")) and _b(cfg, "LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK", True)
        if not be_like and not tech:
            return None
    full = ("CLOSE" in reason) or ("STOP" in reason) or ("KILL" in reason)
    return reason, full


_R1_MARKERS = ("STRONG_BUY", "DC_BREAK", "BREAKOUT", "_BREAK_", "PARABOLIC", "MOMENTUM_RIDER", "TOR_BREAK", "WT_DC_HTF", "HIGH_BREAK", "PEAK_BREAK", "OBLIGATORY_OPEN")


def prepare_pp(npz, n, is_long, cfg, close, safe, P):
    """extra arrays for the ez_manage.process_position exits (R1 / R2 / MOMENTUM_TP / DC_BASIS_3M_REDUCE) — same 3m->15m floors as prepare()."""
    P["vel15"], P["acc15"] = safe(npz, "wt_velocity_15m", n, 0.0), safe(npz, "wt_acceleration_15m", n, 0.0)
    P["k15_"], P["k15p_"] = P["k15"], P["k15p"]
    P["lo15"], P["lo15p"] = safe(npz, "low_15m", n, 0.0), safe(npz, "low_15m_prev", n, 0.0)
    P["hi15"], P["hi15p"] = safe(npz, "high_15m", n, 0.0), safe(npz, "high_15m_prev", n, 0.0)
    P["dclow15"], P["dchigh15"] = safe(npz, "dc_low_15m", n, 0.0), safe(npz, "dc_high_15m", n, 0.0)
    return P


def step_pp(P, cfg, i, px, st, pos):
    """ez_manage.process_position exits, evaluated at bar i. Returns None or (reason, True) (all four are full closes in a sim: live REDUCE-of-whole-position == close).
    st: gain, peak, age_min, held_bars, min_hold, entry_reason."""
    is_long = P["is_long"]
    gain, peak = float(st["gain"]), float(st["peak"])
    # ---- R1_DC_LOW4_3M_EMERGENCY: USER 2026-10-01 must NOT run live nor vectorized -> not implemented (switch stays False everywhere) ----
    # ---- R2_WT_VEL_SLOW (47277-47380; config.py WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED=False -> sweepable, off at baseline) ----
    if _b(cfg, "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED", False):
        band, floor = _f(cfg, "WT_15M_VEL_SLOW_GAIN_BAND_PCT", 0.10), _f(cfg, "WT_15M_VEL_SLOW_GAIN_FLOOR_PCT", 0.01)
        if peak >= _f(cfg, "R2_PEAK_MIN_PCT", 0.5) and floor <= gain < band:
            v, a = P["vel15"][i], P["acc15"][i]          # R2_TF_LIST default ('15m',)
            vp = v - a
            against = (v < 0) if is_long else (v > 0)
            decel = abs(v) < abs(vp) * _f(cfg, "WT_VEL_DECEL_RATIO", 0.5) and abs(vp) > 1e-6
            dying = (not _b(cfg, "WT_VEL_USE_DECEL_RATIO_ONLY", True)) and abs(v) <= _f(cfg, "WT_15M_VEL_NEAR_ZERO_THRESHOLD", 0.1)
            if against and (decel or dying):
                wD1, wD2 = P["w"]["D"][0][i], P["w"]["D"][1][i]
                htf_ok = abs(wD1) > 1e-9 and abs(wD2) > 1e-9
                if not (htf_ok and ((wD1 > wD2) if is_long else (wD1 < wD2))):
                    return "R2_WT_VEL_SLOW", True
    # ---- MOMENTUM_TP (52282-52340): gain > 0.5; branches A (k15 exhaustion), B (OB lower low), D (hard drop below prev low); C/F need k_3m -> NOT modeled ----
    if _b(cfg, "MOMENTUM_TP_ENABLED", True) and gain > 0.5:
        k, kp = P["k15"][i], P["k15p"][i]
        floor_br = False   # branches B/C/D/E/F/G need 3m -> ignored
        if is_long:
            if k > 95 and k < kp:
                return "PROFIT_TP_EXHAUSTION", True
            if floor_br and k > 95 and P["lo15"][i] > 0 and P["lo15p"][i] > 0 and P["lo15"][i] < P["lo15p"][i]:
                return "OB_LOWER_LOW", True
            if floor_br and P["lo15p"][i] > 0 and px < P["lo15p"][i]:
                return "HARD_DROP_BELOW_PREV_LOW", True
        else:
            if k < 5 and k > kp:
                return "PROFIT_TP_EXHAUSTION", True
            if floor_br and k < 5 and P["lo15"][i] > 0 and P["lo15p"][i] > 0 and P["lo15"][i] > P["lo15p"][i]:
                return "OS_HIGHER_LOW", True
            if floor_br and P["hi15p"][i] > 0 and px > P["hi15p"][i]:
                return "HARD_RISE_ABOVE_PREV_HIGH", True
    # ---- DC_BASIS_3M_REDUCE (51823-51890): price through dc_*_3m extreme; _check_loss_protection blocks gain in [-0.7, 0.12] (ENABLE_LOSS_PROTECTION=True) ----
    if _b(cfg, "DC_BASIS_3M_REDUCE_ENABLED", True) and not _b(cfg, "HEDGE_MODE", False):
        lvl = P["dc20_lvl"][i]
        if lvl > 0 and ((is_long and px < lvl) or ((not is_long) and px > lvl)):
            lp_thr = _f(cfg, "NEW_POSITION_MAX_LOSS_THRESHOLD", -0.7)
            blocked = (gain >= lp_thr and gain <= 0.12) if _b(cfg, "ENABLE_LOSS_PROTECTION", True) else False
            if gain < lp_thr:
                blocked = False
            if not blocked:
                return "DC_BASIS_3M_REDUCE", True
    return None


def reduce_fraction(cfg, gain):
    """live 14786-14796: fraction of the position removed by a (non-full-close) hard exit; None = all-but-dust (reduce to min qty)."""
    mp = _f(cfg, "NOLOSS_MIN_PROFIT_PCT", 0.5)
    if gain < 1.0 and gain >= mp:
        return _f(cfg, "WT_REDUCE_FRAC_LOW", 0.15) if gain < 0.5 else _f(cfg, "WT_REDUCE_FRAC_MED", 0.25)
    if 1.0 <= gain < 3.0:
        return _f(cfg, "WT_REDUCE_FRAC_HIGH", 0.50)
    return None
