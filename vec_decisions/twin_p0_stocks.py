"""twin_p0_stocks.py — P0 LIVE-CONNECT scalar live twins for vector-only template switches (stocks).

Each twin mirrors its EXISTING vector predicate EXACTLY (cited line). Scalar and
vector implementations share one core so the twin test proves live-twin ⟺ vector
agreement without importing the heavyweight engines.

Reference key (v12 = v12_quick_engine.py, fv2 = v12_quick_engine_fast_v2.py):
  DD_BOUNCE              v12:612-616   stoch entry gate k<40 L / k>60 S
  FG_FEAR/GREED          fv2:10903-10908 rsi_1h gates, active only when != 625-default
  FROZEN_STOP_FILTER_TF  v12:12386 TF resolution (FILTER_TF overrides family TF)
  GR_TIGHT_STOP          ez_manage.py:50528-50565 position-level tight stop (v12:8620 read
                         is tuple-inert; fv2:17203 adx>15 is a NEW_AUDIT delta-proxy)
  RALLY_BYPASS_CD        v12:12960-12970 rally cooldown bypass
  MIN_HOLD_BARS          v12:12507 max(MIN_HOLD_BARS, MIN_HOLD_BARS_BEFORE_EXIT); gate 14055
  MTF_DC_USE_DC4         v12:12666-12669 dc_high4/dc_low4 band-key select
  STOCH_CROSS            v12:9248-9256 K/D cross gate (k_prev vs CURRENT d)
  DAYTRADE_1H_EXP        v12:8677-8679 dc_width_1h > prev + dc_pos entry band
  TRADIER_STOCH x4       v12:8800-8804 K band (entry,extreme)=(30,15)L (70,85)S
  WT_SIMPLE_GUARANTEE    v12:9698-9701 entry OR + v12:10219-10222 exit OR (core cross;
                         HL/HH/vol refinements via explicit flags, v12:9702-9736)
  FORCE_MIN_ONE_TRADE    v12:9870-9873 force first_valid bar when no entry anywhere
  CRYPTO_SPIKE_FADE      v12:551-556 atr_1h > thr exit OR when != 625-default
"""
from __future__ import annotations

import math

import numpy as np


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else float(d)
    except (TypeError, ValueError):
        return float(d)


def _b(x, d=False):
    try:
        return bool(x)
    except Exception:
        return bool(d)


# ── 1. DD_BOUNCE_ENABLED (v12:612-616) ──────────────────────────────
def dd_bounce_fire(k, is_long, enabled=False):
    """Vector: entry_mask &= (k<40) L / (k>60) S. Scalar twin: gate pass."""
    if not _b(enabled):
        return True
    k = _f(k, 50.0)
    return (k < 40.0) if is_long else (k > 60.0)


def dd_bounce_fire_vec(k_arr, is_long, enabled=False):
    k_arr = np.asarray(k_arr, dtype=float)
    if not _b(enabled):
        return np.ones(k_arr.shape, dtype=bool)
    return (k_arr < 40.0) if is_long else (k_arr > 60.0)


# ── 2/3. FG_FEAR/GREED_THRESHOLD (fv2:10903-10908) ──────────────────
def _fg_active(val, dflt625):
    return float(val or 0) != float(dflt625 or 0)


def fg_fear_fire(rsi_1h, val, dflt625, is_long):
    """FEAR: active when val!=625default; gate rsi>thr L / rsi<thr S (thr fb 30)."""
    if not _fg_active(val, dflt625):
        return True
    thr = float(val or 30)
    v = _f(rsi_1h, 50.0)
    return (v > thr) if is_long else (v < thr)


def fg_fear_fire_vec(rsi_arr, val, dflt625, is_long):
    rsi_arr = np.asarray(rsi_arr, dtype=float)
    if not _fg_active(val, dflt625):
        return np.ones(rsi_arr.shape, dtype=bool)
    thr = float(val or 30)
    return (rsi_arr > thr) if is_long else (rsi_arr < thr)


def fg_greed_fire(rsi_1h, val, dflt625, is_long):
    """GREED: active when val!=625default; gate rsi<thr L / rsi>thr S (thr fb 70)."""
    if not _fg_active(val, dflt625):
        return True
    thr = float(val or 70)
    v = _f(rsi_1h, 50.0)
    return (v < thr) if is_long else (v > thr)


def fg_greed_fire_vec(rsi_arr, val, dflt625, is_long):
    rsi_arr = np.asarray(rsi_arr, dtype=float)
    if not _fg_active(val, dflt625):
        return np.ones(rsi_arr.shape, dtype=bool)
    thr = float(val or 70)
    return (rsi_arr < thr) if is_long else (rsi_arr > thr)


# ── 4. FROZEN_STOP_FILTER_TF (v12:12386 family pattern) ──────────────
def frozen_eff_tf(filter_tf="15m", family_tf="1h"):
    """FILTER_TF overrides family TF; OFF/empty falls back to family TF."""
    ft = str(filter_tf or "").strip()
    if ft == "" or ft.upper() == "OFF":
        return str(family_tf or "1h")
    return ft


