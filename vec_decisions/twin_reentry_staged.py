"""Shared pure predicates for 11 staged reentry/augment switches (twin module).

Each predicate below is a faithful scalar/array twin of staged-or-live logic found
in this checkout. Getter param is ALWAYS named ``get``; every ``get`` call uses a
literal key. Predicates return None when their master switch is OFF (inert,
BACKTEST_BIBLE section 19/39: an honest non-firing, never a stub). Comparison
operators are ``&``/``|``/``~`` only so the SAME code serves live scalars and
numpy vec masks.

REFERENCE LOGIC (all verified in this checkout):
  REENTRY_PULL1..4 : v12_quick_engine.py:8343-8393 B_PULL1..4 blocks inside
                     compute_reentry_blocks (REACHABLE via v12:9073 _base_entry
                     and v12:12669 loop cache). Twin scalarizes the staged vec
                     formulas exactly (exec-proven in test_reentry_staged_twin.py).
  BREAKOUT_LEASH_REENTRY_MULT : ez_reentry.py:723 sizing mult for leash
                     reentries (live, both venues via shared module).
  BB_BREAKOUT_SCORE : ez_positions_quick.py:4507-4517 AdvancedSignalRater.rate
                     (live crypto, full gate) + tradier_manage.py:12705-12711
                     (live stocks, pct-only gate).
  DC_HOPELESS_EXIT_ENABLED : ez_manage.py:50164-50215 process_position
                     (live crypto). Stocks: missing (hook adds).
  WT_4H_VEL_EXIT_ENABLED : ez_manage.py:50087-50162 process_position
                     (live crypto). Stocks: missing (hook adds).
  AUGMENT_FALLBACK_GAIN_PCT / _REDUCE_ENABLED / _REDUCE_PCT : semantics from
                     config_tradier.py:3567-3569 comments ("fallback threshold
                     pct from peak", "fraction of last augment to reduce on
                     fallback"). Live sites are dead stubs (ez_manage.py:58925-
                     58940 inside never-called _ensure_ez_all; tradier:33425-
                     33436); vec has no reads. Twin DEFINES the honest behavior.

STAGED VEC CODE DELIBERATELY *NOT* TWINNED (fake-audit class, BIBLE section 19):
  v12:416-421 (BREAKOUT_LEASH gates entry on ``close>0`` no-op) and v12:586-594
  (DC_HOPELESS gates entry_mask for an EXIT switch, ``_=`` reads) both sit in
  _batch1_template_wiring (v12:193), which has no callers (DEAD). Calling them
  reachable would fabricate deltas. Operator should DELETE them, not unstage.

KEY MAPPING (live indicator dicts <-> NPZ arrays): the vec staged code reads
base-TF keys (e.g. ``stoch_k_3m`` resolves to ``stoch_k_15m`` when BASE_TF=15m).
The twin uses canonical live indicator names (``stoch_k_3m``,
``wt_velocity_3m``, ...); vec callers map keys to arrays, live callers pass
``ind.get``. FIELD GAP (REPORT): neither venue tracks ``stoch_k_3m_prev2``;
when missing the twin degrades the PULL1 reversal clause to a 1-bar turn
(fail-open by one clause). Live must supply prev2 for exact vec parity.
"""
import numpy as np


__all__ = [
    "reentry_pull1_fires",
    "reentry_pull2_fires",
    "reentry_pull3_fires",
    "reentry_pull4_fires",
    "breakout_leash_reentry_mult",
    "breakout_leash_reentry_qty",
    "bb_breakout_fires",
    "bb_breakout_score",
    "dc_hopeless_exit_fires",
    "wt_4h_vel_exit_fires",
    "augment_fallback_fires",
    "augment_fallback_reduce_frac",
]


def _f(get, key, default):
    """Float fetch mirroring utils.safe_fetch_float + ndarray passthrough."""
    try:
        v = get(key, default)
    except Exception:
        return default
    if v is None:
        return default
    if isinstance(v, np.ndarray):
        return v.astype(np.float64)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return default
        try:
            return float(s)
        except Exception:
            return default
    try:
        return float(v)
    except Exception:
        return default


def _b(get, key, default):
    """Bool fetch: live config passes real bools; sheet strings coerced."""
    try:
        v = get(key, default)
    except Exception:
        return bool(default)
    if v is None:
        return bool(default)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "t", "yes", "y", "on"):
            return True
        if s in ("0", "false", "f", "no", "n", "off", ""):
            return False
        return True
    return bool(v)


