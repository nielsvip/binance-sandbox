"""Stocks live twins of the vec entry FILTER_TF masks (PARITY LANE C, 2026-10-06).

Scalar, per-tick mirrors of the exact vec predicates applied to ``entry_sig`` in
``v12_quick_engine.simulate_one``:
  - vec_decisions.filter_tf_gates.mom3_entry_gate / momentum_breakout_gate
  - vec_decisions.bb_pullback_gate.bb_pullback_gate_vec (when BB_PULLBACK_GATE_FILTER_TF != OFF)
  - vec_decisions.generic_filter_tf._cond kinds: bb_reclaim, dc_break, wt_cross_side,
    bar_pattern_side, dc_retest_hold, bb_bounce
Each function returns a veto string (entry blocked) or None (entry allowed).
Missing live keys fail OPEN exactly like the vec ``safe`` defaults do.
Pure stdlib, no tradier_manage import (unit-testable in isolation).
"""
from __future__ import annotations

import math

BAR_PATTERN_BULL = frozenset({"morning_star", "three_white_soldiers", "bull_engulfing", "tweezer_bottom", "hammer", "bull_harami", "pin_bar_bull", "three_bar_bull"})
BAR_PATTERN_BEAR = frozenset({"evening_star", "three_black_crows", "bear_engulfing", "tweezer_top", "shooting_star", "bear_harami", "pin_bar_bear", "three_bar_bear"})
BAR_PATTERN_CODES = {"none": 0, "morning_star": 1, "evening_star": 2, "three_white_soldiers": 3, "three_black_crows": 4, "bull_engulfing": 5, "bear_engulfing": 6, "tweezer_bottom": 7, "tweezer_top": 8, "hammer": 9, "shooting_star": 10, "bull_harami": 11, "bear_harami": 12, "multi_inside": 13, "inside_bar": 14, "outside_bar": 15, "pin_bar_bull": 16, "pin_bar_bear": 17, "three_bar_bull": 18, "three_bar_bear": 19, "doji": 20}
_BULL_CODES = frozenset({1, 3, 5, 7, 9, 11, 16, 18})
_BEAR_CODES = frozenset({2, 4, 6, 8, 10, 12, 17, 19})