# ── 5. GUARANTEED_REENTRY_TIGHT_STOP (ez:50528-50565) ────────────────
def gr_tight_stop_fire(enabled=True, gain_pct=0.0, age_s=9999.0, stop_pct=0.5,
                       min_age_s=60.0, max_age_s=1800.0, is_gr_position=False):
    """Fire when GR-tagged position, min<=age<=max, gain <= -abs(pct)."""
    if not (_b(enabled) and bool(is_gr_position)):
        return False
    if not (float(min_age_s) <= float(age_s) <= float(max_age_s)):
        return False
    return float(gain_pct) <= -abs(float(stop_pct))


def gr_tight_stop_fire_vec(gain_arr, age_arr, enabled=True, stop_pct=0.5,
                           min_age_s=60.0, max_age_s=1800.0, is_gr_arr=None):
    g = np.asarray(gain_arr, dtype=float)
    a = np.asarray(age_arr, dtype=float)
    if not _b(enabled):
        return np.zeros(g.shape, dtype=bool)
    gr = np.ones(g.shape, dtype=bool) if is_gr_arr is None else np.asarray(is_gr_arr, dtype=bool)
    return gr & (a >= float(min_age_s)) & (a <= float(max_age_s)) & (g <= -abs(float(stop_pct)))


# ── 6. HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN (v12:12960-12970) ─────
def rally_bypass_fire(enabled=True, bypass=True, pos_none=True, closed_before=True,
                      cd=0, px=0.0, last_exit=0.0, wt1=0.0, wt1_prev=0.0,
                      require_wt=False, is_long=True, hrf_ok=True):
    """Twin of the vector bypass block: gate AND rally-cross AND filter-ok."""
    if not (_b(enabled) and _b(bypass) and bool(pos_none) and bool(closed_before) and cd > 0):
        return False
    if not (float(last_exit) > 0):
        return False
    wt_ok = (float(wt1) > float(wt1_prev)) if is_long else (float(wt1) < float(wt1_prev))
    if not bool(require_wt):
        wt_ok = True
    cross = (float(px) > float(last_exit)) if is_long else (float(px) < float(last_exit))
    return bool(cross and wt_ok and bool(hrf_ok))


# ── 7. MIN_HOLD_BARS (v12:12507 + gate v12:14055) ────────────────────
def min_hold_bars(cfg, dflt_main=3, dflt_before=0):
    """max(MIN_HOLD_BARS, MIN_HOLD_BARS_BEFORE_EXIT) — vector getattr defaults."""
    try:
        main = cfg.MIN_HOLD_BARS if not isinstance(cfg, dict) else cfg.get("MIN_HOLD_BARS", dflt_main)
    except AttributeError:
        main = getattr(cfg, "MIN_HOLD_BARS", dflt_main)
    before = cfg.get("MIN_HOLD_BARS_BEFORE_EXIT", dflt_before) if isinstance(cfg, dict) else getattr(cfg, "MIN_HOLD_BARS_BEFORE_EXIT", dflt_before)
    try:
        return max(int(main or 0), int(before or 0))
    except (TypeError, ValueError):
        return 0


def min_hold_ok(held_bars, cfg):
    return float(held_bars) >= float(min_hold_bars(cfg))


# ── 8. MTF_DC_REJECT_USE_DC4 (v12:12666-12669) ───────────────────────
def mtf_dc_band_key(tf, is_long, use_dc4=False):
    """Exact key select: dc_high4_/dc_high_ L, dc_low4_/dc_low_ S, + TF."""
    u4 = _b(use_dc4)
    if is_long:
        return ("dc_high4_" if u4 else "dc_high_") + str(tf)
    return ("dc_low4_" if u4 else "dc_low_") + str(tf)


# ── 9. STOCH_CROSS_ENTRY_TRADIER (v12:9248-9256) ─────────────────────
def stoch_cross_fire(k, d, k_prev, is_long):
    """L: (k_prev<=d)&(k>d); S: (k_prev>=d)&(k<d). NOTE: k_prev vs CURRENT d."""
    k, d, kp = _f(k, 50.0), _f(d, 50.0), _f(k_prev, 50.0)
    if is_long:
        return (kp <= d) and (k > d)
    return (kp >= d) and (k < d)


def stoch_cross_fire_vec(k_arr, d_arr, is_long):
    k_arr = np.asarray(k_arr, dtype=float)
    d_arr = np.asarray(d_arr, dtype=float)
    kp = np.roll(k_arr, 1)
    if kp.size:
        kp[0] = k_arr[0]
    if is_long:
        return (kp <= d_arr) & (k_arr > d_arr)
    return (kp >= d_arr) & (k_arr < d_arr)


# ── 10. TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION (v12:8673-8679) ─────
def daytrade_expansion_ok(width_1h, width_1h_prev, require=True):
    """require ? width > prev_width : True."""
    if not _b(require):
        return True
    return _f(width_1h) > _f(width_1h_prev)