def _s(get, key, default):
    """String fetch for TF-style config values."""
    try:
        v = get(key, default)
    except Exception:
        return str(default)
    if v is None:
        return str(default)
    try:
        return str(v)
    except Exception:
        return str(default)


def _ha_num(v):
    """Heikin-Ashi to number: live uses 'green'/'red' strings, vec uses ints."""
    if isinstance(v, np.ndarray):
        return v.astype(np.float64)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("green", "bull", "bullish", "up", "long", "1"):
            return 1.0
        if s in ("red", "bear", "bearish", "down", "short", "-1"):
            return -1.0
        if s in ("neutral", "none", ""):
            return 0.0
        try:
            return float(s)
        except Exception:
            return 0.0
    if v is None:
        return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


def _ha_eq(v, want):
    return _ha_num(v) == want


def reentry_pull1_fires(get, is_long):
    """Twin of staged vec B_PULL1 (v12:8342-8354): all-HTF trend + deep LTF
    pullback + active reversal. Live gate default False (config.py:51)."""
    if not _b(get, "REENTRY_PULL1_ENABLED", False):
        return None
    k = _f(get, "stoch_k_3m", 50.0)
    kp = _f(get, "stoch_k_3m_prev", k)
    kp2 = _f(get, "stoch_k_3m_prev2", k)
    wvel = _f(get, "wt_velocity_3m", 0.0)
    w1_3m = _f(get, "wt1_3m", 0.0)
    if is_long:
        htf = (_f(get, "wt1_1h", 0.0) > _f(get, "wt2_1h", 0.0)) & (_f(get, "wt1_4h", 0.0) > _f(get, "wt2_4h", 0.0)) & (_f(get, "wt1_D", 0.0) > _f(get, "wt2_D", 0.0)) & _ha_eq(get("ha_D", 0), 1) & (_f(get, "stoch_k_1h", 50.0) < 70.0)
        deep = (k < 20.0) & (w1_3m < -35.0)
        reversing = (k > kp) & (wvel > 0.0) & (kp < kp2)
        return htf & deep & reversing
    htf = (_f(get, "wt1_1h", 0.0) < _f(get, "wt2_1h", 0.0)) & (_f(get, "wt1_4h", 0.0) < _f(get, "wt2_4h", 0.0)) & (_f(get, "wt1_D", 0.0) < _f(get, "wt2_D", 0.0)) & _ha_eq(get("ha_D", 0), -1) & (_f(get, "stoch_k_1h", 50.0) > 30.0)
    deep = (k > 80.0) & (w1_3m > 35.0)
    reversing = (k < kp) & (wvel < 0.0) & (kp > kp2)
    return htf & deep & reversing


def reentry_pull2_fires(get, is_long, px):
    """Twin of staged vec B_PULL2 (v12:8356-8367): rising HTF fundamentals +
    pullback to SMA200_1h + momentum returning. ``px`` = mark price (live) or
    close array (vec). Live gate default False (config.py:52)."""
    if not _b(get, "REENTRY_PULL2_ENABLED", False):
        return None
    k = _f(get, "stoch_k_3m", 50.0)
    d = _f(get, "stoch_d_3m", 50.0)
    wvel = _f(get, "wt_velocity_3m", 0.0)
    sma = _f(get, "sma_200_1h", 0.0)
    if is_long:
        rising = (_f(get, "wt_velocity_D", 0.0) > 1.0) & (_f(get, "wt_velocity_4h", 0.0) > 0.0) & _ha_eq(get("ha_D", 0), 1) & (_ha_num(get("ha_4h", 0)) >= 0)
        near = (sma > 0.0) & (px < sma * 1.01) & (px > sma * 0.98)
        mom = (k > d) & (k < 35.0) & (wvel > 0.0)
        return rising & near & mom
    falling = (_f(get, "wt_velocity_D", 0.0) < -1.0) & (_f(get, "wt_velocity_4h", 0.0) < 0.0) & _ha_eq(get("ha_D", 0), -1) & (_ha_num(get("ha_4h", 0)) <= 0)
    near = (sma > 0.0) & (px > sma * 0.99) & (px < sma * 1.02)
    mom = (k < d) & (k > 65.0) & (wvel < 0.0)
    return falling & near & mom