def _num(get_ind, key):
    try:
        v = get_ind(key)
    except Exception:
        return None
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _fnum(v, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if math.isnan(f) else f


def resolve_tf(raw):
    t = str(raw if raw is not None else "OFF").strip()
    return None if (not t or t.upper() == "OFF") else t


def mom3_block(get_ind, price, is_long, tf, thr_long, thr_short):
    if tf is None or not price or price <= 0:
        return None
    c3 = _num(get_ind, f"close_3bar_{tf}")
    if c3 is None or c3 <= 0:
        return None
    m = (price - c3) / c3 * 100.0
    ok = (m < _fnum(thr_long, -1.0)) if is_long else (m > _fnum(thr_short, 1.0))
    return None if ok else f"MOM3_FILTER_TF_{tf}_BLOCK({m:.2f}%)"


def momentum_breakout_block(get_ind, price, is_long, tf):
    if tf is None or not price or price <= 0:
        return None
    c3 = _num(get_ind, f"close_3bar_{tf}")
    if c3 is None or c3 <= 0:
        return None
    m = (price - c3) / c3 * 100.0
    ok = (m > 0.0) if is_long else (m < 0.0)
    return None if ok else f"MOMENTUM_BREAKOUT_TF_{tf}_BLOCK({m:.2f}%)"


def bb_pullback_filter_block(get_ind, is_long, tf, enabled, long_max, short_min):
    """vec: entry_sig &= ~bb_pullback_gate_vec at TF=FILTER_TF (master + LONG_MAX/SHORT_MIN)."""
    if tf is None or not truthy(enabled):
        return None
    b = _num(get_ind, f"bb_pct_b_{tf}")
    if b is None:
        return None
    bound = _fnum(long_max, 0.30) if is_long else _fnum(short_min, 0.70)
    blocked = (b > bound) if is_long else (b < bound)
    return f"BB_PULLBACK_TF_{tf}_BLOCK(pctB={b:.2f})" if blocked else None


def bb_reclaim_block(get_ind, is_long, tf, label):
    if tf is None:
        return None
    b = _num(get_ind, f"bb_pct_b_{tf}")
    if b is None:
        return None
    ok = (b > 0.5) if is_long else (b < 0.5)
    return None if ok else f"{label}_{tf}_BLOCK(pctB={b:.2f})"


def dc_break_block(get_ind, price, is_long, tf):
    if tf is None or not price or price <= 0:
        return None
    lvl = _num(get_ind, f"dc_high_{tf}_prev" if is_long else f"dc_low_{tf}_prev")
    if lvl is None or lvl <= 0:
        return None
    ok = (price > lvl) if is_long else (price < lvl)
    return None if ok else f"DC_BREAK_TF_{tf}_BLOCK(px{price:.4f}_vs_{lvl:.4f})"


def wt_cross_side_block(get_ind, is_long, tf, label):
    if tf is None:
        return None
    w1 = _num(get_ind, f"wt1_{tf}")
    w2 = _num(get_ind, f"wt2_{tf}")
    if w1 is None or w2 is None or (w1 == 0 and w2 == 0):
        return None
    ok = (w1 > w2) if is_long else (w1 < w2)
    return None if ok else f"{label}_{tf}_BLOCK(wt1={w1:.1f}_wt2={w2:.1f})"


def _pattern_code(raw):
    if raw is None:
        return None
    if isinstance(raw, str):
        return BAR_PATTERN_CODES.get(raw.strip().lower(), 0)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def bar_pattern_side_block(get_ind, is_long, tf):
    """vec bar_pattern_side: bullish code set for LONG / bearish for SHORT; missing key -> pass."""
    if tf is None:
        return None
    try:
        raw = get_ind(f"bar_pattern_{tf}")
    except Exception:
        raw = None
    code = _pattern_code(raw)
    if code is None or code < 0:
        return None
    ok = (code in _BULL_CODES) if is_long else (code in _BEAR_CODES)
    return None if ok else f"BAR_PATTERNS_TF_{tf}_BLOCK(code={code})"


def dc_retest_hold_block(get_ind, price, is_long, tf, master_enabled):
    """vec dc_retest_hold, master-gated by BREAKOUT_RETEST_ARMED_ENABLED (v12 [JSN2] forces OFF otherwise)."""
    if tf is None or not truthy(master_enabled) or not price or price <= 0:
        return None
    lvl = _num(get_ind, f"dc_high_{tf}_prev" if is_long else f"dc_low_{tf}_prev")
    if lvl is None or lvl <= 0:
        return None
    wick = _num(get_ind, f"low_{tf}" if is_long else f"high_{tf}") or 0.0
    if is_long:
        ok = wick > 0 and wick <= lvl and price > lvl
    else:
        ok = wick > 0 and wick >= lvl and price < lvl
    return None if ok else f"BREAKOUT_RETEST_TF_{tf}_BLOCK(px{price:.4f}_wick{wick:.4f}_lvl{lvl:.4f})"


def bb_bounce_filter_block(get_ind, price, is_long, tf):
    """vec FILTER_TF_MAP BB_BOUNCE_ENTRY_TF ('entry','bb_bounce'): long needs low<=bb_lower<close; band missing -> pass."""
    if tf is None:
        return None
    band = _num(get_ind, f"bb_lower_{tf}" if is_long else f"bb_upper_{tf}")
    if band is None or band <= 0:
        return None
    px = float(price or 0.0)
    if is_long:
        lo = _num(get_ind, f"low_{tf}") or 0.0
        ok = lo > 0 and lo <= band and px > band
    else:
        hi = _num(get_ind, f"high_{tf}") or 0.0
        ok = hi > 0 and hi >= band and px < band
    return None if ok else f"BB_BOUNCE_FILTER_TF_{tf}_BLOCK(px{px:.4f}_band{band:.4f})"


# ── PARITY LANE C phase 3 (2026-10-06) ───────────────────────────────────────


def _bool_ind(get_ind, key):
    try:
        v = get_ind(key)
    except Exception:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v) if v is not None else False


def ema50_block(get_ind, price, is_long, enabled, pct):
    """vec v12 EMA50_15M_ENTRY_FILTER: LONG blocked when px <= ema_50_15m*(1+pct/100) (ema>0, px>0)."""
    if not truthy(enabled):
        return None
    px = float(price or 0.0)
    ema = _num(get_ind, "ema_50_15m")
    if ema is None or ema <= 0 or px <= 0:
        return None
    p = _fnum(pct, 0.0) / 100.0
    blocked = (px <= ema * (1.0 + p)) if is_long else (px >= ema * (1.0 - p))
    return f"EMA50_15M_ENTRY_FILTER_BLOCK(px{px:.4f}_ema{ema:.4f})" if blocked else None


