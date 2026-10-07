"""lane_vec_mopup3.py — numpy twins for the MOP-UP-3 LIVE_ONLY family (7 switches).

Live citations (exact defaults mirror config.py / config_tradier.py):
  MTS_GATE_ENABLED ............ ez_positions_quick.py:2786-2805 (side-aware bottom/eq
    veto: LONG bottom>=10 & eq>=5, SHORT bottom>=5 & eq>=0; crypto defaults).
  MTS_BOTTOM_BONUS_THRESHOLD .. ez_positions_quick.py:2809-2814 (bottom>25 -> +4 score).
  MTS_BOTTOM_STRONG_THRESHOLD . ez_positions_quick.py:2809-2814 (bottom>40 -> +8 score).
    Score engine: analyze_multi_tf_state (:732-980). Frozen-NPZ subset below.
  MOVER_THRESHOLD ............. ez_positions_quick.py:4936-4977 (scan_movers mean-
    reversion trigger; LONG: score<-thr & dc<0.2, SHORT: score>+thr & dc>0.8).
  REENTRY_POST_CONSOL_MULT .... ez_manage.py:43685-43701 (_compose_reentry_mult:
    bar_atr_rank 1h/4h/D < 0.15 count >= 2 -> stack mult).
  REENTRY_K15M_PARTIAL_MULT ... ez_manage.py:43766-43781 (D-path: k NOT ok -> partial
    mult instead of 1.0; k ok = LONG k<thr / SHORT k>100-thr, thr 90.0).
  REENTRY_WT15M_SIZE_MULT ..... ez_manage.py:43707-43740 (C-path: 15m WT cross-state
    in direction + HTF favorable -> base mult; post-consol stacks via parent).

Design (mirrors lane_vec_scalp_v3.py):
  - Threshold switches -> pure sub-condition mask, gate-free (parent ANDs).
    None ONLY when a required NPZ key is absent — never fabricated.
  - MTS_GATE_ENABLED -> pass-mask (gate on AND bottom>=min AND eq>=min);
    None when the gate is off. Side-aware minima arrive via kw (live crypto
    defaults baked as bare numbers; the MIN switches belong to other lanes).
  - Sizing MULTs -> float scale array (mult where the trigger fires, 1.0
    elsewhere); None when keys are absent or the path is disabled. Parent
    folds (multiplies) into sizing. Foreign trigger params (thresholds,
    enabled flags) arrive via kw as bare values — never re-read here.
  - Position-state legs (delta tolerance, exit levels) are owned live-side;
    scales answer "which mult applies IF the reentry fires".
Polarity: mask True = condition FIRES (gate passes / threshold crossed /
trigger armed). None = switch off, required data absent, or side-inapplicable.
Default-inert: at live defaults each twin reproduces live exactly on the
frozen data subset (legs verified below). Fail-open: unexpected error -> None.

NPZ reality (probed on backtest_v8/indicators/AAPL.npz, 940 keys):
  PRESENT: k/d/wt1/wt2/wt_cross/wt_cross_value/wt_cross_prev_value/
    wt_cross_rising/wt_velocity/wt_cross_bars_ago/wt_divergence/
    wt_momentum_state/wt_bullish/dc_position/bar_atr_rank for 15m/1h/4h/D;
    div_hid_bull/bear_wt_* for 15m/1h/4h/D; wt1/wt2_15m/1h/4h; close.
  ABSENT: all 3m/1m keys (k_3m, ema_20_3m, dc_high/low_3m,
    relative_volume_3m, dc_position_3m), 0dc_* composite overlay keys.
  INT encodings (backtest_v8_precompute.py): wt_cross +1=BULL/-1=BEAR (:1228);
    wt_cross_rising = (w1>w2) (:1231); wt_divergence +1=BULL/-1=BEAR (:1288-1293);
    wt_momentum_state -2=EXHAUST_DOWN/+2=EXHAUST_UP (:1260-1262, sibling
    grey_wire_exits.py:24).
MTS frozen subset: live scores 6 TFs (1m/3m/15m/1h/4h/D, weights 1/2/3/5/8/5);
the twin scores the 4 frozen TFs (15m/1h/4h/D, live weights 3/5/8/5,
normalised by available weight). 1m/3m legs are uncomputable — omitted, never
defaulted. 0dc_* overlay omitted when keys are absent = live-default no-op
(0.5/0.5/0/1.0 produce zero adjustment, :861-885). Scalp-dc convergence legs
default to 0.5 when dc_position_3m is absent = live default (:932), so all
convergence bonuses stay off on frozen NPZ unless the caller supplies the key.

SKIPPED (honest stop — no bar predicate in live; not in SUPPORTED):
  LEADERBOARD_FILTER — membership veto on runtime winners/losers dicts
    (ez_manage.py:11027-11037, :21729, :22019, :46560, :50660); no bar data.
  MARKET_QUALITY_SCORE_ENABLED — score-bonus only (+5/+2, no veto/filter;
    ez_positions_quick.py:4577-4583). Sibling lane_vec_gates2 agrees.
  REGIME_RANGING_EXIT_GAIN_MIN / REGIME_TRENDING_EXIT_GAIN_MIN — DEAD: no
    consumer reads exit_gain_min (crypto uses only k_zone_bonus :2711/:3621;
    tradier gates carry `and False` :16786/:16831).
  ATR_LONG_WINDOW — dead computation-window param (ez_indicators.py:261, and
    :2434 "atr_long REMOVED — 0 references in consumers"). Gates2 agrees.
  ABLATION_DISABLE_ENTRY_LEADERBOARD / _RANKING / _REVERSAL / _TECHNICAL —
    ablation kill-switches gating eval_funcs appends (ez_manage.py:54066,
    :54348-54353); no bar predicate.
  MTF_FILTER_STRONG_BUY_QUICK_BYPASS — reason-string bypass (ez:30938-30956).
  PER_SYM_GATE_FLAT_OPEN_ENFORCE — action gate on per-sym JSON state
    (tradier_manage.py:26985); no bar data.
  BTC_TECH_EXIT_WT_MIN_TFS — BTC-dedicated-loop-only param behind master
    BTC_DEDICATED_ENABLED default False (config.py:4194); only audit stubs
    elsewhere. Gates2 agrees.
  BTC_ROUND_BANDS_EACH_SIDE — level-count param (8); only audit stubs.
  RECENT_REDUCTION_GUARD_ENABLED / RECENT_REDUCTION_GUARD_WINDOW_S —
    wall-clock guard on runtime _recent_reduces dict (ez_manage.py:32537-32540).
"""
from __future__ import annotations