def reentry_pull3_fires(get, is_long):
    """Twin of staged vec B_PULL3 (v12:8369-8380): BB extreme band in strict
    trend + stoch cross. Live gate default False (config.py:53)."""
    if not _b(get, "REENTRY_PULL3_ENABLED", False):
        return None
    k = _f(get, "stoch_k_3m", 50.0)
    kp = _f(get, "stoch_k_3m_prev", k)
    d = _f(get, "stoch_d_3m", 50.0)
    bb = _f(get, "bb_pct_b_1h", 0.5)
    if is_long:
        trend = (_f(get, "wt1_1h", 0.0) > _f(get, "wt2_1h", 0.0)) & (_f(get, "wt1_4h", 0.0) > _f(get, "wt2_4h", 0.0)) & _ha_eq(get("ha_D", 0), 1)
        band = bb < 0.15
        turn = (kp <= d) & (k > d) & (k < 30.0)
        return trend & band & turn
    trend = (_f(get, "wt1_1h", 0.0) < _f(get, "wt2_1h", 0.0)) & (_f(get, "wt1_4h", 0.0) < _f(get, "wt2_4h", 0.0)) & _ha_eq(get("ha_D", 0), -1)
    band = bb > 0.85
    turn = (kp >= d) & (k < d) & (k > 70.0)
    return trend & band & turn


def reentry_pull4_fires(get, is_long):
    """Twin of staged vec B_PULL4 (v12:8382-8393): RSI extreme pullback in
    trend + 3m WT bouncing. Live gate default False (config.py:54)."""
    if not _b(get, "REENTRY_PULL4_ENABLED", False):
        return None
    wvel = _f(get, "wt_velocity_3m", 0.0)
    w1_3m = _f(get, "wt1_3m", 0.0)
    w1_15m = _f(get, "wt1_15m", 0.0)
    if is_long:
        rsi = _f(get, "rsi_1h", 50.0) < 35.0
        htf = _ha_eq(get("ha_4h", 0), 1) & _ha_eq(get("ha_D", 0), 1)
        bounce = (wvel > 0.0) & (w1_3m < -15.0) & (w1_3m > w1_15m * 0.7)
        return rsi & htf & bounce
    rsi = _f(get, "rsi_1h", 50.0) > 65.0
    htf = _ha_eq(get("ha_4h", 0), -1) & _ha_eq(get("ha_D", 0), -1)
    roll = (wvel < 0.0) & (w1_3m > 15.0) & (w1_3m < w1_15m * 1.3)
    return rsi & htf & roll


def breakout_leash_reentry_mult(get):
    """Twin of live leash-reentry sizing (ez_reentry.py:723): the mult applied
    to fire_qty for BREAKOUT_LEASH reentries. Default 1.5 (config.py:908)."""
    return _f(get, "BREAKOUT_LEASH_REENTRY_MULT", 1.5)


def breakout_leash_reentry_qty(get, base_qty):
    """Fire qty = base * mult (mirrors ez_reentry.py:745 ``* sizing_frac``).
    Non-positive results are skipped by the caller (ez_reentry.py:746-747)."""
    return base_qty * breakout_leash_reentry_mult(get)


def bb_breakout_fires(get, is_long, px):
    """Twin of live BB-breakout gate (ez_positions_quick.py:4508-4517):
    BB_BREAKOUT_ENABLED + TF select + ADX>25 trending + price beyond band and
    beyond SMA200. Keys stay literal: TF selects among fixed key sets."""
    if not _b(get, "BB_BREAKOUT_ENABLED", False):
        return None
    tf = _s(get, "BB_BREAKOUT_TF", "1h").strip().lower()
    if tf in ("off", "false", "0", "none", ""):
        return None
    if tf == "15m":
        pct = _f(get, "bb_pct_b_15m", 0.5)
        sma = _f(get, "sma_200_15m", 0.0)
        adx = _f(get, "adx_15m", 0.0)
    elif tf == "4h":
        pct = _f(get, "bb_pct_b_4h", 0.5)
        sma = _f(get, "sma_200_4h", 0.0)
        adx = _f(get, "adx_4h", 0.0)
    elif tf in ("d", "1d", "day", "daily"):
        pct = _f(get, "bb_pct_b_D", 0.5)
        sma = _f(get, "sma_200_D", 0.0)
        adx = _f(get, "adx_D", 0.0)
    else:
        pct = _f(get, "bb_pct_b_1h", 0.5)
        sma = _f(get, "sma_200_1h", 0.0)
        adx = _f(get, "adx_1h", 0.0)
    trending = (adx > 25.0) & (sma > 0.0)
    if is_long:
        return trending & (pct > 1.0) & (px > sma)
    return trending & (pct < 0.0) & (px < sma)