def bb_bounce_source_fires(get_ind, is_long, tf):
    """vec compute_reentry_blocks B_BB_BOUNCE_ENTRY_{tf}: prev %B<0.20 & %B>0.25 & lower>0 (long). Prev = _lc_bb_pct_b_{tf}_prev."""
    if tf not in ("15m", "1h", "4h", "D"):
        return False, ""
    pct = _num(get_ind, f"bb_pct_b_{tf}")
    prev = _num(get_ind, f"_lc_bb_pct_b_{tf}_prev")
    if pct is None or prev is None:
        return False, ""
    if is_long:
        band = _num(get_ind, f"bb_lower_{tf}") or 0.0
        fire = prev < 0.20 and pct > 0.25 and band > 0
    else:
        band = _num(get_ind, f"bb_upper_{tf}") or 0.0
        fire = prev > 0.80 and pct < 0.75 and band > 0
    return (True, f"BB_BOUNCE_ENTRY_{tf}_{'L' if is_long else 'S'}_pb{prev:.2f}->{pct:.2f}") if fire else (False, "")


def wt15_bounce_fires(get_cfg, get_ind, is_long):
    """vec compute_entry_signals WT_15M_BOUNCE_OPEN block (v12 ~9897-9976). Prev row = _lc_wt*_15m_prev."""
    if not truthy(get_cfg("WT_15M_BOUNCE_OPEN_ENABLED", False)):
        return False, ""
    w1 = _num(get_ind, "wt1_15m") or 0.0
    w2 = _num(get_ind, "wt2_15m") or 0.0
    w1p = _num(get_ind, "_lc_wt1_15m_prev")
    w2p = _num(get_ind, "_lc_wt2_15m_prev")
    if w1p is None or w2p is None:
        return False, ""
    up = (w1p <= w2p) and (w1 > w2)
    down = (w1p >= w2p) and (w1 < w2)
    cross = up if is_long else down
    still = (w1 > w2) if is_long else (w1 < w2)
    bb = _num(get_ind, "bb_pct_b_15m")
    bb = 0.5 if bb is None else bb
    bb_ok = _fnum(get_cfg("WT_15M_BOUNCE_BB_MIN", 0.05), 0.05) <= bb <= _fnum(get_cfg("WT_15M_BOUNCE_BB_MAX", 0.95), 0.95)
    r1 = _bool_ind(get_ind, "wt_cross_rising_1h")
    r4 = _bool_ind(get_ind, "wt_cross_rising_4h")
    both = truthy(get_cfg("WT_15M_BOUNCE_REQUIRE_BOTH_HTF", False))
    if is_long:
        htf_ok = (r4 and r1) if both else (r4 or r1)
    else:
        htf_ok = ((not r4) and (not r1)) if both else ((not r4) or (not r1))
    hl_on = truthy(get_cfg("WT_15M_BOUNCE_FILTER_HL_ENABLED", False)) or truthy(get_cfg("WT_15M_BOUNCE_LOW_1H_GT_PREV", False))
    hh_on = truthy(get_cfg("WT_15M_BOUNCE_FILTER_HH_ENABLED", False)) or truthy(get_cfg("WT_15M_BOUNCE_HIGH_1H_GT_PREV", False))
    hlhh_ok = True
    if hl_on or hh_on:
        lo, lop = _num(get_ind, "dc_low_1h") or 0.0, _num(get_ind, "dc_low_1h_prev")
        hi, hip = _num(get_ind, "dc_high_1h") or 0.0, _num(get_ind, "dc_high_1h_prev")
        lop = lo if lop is None else lop
        hip = hi if hip is None else hip
        hl_ok = (lo > lop) if hl_on else True
        hh_ok = (hi > hip) if hh_on else True
        mode = str(get_cfg("WT_15M_BOUNCE_FILTER_MODE", "AND") or "AND").upper()
        if mode == "OR":
            hlhh_ok = hl_ok if (hl_on and not hh_on) else (hh_ok if (hh_on and not hl_on) else (hl_ok or hh_ok))
        else:
            hlhh_ok = hl_ok and hh_ok
    trigger = still if (hl_on or hh_on) else cross
    vol_on = truthy(get_cfg("WT_15M_BOUNCE_VOLUME_FILTER_ENABLED", False)) or truthy(get_cfg("WT_15M_BOUNCE_REL_VOL_GT_1", False))
    vol_ok = True
    if vol_on:
        vmode = str(get_cfg("WT_15M_BOUNCE_VOLUME_MODE", "relvol") or "relvol").lower()
        thr = _fnum(get_cfg("WT_15M_BOUNCE_VOLUME_THRESHOLD", 1.0), 1.0)
        if vmode == "relvol":
            rel = _num(get_ind, "relative_volume_15m")
            if rel is None or rel == 1.0:
                rel = _num(get_ind, "relative_volume_1h")
            vol_ok = (1.0 if rel is None else rel) > thr
        else:
            vol, sma = _num(get_ind, "volume_15m"), _num(get_ind, "volume_sma_15m")
            if not vol or not sma:
                vol, sma = _num(get_ind, "volume_1h"), _num(get_ind, "volume_sma_1h")
            vol = vol or 0.0
            sma = sma if sma else 1.0
            vol_ok = vol > (sma * thr if vmode == "ema" else sma)
    fire = trigger and bb_ok and htf_ok and hlhh_ok and vol_ok
    return (True, f"WT_15M_BOUNCE_OPEN_{'L' if is_long else 'S'}_wt1={w1:.1f}_wt2={w2:.1f}") if fire else (False, "")