from typing import Any, Mapping
import functools
import math

import numpy as np

SUPPORTED = (
    "MTS_GATE_ENABLED",
    "MTS_BOTTOM_BONUS_THRESHOLD",
    "MTS_BOTTOM_STRONG_THRESHOLD",
    "MOVER_THRESHOLD",
    "REENTRY_POST_CONSOL_MULT",
    "REENTRY_K15M_PARTIAL_MULT",
    "REENTRY_WT15M_SIZE_MULT",
)


def _f(cfg: Any, name: str, default: float) -> float:
    try:
        v = float(getattr(cfg, name, default))
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _b(cfg: Any, name: str, default: bool) -> bool:
    try:
        return bool(getattr(cfg, name, default))
    except Exception:
        return default


def _s(cfg: Any, name: str, default: str) -> str:
    try:
        v = getattr(cfg, name, default)
        return str(v).strip() if v is not None else default
    except Exception:
        return default


def _i(cfg: Any, name: str, default: int) -> int:
    try:
        return int(float(getattr(cfg, name, default)))
    except (TypeError, ValueError):
        return default


def _have(npz: Mapping[str, Any], key: str) -> bool:
    try:
        return key in npz
    except Exception:
        return False


def _col(npz: Mapping[str, Any], key: str, n: int) -> np.ndarray | None:
    """Strict column read: None when the key is absent (no fabrication)."""
    if not _have(npz, key):
        return None
    try:
        a = np.asarray(npz[key], dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return a[:n]


def _as_float_arr(x: Any, n: int) -> np.ndarray | None:
    try:
        a = np.asarray(x, dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return a[:n]


def _fail_open(fn):
    """Fail-open: any unexpected error -> None (parent skips the leg)."""

    @functools.wraps(fn)
    def _w(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return None

    return _w


# Live MTS weights for the frozen TF subset (bare numbers; the MTS_WEIGHT_*
# switches are foreign — intentionally not re-read here).
_MTS_TFS = (("15m", 3.0), ("1h", 5.0), ("4h", 8.0), ("D", 5.0))


def _fin(a: np.ndarray) -> np.ndarray:
    return np.isfinite(a)


def _mts_scores(npz: Mapping[str, Any], n: int, is_long: bool) -> tuple | None:
    """Vec mirror of analyze_multi_tf_state bottom_score/entry_quality.

    Live: ez_positions_quick.py:732-980. Frozen subset (15m/1h/4h/D) per the
    module docstring. Returns (bottom, entry) capped to [-100, 100], or None
    when any required key is absent. NaN bars yield no legs (fail-open).
    """
    if n <= 0:
        return None
    tot_b = np.zeros(n)
    tot_e = np.zeros(n)
    tot_w = 0.0
    extreme = np.zeros(n, dtype=int)
    htf_bull = np.zeros(n, dtype=int)
    per_tf: list = []
    for tf, w in _MTS_TFS:
        k = _col(npz, f"k_{tf}", n)
        w1 = _col(npz, f"wt1_{tf}", n)
        w2 = _col(npz, f"wt2_{tf}", n)
        xc = _col(npz, f"wt_cross_{tf}", n)
        xcv = _col(npz, f"wt_cross_value_{tf}", n)
        xpv = _col(npz, f"wt_cross_prev_value_{tf}", n)
        xrs = _col(npz, f"wt_cross_rising_{tf}", n)
        vel = _col(npz, f"wt_velocity_{tf}", n)
        bago = _col(npz, f"wt_cross_bars_ago_{tf}", n)
        div = _col(npz, f"wt_divergence_{tf}", n)
        mom = _col(npz, f"wt_momentum_state_{tf}", n)
        bull = _col(npz, f"wt_bullish_{tf}", n)
        dcp = _col(npz, f"dc_position_{tf}", n)
        if k is None or w1 is None or w2 is None or xc is None or xcv is None or xpv is None or xrs is None or vel is None or bago is None or div is None or mom is None or bull is None or dcp is None:
            return None
        kp = _col(npz, f"k_{tf}_prev", n)
        if kp is None:
            kp = k  # live defaults prev to current -> k_rising False
        hid_b = _col(npz, f"div_hid_bull_wt_{tf}", n)
        hid_s = _col(npz, f"div_hid_bear_wt_{tf}", n)
        kf = _fin(k)
        w1f = _fin(w1)
        w2f = _fin(w2)
        wsc = np.where(w1f & w2f, w1 - w2, np.nan)
        rising = kf & _fin(kp) & (k > kp)
        bago_f = np.where(_fin(bago), bago, 999.0)
        xrs_f = xrs == 1
        bullish = bull == 1
        xcv_f = _fin(xcv)
        xpv_f = _fin(xpv)
        raw_dc = np.where(_fin(dcp), dcp, 0.5)
        raw_dc = np.where(raw_dc > 1.0, raw_dc / 100.0, raw_dc)
        dc = np.clip(raw_dc, 0.0, 1.0)
        tf_b = np.zeros(n)
        tf_e = np.zeros(n)
        if is_long:
            k_ext = kf & (k < 10.0)
            k_zone = kf & (k < 30.0)
            extreme += k_ext.astype(int)
            tf_b += np.where(k_ext, 20.0, 0.0)
            tf_b += np.where(k_zone, 10.0, 0.0)
            tf_b += np.where((xc == 1) & (bago_f < 5.0), 15.0, 0.0)
            tf_b += np.where(xcv_f & (xcv < -40.0), 15.0, np.where(xcv_f & (xcv < -20.0), 8.0, 0.0))
            tf_b += np.where(xrs_f, 10.0, 0.0)
            div_bull = (div == 1) | ((hid_b == 1) if hid_b is not None else False)
            tf_b += np.where(div_bull, 20.0, 0.0)
            tf_b += np.where(mom == -2, 10.0, 0.0)
            tf_b += np.where(_fin(vel) & (vel > 0.0) & rising, 5.0, 0.0)
            tf_b += np.where(dc < 0.10, 25.0, np.where(dc < 0.20, 18.0, np.where(dc < 0.30, 12.0, np.where(dc < 0.40, 5.0, np.where(dc > 0.80, -10.0, 0.0)))))
            tf_e += np.where(xcv_f & xpv_f & (xcv > xpv), 15.0, 0.0)
            tf_e += np.where(bago_f < 3.0, 15.0, np.where(bago_f < 10.0, 5.0, 0.0))
            tf_e += np.where(xrs_f, 10.0, 0.0)
            tf_e += np.where((dc < 0.25) & bullish, 15.0, 0.0)
        else:
            k_ext = kf & (k > 90.0)
            k_zone = kf & (k > 70.0)
            extreme += k_ext.astype(int)
            tf_b += np.where(k_ext, 20.0, 0.0)
            tf_b += np.where(k_zone, 10.0, 0.0)
            tf_b += np.where((xc == -1) & (bago_f < 5.0), 15.0, 0.0)
            tf_b += np.where(xcv_f & (xcv > 40.0), 15.0, np.where(xcv_f & (xcv > 20.0), 8.0, 0.0))
            tf_b += np.where(xrs_f, 10.0, 0.0)
            div_bear = (div == -1) | ((hid_s == 1) if hid_s is not None else False)
            tf_b += np.where(div_bear, 20.0, 0.0)
            tf_b += np.where(mom == 2, 10.0, 0.0)
            tf_b += np.where(_fin(vel) & (vel < 0.0) & ~rising & kf, 5.0, 0.0)
            tf_b += np.where(dc > 0.90, 25.0, np.where(dc > 0.80, 18.0, np.where(dc > 0.70, 12.0, np.where(dc > 0.60, 5.0, np.where(dc < 0.20, -10.0, 0.0)))))
            tf_e += np.where(xcv_f & xpv_f & (xcv < xpv), 15.0, 0.0)
            tf_e += np.where(bago_f < 3.0, 15.0, np.where(bago_f < 10.0, 5.0, 0.0))
            tf_e += np.where(xrs_f, 10.0, 0.0)
            tf_e += np.where((dc > 0.75) & ~bullish, 15.0, 0.0)
        htf_bull += bullish.astype(int)
        tot_b += tf_b * w
        tot_e += tf_e * w
        tot_w += w
        per_tf.append((dc, k, wsc, bago_f, bullish))
    bottom = tot_b / tot_w
    entry = tot_e / tot_w
    bottom = np.where(extreme >= 4, bottom * 2.0, np.where(extreme >= 3, bottom * 1.5, bottom))
    entry = entry + htf_bull * 5.0
    bottom = np.clip(bottom, -100.0, 100.0)
    entry = np.clip(entry, -100.0, 100.0)
    htf_pos = _col(npz, "0dc_htf_pos", n)
    ltf_pos = _col(npz, "0dc_ltf_pos", n)
    dc_moment = _col(npz, "0dc_moment", n)
    dc_exp = _col(npz, "0dc_expansion", n)
    if htf_pos is not None and ltf_pos is not None and dc_moment is not None and dc_exp is not None:
        hp = np.where(_fin(htf_pos), htf_pos, 0.5)
        lp = np.where(_fin(ltf_pos), ltf_pos, 0.5)
        dm = np.where(_fin(dc_moment), dc_moment, 0.0)
        ex = np.where(_fin(dc_exp), dc_exp, 1.0)
        if is_long:
            bottom += np.where((hp < 0.30) & (lp < 0.30), 15.0, np.where((hp < 0.40) & (lp < 0.40), 8.0, 0.0))
            entry += np.where((lp > hp) & (hp < 0.30), 10.0, 0.0)
            bottom += np.where(hp > 0.80, -10.0, 0.0)
            bottom += np.where(dm < -30.0, 8.0, 0.0)
        else:
            bottom += np.where((hp > 0.70) & (lp > 0.70), 15.0, np.where((hp > 0.60) & (lp > 0.60), 8.0, 0.0))
            entry += np.where((lp < hp) & (hp > 0.70), 10.0, 0.0)
            bottom += np.where(hp < 0.20, -10.0, 0.0)
            bottom += np.where(dm > 30.0, 8.0, 0.0)
        entry += np.where(ex > 1.2, 5.0, 0.0)
        bottom += np.where(ex < 0.7, 5.0, 0.0)
        bottom = np.clip(bottom, -100.0, 100.0)
        entry = np.clip(entry, -100.0, 100.0)
    bot_cnt = np.zeros(n, dtype=int)
    top_cnt = np.zeros(n, dtype=int)
    for dc_p, kk, wt_sc, wt_ba, wt_bu in per_tf:
        bot_sig = (dc_p < 0.15).astype(int) + (wt_sc < -15.0).astype(int) + (kk < 25.0).astype(int) + ((wt_ba < 12.0) & wt_bu).astype(int)
        bot_cnt += (bot_sig >= 2).astype(int)
        top_sig = (dc_p > 0.85).astype(int) + (wt_sc > 15.0).astype(int) + (kk > 75.0).astype(int) + ((wt_ba < 12.0) & ~wt_bu).astype(int)
        top_cnt += (top_sig >= 2).astype(int)
    scalp_dc = _col(npz, "dc_position_3m", n)
    if scalp_dc is None:
        focus = np.full(n, 0.5)  # live default when the key is missing
    else:
        focus = np.where(_fin(scalp_dc), scalp_dc, 0.5)
    focus = np.where(focus > 1.0, focus / 100.0, focus)
    below = focus < 0.05
    above = focus > 0.95
    near_lo = focus < 0.15
    near_hi = focus > 0.85
    if is_long:
        bounce = (bot_cnt >= 2) & (near_lo | below)
        bottom += np.where(bounce, 10.0 * bot_cnt, 0.0)
        entry += np.where(bounce, 8.0 * bot_cnt, 0.0)
        brk = (~bounce) & (top_cnt >= 2) & above
        bottom += np.where(brk, 15.0 * top_cnt, 0.0)
        entry += np.where(brk, 12.0 * top_cnt, 0.0)
    else:
        rev = (top_cnt >= 2) & (near_hi | above)
        bottom += np.where(rev, 10.0 * top_cnt, 0.0)
        entry += np.where(rev, 8.0 * top_cnt, 0.0)
        brk = (~rev) & (bot_cnt >= 2) & below
        bottom += np.where(brk, 15.0 * bot_cnt, 0.0)
        entry += np.where(brk, 12.0 * bot_cnt, 0.0)
    bottom = np.clip(bottom, -100.0, 100.0)
    entry = np.clip(entry, -100.0, 100.0)
    return bottom, entry


# === 1. MTS_GATE_ENABLED (live :2786-2805) — side-aware pass-mask ===
@_fail_open
def mts_gate_pass_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, bmin: Any = None, eqmin: Any = None) -> np.ndarray | None:
    if not _b(cfg, "MTS_GATE_ENABLED", True):
        return None
    if bmin is None:
        bmin = 10.0 if is_long else 5.0
    if eqmin is None:
        eqmin = 5.0 if is_long else 0.0
    sc = _mts_scores(npz, n, is_long)
    if sc is None:
        return None
    b, e = sc
    return (b >= float(bmin)) & (e >= float(eqmin))


# === 2. MTS_BOTTOM_BONUS_THRESHOLD (live :2813-2814) — pure threshold ===
@_fail_open
def mts_bottom_bonus_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    sc = _mts_scores(npz, n, is_long)
    if sc is None:
        return None
    return sc[0] > _f(cfg, "MTS_BOTTOM_BONUS_THRESHOLD", 25.0)


# === 3. MTS_BOTTOM_STRONG_THRESHOLD (live :2813) — pure threshold ===
@_fail_open
def mts_bottom_strong_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    sc = _mts_scores(npz, n, is_long)
    if sc is None:
        return None
    return sc[0] > _f(cfg, "MTS_BOTTOM_STRONG_THRESHOLD", 40.0)


# === 4. MOVER_THRESHOLD (live :4936-4977) — side-aware mean-reversion trigger ===
@_fail_open
def mover_trigger_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, lin_min: float = 0.3, vol_min: float = 1.0) -> np.ndarray | None:
    thr = _f(cfg, "MOVER_THRESHOLD", 5.0)
    px = _col(npz, "close", n)
    k3 = _col(npz, "k_3m", n)
    k15 = _col(npz, "k_15m", n)
    r3 = _col(npz, "relative_volume_3m", n)
    r15 = _col(npz, "relative_volume_15m", n)
    ema = _col(npz, "ema_20_3m", n)
    hi = _col(npz, "dc_high_3m", n)
    lo = _col(npz, "dc_low_3m", n)
    if px is None or k3 is None or k15 is None or r3 is None or r15 is None or ema is None or hi is None or lo is None:
        return None
    k3p = _col(npz, "k_3m_prev", n)
    if k3p is None:
        k3p = k3  # live defaults prev to current
    k15p = _col(npz, "k_15m_prev", n)
    if k15p is None:
        k15p = k15
    valid = _fin(px) & _fin(k3) & _fin(k3p) & _fin(k15) & _fin(k15p) & _fin(r3) & _fin(r15) & _fin(ema) & _fin(hi) & _fin(lo)
    rng = hi - lo
    ok = valid & (rng > 0.0) & (px > 0.0) & (ema > 0.0)
    dc = np.where(ok, (px - lo) / np.where(rng > 0.0, rng, 1.0), 0.5)
    ema_dev = np.where(ok, (px - ema) / np.where(ema > 0.0, ema, 1.0) * 100.0, 0.0)
    lin = np.minimum((np.abs(k3 - k3p) * 2.0 + np.abs(k15 - k15p)) / 45.0, 1.0)
    slope = ema_dev * (1.0 + np.abs(dc - 0.5) * 2.0)
    boost = 1.0 + 3.0 * lin
    rv = np.maximum(r3, r15)
    vol = np.maximum(np.minimum(rv, 5.0) / 2.0, 0.5)
    score = slope * boost * vol
    base = ok & (lin >= float(lin_min)) & (rv >= float(vol_min))
    if is_long:
        return base & (score < -thr) & (dc < 0.2)
    return base & (score > thr) & (dc > 0.8)


# === 5. REENTRY_POST_CONSOL_MULT (live :43685-43701) — sizing scale ===
@_fail_open
def reentry_post_consol_scale(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, enabled: bool = True, atr_thr: float = 0.15, tfs_req: int = 2) -> np.ndarray | None:
    if not enabled:
        return None
    mult = _f(cfg, "REENTRY_POST_CONSOL_MULT", 1.5)
    a1 = _col(npz, "bar_atr_rank_1h", n)
    a4 = _col(npz, "bar_atr_rank_4h", n)
    ad = _col(npz, "bar_atr_rank_D", n)
    if a1 is None or a4 is None or ad is None:
        return None
    comp = (a1 < float(atr_thr)).astype(int) + (a4 < float(atr_thr)).astype(int) + (ad < float(atr_thr)).astype(int)
    return np.where(comp >= int(tfs_req), mult, 1.0)


# === 6. REENTRY_K15M_PARTIAL_MULT (live :43766-43781) — sizing scale ===
@_fail_open
def reentry_k15m_partial_scale(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, enabled: bool = True, k_thr: float = 90.0) -> np.ndarray | None:
    if not enabled:
        return None
    mult = _f(cfg, "REENTRY_K15M_PARTIAL_MULT", 0.5)
    k = _col(npz, "k_15m", n)
    if k is None:
        return None
    kf = _fin(k)
    if is_long:
        ok = kf & (k < float(k_thr))
    else:
        ok = kf & (k > (100.0 - float(k_thr)))
    return np.where(kf & ~ok, mult, 1.0)


# === 7. REENTRY_WT15M_SIZE_MULT (live :43707-43740) — sizing scale ===
@_fail_open
def reentry_wt15m_scale(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, htf_required: bool = True) -> np.ndarray | None:
    mult = _f(cfg, "REENTRY_WT15M_SIZE_MULT", 1.5)
    w1 = _col(npz, "wt1_15m", n)
    w2 = _col(npz, "wt2_15m", n)
    h1 = _col(npz, "wt1_1h", n)
    h2 = _col(npz, "wt2_1h", n)
    f1 = _col(npz, "wt1_4h", n)
    f2 = _col(npz, "wt2_4h", n)
    if w1 is None or w2 is None or h1 is None or h2 is None or f1 is None or f2 is None:
        return None
    if is_long:
        crossed = (w1 > w2)
        fav = (h1 > h2) | (f1 > f2)
    else:
        crossed = (w1 < w2)
        fav = (h1 < h2) | (f1 < f2)
    trig = crossed & (fav | (not htf_required))
    return np.where(trig, mult, 1.0)


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any) -> np.ndarray | None:
    if switch == "MTS_GATE_ENABLED":
        return mts_gate_pass_mask(npz, n, is_long, cfg, bmin=kw.get("bmin"), eqmin=kw.get("eqmin"))
    if switch == "MTS_BOTTOM_BONUS_THRESHOLD":
        return mts_bottom_bonus_mask(npz, n, is_long, cfg)
    if switch == "MTS_BOTTOM_STRONG_THRESHOLD":
        return mts_bottom_strong_mask(npz, n, is_long, cfg)
    if switch == "MOVER_THRESHOLD":
        return mover_trigger_mask(npz, n, is_long, cfg, lin_min=kw.get("lin_min", 0.3), vol_min=kw.get("vol_min", 1.0))
    if switch == "REENTRY_POST_CONSOL_MULT":
        return reentry_post_consol_scale(npz, n, is_long, cfg, enabled=kw.get("enabled", True), atr_thr=kw.get("atr_thr", 0.15), tfs_req=kw.get("tfs_req", 2))
    if switch == "REENTRY_K15M_PARTIAL_MULT":
        return reentry_k15m_partial_scale(npz, n, is_long, cfg, enabled=kw.get("enabled", True), k_thr=kw.get("k_thr", 90.0))
    if switch == "REENTRY_WT15M_SIZE_MULT":
        return reentry_wt15m_scale(npz, n, is_long, cfg, htf_required=kw.get("htf_required", True))
    raise KeyError(f"unknown mopup3 switch: {switch!r}")