def bb_breakout_score(get):
    """Score bonus added when bb_breakout_fires (live: ez_positions_quick.py:
    4515,4517 ``score += getattr(config, 'BB_BREAKOUT_SCORE', 20)``)."""
    return int(_f(get, "BB_BREAKOUT_SCORE", 20))


def dc_hopeless_exit_fires(get, is_long, entry_px, age_s):
    """Twin of live DC_HOPELESS exit (ez_manage.py:50170-50200): entry price is
    outside the 4h Donchian channel (structure failed) and the position is
    older than DC_HOPELESS_EXIT_MIN_AGE_S (default 900)."""
    if not _b(get, "DC_HOPELESS_EXIT_ENABLED", False):
        return None
    hi = _f(get, "dc_high_4h", 0.0)
    lo = _f(get, "dc_low_4h", 0.0)
    min_age = _f(get, "DC_HOPELESS_EXIT_MIN_AGE_S", 900.0)
    valid = (hi > 0.0) & (lo > 0.0) & (entry_px > 0.0)
    if is_long:
        hopeless = entry_px > hi
    else:
        hopeless = entry_px < lo
    return valid & hopeless & (age_s > min_age)


def wt_4h_vel_exit_fires(get, is_long, age_s, gain_pct):
    """Twin of live WT_4H_VEL exit (ez_manage.py:50087-50142): 4h WT velocity
    against the side + age>360s + profit floor + stoch-K extreme confirm."""
    if not _b(get, "WT_4H_VEL_EXIT_ENABLED", False):
        return None
    vel = _f(get, "wt_velocity_4h", 0.0)
    if is_long:
        against = vel < _f(get, "WT_4H_VEL_EXIT_LONG_VEL_MIN", -2.0)
    else:
        against = vel > _f(get, "WT_4H_VEL_EXIT_SHORT_VEL_MIN", 2.0)
    age_ok = age_s > 360.0
    if _b(get, "WT_4H_VEL_EXIT_REQUIRE_PROFIT", True):
        profit_ok = gain_pct >= _f(get, "COMMISSION_BUFFER_PCT", 0.10)
    else:
        profit_ok = True
    if _b(get, "WT_4H_VEL_EXIT_REQUIRE_K_EXTREME", True):
        hi = _f(get, "WT_4H_VEL_EXIT_K_EXTREME_HIGH", 80.0)
        lo = _f(get, "WT_4H_VEL_EXIT_K_EXTREME_LOW", 20.0)
        k3 = _f(get, "k_3m", 50.0)
        k15 = _f(get, "k_15m", 50.0)
        if is_long:
            kx_ok = (k3 >= hi) | (k15 >= hi)
        else:
            kx_ok = (k3 <= lo) | (k15 <= lo)
    else:
        kx_ok = True
    return against & age_ok & profit_ok & kx_ok


def augment_fallback_fires(get, gain_pct, peak_gain_pct):
    """Gain fell back from peak by >= AUGMENT_FALLBACK_GAIN_PCT (default 1.0
    %-points, same unit as live position gain). Semantics: config_tradier.py:
    3569 'fallback threshold pct from peak'. Gate-free by design: the
    threshold test itself is the trigger; REDUCE gating lives in
    augment_fallback_reduce_frac."""
    thr = _f(get, "AUGMENT_FALLBACK_GAIN_PCT", 1.0)
    return (peak_gain_pct - gain_pct) >= thr


def augment_fallback_reduce_frac(get):
    """Fraction of the last augment to reduce on fallback (config_tradier.py:
    3568). 0.0 unless AUGMENT_FALLBACK_REDUCE_ENABLED (default False)."""
    if not _b(get, "AUGMENT_FALLBACK_REDUCE_ENABLED", False):
        return 0.0
    return _f(get, "AUGMENT_FALLBACK_REDUCE_PCT", 0.5)