def top_fade_confirm(get_ind, is_long, tf):
    """vec generic_filter_tf 'wt_top_fade' exit_confirm: True = the close may proceed. Missing prev -> allow (fail-open)."""
    if tf is None:
        return True
    w1 = _num(get_ind, f"wt1_{tf}")
    w1p = _num(get_ind, f"_lc_wt1_{tf}_prev")
    if w1 is None or w1p is None or (w1 == 0 and w1p == 0):
        return True
    return (w1 > 60 and w1 < w1p) if is_long else (w1 < -60 and w1 > w1p)


def candle_against_confirm(get_ind, is_long, tf):
    """vec generic_filter_tf 'bar_pattern_against' exit_confirm: bearish pattern confirms a LONG close. Missing -> allow."""
    if tf is None:
        return True
    try:
        raw = get_ind(f"bar_pattern_{tf}")
    except Exception:
        raw = None
    code = _pattern_code(raw)
    if code is None or code < 0:
        return True
    return (code in _BEAR_CODES) if is_long else (code in _BULL_CODES)


EXIT_CONFIRM_PREFIXES = ("TECHNICAL_", "WT_CROSSUNDER_FINAL", "WT_CROSSOVER_FINAL")


def exit_confirm_block(get_ind, is_long, reason, top_fade_tf, candle_tf):
    """vec simulate_one `_xc_ok`: exit_sig-block closes (TECHNICAL dc / WT final) need every active exit_confirm filter. Returns veto str or None."""
    if not str(reason or "").startswith(EXIT_CONFIRM_PREFIXES):
        return None
    if not top_fade_confirm(get_ind, is_long, top_fade_tf):
        return f"EXIT_TOP_FADE_FILTER_TF_{top_fade_tf}_NO_CONFIRM"
    if not candle_against_confirm(get_ind, is_long, candle_tf):
        return f"CANDLE_PATTERN_STOPS_FILTER_TF_{candle_tf}_NO_CONFIRM"
    return None


FAST_RISER_MIN_GAIN_PCT = 0.8


def _ha_sign(raw):
    if isinstance(raw, str):
        r = raw.strip().lower()
        return 1 if r == "green" else (-1 if r == "red" else 0)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def fast_riser_fires(get_ind, price, is_long, tf, gain_pct):
    """vec filter_tf_gates.fast_riser_sig + loop gate live_pnl_pct > 0.8 (FAST_RISER_DOUBLE augment)."""
    if tf is None:
        return False, ""
    px = float(price or 0.0)
    prev = _num(get_ind, f"close_{tf}_prev") or 0.0
    low = _num(get_ind, f"low_{tf}") or 0.0
    low_prev = _num(get_ind, f"low_{tf}_prev") or 0.0
    try:
        ha = _ha_sign(get_ind(f"ha_{tf}"))
    except Exception:
        ha = 0
    if not (prev > 0 and low > 0 and low_prev > 0):
        return False, ""
    jump = (px - prev) / prev
    if is_long:
        sig = px > prev and abs(jump) >= 0.002 and ha > 0 and low < low_prev
    else:
        sig = px < prev and abs(jump) >= 0.002 and ha < 0 and low > low_prev
    if sig and _fnum(gain_pct, 0.0) > FAST_RISER_MIN_GAIN_PCT:
        return True, f"FAST_RISER_DOUBLE_{tf}_jump{jump * 100:.2f}%_g{_fnum(gain_pct, 0.0):.2f}%"
    return False, ""