def daytrade_entry_fire(dc_pos_15m, width_1h, width_1h_prev, thr=0.15, require=True, is_long=True):
    """Full B_DAYTRADE: dc_pos band AND expansion gate."""
    pos = _f(dc_pos_15m, 0.5)
    band = (pos < float(thr)) if is_long else (pos > (1.0 - float(thr)))
    return bool(band and daytrade_expansion_ok(width_1h, width_1h_prev, require))


def daytrade_entry_fire_vec(dc_arr, w_arr, is_long, thr=0.15, require=True):
    dc_arr = np.asarray(dc_arr, dtype=float)
    w_arr = np.asarray(w_arr, dtype=float)
    wp = np.roll(w_arr, 1)
    if wp.size:
        wp[0] = w_arr[0]
    band = (dc_arr < float(thr)) if is_long else (dc_arr > (1.0 - float(thr)))
    exp = (w_arr > wp) if _b(require) else np.ones(dc_arr.shape, dtype=bool)
    return band & exp


# ── 11-14. TRADIER_STOCH_* thresholds (v12:8800-8804) ────────────────
STOCH_VEC_DEFAULTS = {
    "TRADIER_STOCH_ENTRY_LONG_TRADIER": 30,
    "TRADIER_STOCH_ENTRY_SHORT_TRADIER": 70,
    "TRADIER_STOCH_EXTREME_LONG_TRADIER": 15,
    "TRADIER_STOCH_EXTREME_SHORT_TRADIER": 85,
}


def stoch_threshold(cfg_resolve, key):
    """Exact vector key + getattr default (30/70/15/85). cfg_resolve(name,dflt)."""
    return cfg_resolve(key, STOCH_VEC_DEFAULTS[key])


def stoch_band_fire(k, entry_thr, extreme_thr, is_long):
    """L: (k<entry)&(k>extreme); S: (k>entry)&(k<extreme)."""
    k = _f(k, 50.0)
    e, x = float(entry_thr), float(extreme_thr)
    if is_long:
        return (k < e) and (k > x)
    return (k > e) and (k < x)


def stoch_band_fire_vec(k_arr, entry_thr, extreme_thr, is_long):
    k_arr = np.asarray(k_arr, dtype=float)
    e, x = float(entry_thr), float(extreme_thr)
    if is_long:
        return (k_arr < e) & (k_arr > x)
    return (k_arr > e) & (k_arr < x)


# ── 15. WT_SIMPLE_GUARANTEE_ENABLED (v12:9698-9701, 10219-10222) ─────
def wt_simple_entry_fire(wt1, wt2, is_long, hl_hh_ok=True, vol_ok=True, sub_explicit=False):
    """Core: L wt1>wt2 / S wt1<wt2; sub-gates ANDed only when their flags explicit."""
    w1, w2 = _f(wt1), _f(wt2)
    cross = (w1 > w2) if is_long else (w1 < w2)
    if not bool(sub_explicit):
        return bool(cross)
    return bool(cross and bool(hl_hh_ok) and bool(vol_ok))


def wt_simple_entry_fire_vec(w1_arr, w2_arr, is_long):
    w1_arr = np.asarray(w1_arr, dtype=float)
    w2_arr = np.asarray(w2_arr, dtype=float)
    return (w1_arr > w2_arr) if is_long else (w1_arr < w2_arr)


def wt_simple_exit_fire(wt1, wt2, is_long):
    """Exit OR: opposite WT — L wt1<wt2 / S wt1>wt2."""
    w1, w2 = _f(wt1), _f(wt2)
    return (w1 < w2) if is_long else (w1 > w2)


def wt_simple_exit_fire_vec(w1_arr, w2_arr, is_long):
    w1_arr = np.asarray(w1_arr, dtype=float)
    w2_arr = np.asarray(w2_arr, dtype=float)
    return (w1_arr < w2_arr) if is_long else (w1_arr > w2_arr)


# ── 16. FORCE_MIN_ONE_TRADE (v12:9870-9873) ──────────────────────────
def force_first_index(n, any_entry, enabled=False, first_valid=0):
    """Vector: if no entry anywhere and enabled → force first_valid bar. None = no-op."""
    if bool(any_entry) or not _b(enabled):
        return None
    return int(first_valid) if 0 <= int(first_valid) < int(n) else None


# ── 17. CRYPTO_SPIKE_FADE_THRESHOLD_PCT (v12:551-556) ────────────────
def spike_fade_exit_fire(atr_1h, thr, dflt625):
    """Active when thr!=625default; exit OR gate atr_1h > (thr if thr>0 else 0.5)."""
    t = _f(thr)
    d = _f(dflt625)
    if abs(t - d) <= 1e-9:
        return False
    return _f(atr_1h, 1.0) > (t if t > 0 else 0.5)


def spike_fade_exit_fire_vec(atr_arr, thr, dflt625):
    atr_arr = np.asarray(atr_arr, dtype=float)
    t = _f(thr)
    d = _f(dflt625)
    if abs(t - d) <= 1e-9:
        return np.zeros(atr_arr.shape, dtype=bool)
    return atr_arr > (t if t > 0 else 0.5)