def newborn_vel_key(filter_raw, vel_tf_raw):
    """vec simulate_one NEWBORN_LOSS_KILL velocity key (resolve_filter_tf family fallback, filter default 15m)."""
    fam = str(vel_tf_raw or "").strip() or "3m"
    r = str(filter_raw if filter_raw is not None else "").strip()
    if r == "" or r.upper() == "OFF":
        tf = None
    elif r == "15m":
        tf = None if fam.upper() == "OFF" else fam
    else:
        tf = r
    tf = tf or fam
    return f"wt_velocity_{tf}" if tf.upper() != "OFF" else "wt_velocity_15m"


def _kg_tfs(s):
    return [t.strip() for t in str(s or "").split(",") if t.strip()]


def kg_stocks_block(get_cfg, get_ind, price, is_long):
    """Scalar twin of vec_decisions.live_kindergarten_stocks.pass_mask (applied by vec only when KG_STOCKS_LIVE_GATE and
    KG_STOCKS_HARD_VETO_ENABLED). Key presence per tick == vec `k in npz`. Returns veto str or None."""
    if not (truthy(get_cfg("KG_STOCKS_LIVE_GATE", True)) and truthy(get_cfg("KG_STOCKS_HARD_VETO_ENABLED", False))):
        return None
    if not (truthy(get_cfg("EMA_9_21_FILTER_ENABLED", False)) or truthy(get_cfg("KINDERGARTEN_EMA_GATE_ENABLED", False))):
        return None
    cum = truthy(get_cfg("KINDERGARTEN_CUMULATIVE_MODE", True))
    tf1 = str(get_cfg("EMA_9_21_TIMEFRAME", "1h") or "1h")

    def _has(k):
        try:
            return get_ind(k) is not None
        except Exception:
            return False
    if not cum:
        k = f"ema_9_above_21_{tf1}"
        if not _has(k):
            return None
        a = _num(get_ind, k)
        a = -1.0 if a is None else a
        ok = (a != 0.0) if is_long else (a != 1.0)
        return None if ok else f"KG_STOCKS_LEGACY_{tf1}_BLOCK"
    checks = []
    for tf in _kg_tfs(get_cfg("EMA_9_21_FILTER_TFS", tf1)) or [tf1]:
        k = f"ema_9_above_21_{tf}"
        if _has(k):
            a = _num(get_ind, k) or 0.0
            checks.append(("EMA9_21_" + tf, (a != 0.0) if is_long else (a != 1.0)))
    if truthy(get_cfg("EMA_50_200_FILTER_ENABLED", False)):
        for tf in _kg_tfs(get_cfg("EMA_50_200_TFS", get_cfg("EMA_50_200_TIMEFRAME", "D"))) or ["D"]:
            k = f"ema_50_above_200_{tf}"
            if _has(k):
                a = _num(get_ind, k) or 0.0
                checks.append(("EMA50_200_" + tf, (a != 0.0) if is_long else (a != 1.0)))
    px = float(price or 0.0)
    for flag, tfk, nm, key in (("SMA_50_FILTER_ENABLED", "SMA_50_TIMEFRAME", "SMA50_", "sma_50_"), ("SMA_200_FILTER_ENABLED", "SMA_200_TIMEFRAME", "SMA200_", "sma_200_")):
        if truthy(get_cfg(flag, False)):
            tf = str(get_cfg(tfk, "D") or "D")
            k = f"{key}{tf}"
            if _has(k):
                sma = _num(get_ind, k) or 0.0
                ok = ((px > sma) if is_long else (px < sma)) if (sma > 0 and px > 0) else True
                checks.append((nm + tf, ok))
    if not checks:
        return None
    strict = _kg_tfs(get_cfg("KINDERGARTEN_STRICT_TFS", ""))
    if strict:
        for nm, v in checks:
            if any(t in nm for t in strict) and not v:
                return f"KG_STOCKS_STRICT_{nm}_BLOCK"
    from vec_decisions.kg_entry_gate import effective_min_tfs as _emt
    need = min(_emt(lambda k, d=None: get_cfg(k, d)), len(checks))
    passing = sum(1 for _, v in checks if v)
    return None if passing >= need else f"KG_STOCKS_CUM_BLOCK({passing}<{need})"
