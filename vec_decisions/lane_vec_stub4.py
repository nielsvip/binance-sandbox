"""lane_vec_stub4.py — numpy twins for STUB-class DEAD switches with PORTABLE semantics (6 switches).

Every twin below ports a CITED rule from legacy/tools/live code — no invented
semantics. Switches needing fresh designs are SKIPPED (see bottom).

Source citations:
  VEC_REENTRY_DC4_EXITPRICE_ENABLED ... v12_wide_engine.py:7698-7715 (USER precise
      reentry rule; arrays :1923-1935; SweepConfig defaults :824-825) +
      vec_paths/v12_reentry_augment_filter_gap_batch4.py vec_reentry_window_mask.
      LONG: wt1_15m>wt2_15m & vel>=0; since<=hb: close crosses dc4_high;
      else: close crosses exit_price. SHORT mirrored. Overdue extension
      (:7716-7762) NOT ported — owned by REENTRY_GR_*/TRADIER_REENTRY_OVERDUE_*.
  FOLLOW_THROUGH_REENTRY_ENABLED ..... per_sym_engine_crypto.py:1186-1193 (walker
      rule; params :165-167: enabled False / min_move 0.05 / window 5;
      identical tools/per_sym_engine_crypto_isolated.py:165-167,:1185-1193;
      stocks reuse the same walker per_sym_engine_stocks.py:37,:716-718).
      After exit, within window bars, favorable move >= min_move*100 (pct
      points) re-enters the same side. BTC pine variant
      (btc_loop_strategy.pine:494-497) uses a different scale + accel legs —
      NOT ported (different switch family).
  CHANNEL_REENTRY_STOP_ENABLED ....... vec_paths/tight_breakout_stops.py:89-119
      (check_channel_reentry; level picker :58-75; TF/field defaults 1h/dc_high;
      batch4 vector form channel_reentry_exit_mask). Stateful ever-outside
      latch + fire on re-entry inside. Latch update is caller state.
  BTC_DEDICATED_FILTER_TF ............ ez_manage.py:6992-6997 (entry filter;
      real-TF set ez_manage.py:6784 = 1h/4h/D/W; default 15m config.py:5153).
      Non-real TF (incl. default 15m) = live skips check = allow-all.
  DELTA_GATE_BB_SQUEEZE .............. tradier_manage.py:16586 (entry veto branch;
      default True config.py:2010). LIVE GATED OFF by `and False` — the branch
      can never fire. Twin returns the WOULD-BLOCK predicate; parent MUST gate
      it (mirror live) or default-True blocks every entry.
  WT_AGAINST_FILTER_ENABLED .......... tradier_manage.py:17613-17617 (LONG B11:
      wt1_15m>wt2_15m else reject; default False). SHORT side never reads it
      (:17619-17632) -> None on SHORT. ez:60814 / tm:35061 reads are audit
      filler (_=_wt), not semantics.

Design (mirrors lane_vec_scalp_v3.py):
  - ENABLED switches -> full cited predicate mask, or None when off (BIBLE 39).
  - Threshold/filter switches -> pure sub-condition mask, gate-free where the
    live site nests the gate (parent ANDs). None ONLY when the required NPZ key
    / caller state is absent — never fabricated.
  - Side-inapplicable predicates (WT_AGAINST on SHORT) -> None.
  - Caller state arrives via kw: exit_px_arr, bars_since_exit_arr,
    prev_close_arr (derived from close via exact roll when omitted),
    candidate_arr (default all-True), ever_outside_arr. Absent -> None.
  - Fail-open: any exception -> None.
Defaults mirror live config.py / config_tradier.py exactly; engine-only knobs
mirror the cited read sites (HOUR_BARS 12; DC_USE_4BAR True per :1923 read-site
fallback — SweepConfig says False, read site wins since config.py lacks the key).

NPZ reality (backtest_v8/indicators/AAPL.npz, 940 keys): close, wt1_15m,
wt2_15m, wt_velocity_15m, wt1_1h/wt2_1h, wt1_4h/wt2_4h, wt1_D/wt2_D,
dc_high_1h, dc_low_1h, bb_upper_1h, bb_lower_1h, stoch_k_15m PRESENT;
dc_high4_5m, dc_low4_5m, dc_high_5m, dc_low_5m, channel_level ABSENT.
Predicates needing absent keys return None on real NPZ and a real mask when
the caller supplies the key.

SKIPPED (no portable rule — honest stop, do not fabricate):
  PARTIAL_EXIT_FRAC — ez_manage.py:60607-60613 is audit filler (`_ = 1` no-op
    gated on frac not in (0,0.5) and unrealizedProfit != 0: no sizing, no exit
    trigger); tm:34925-34926 getattr stub only. No real frac rule inferable.
  AUGMENT_AT_LOSS_ENABLED — config.py:143 debate gate unresolved (OFF by mandate
    until Tier-2 proof + user unlock); no concrete rule to port. The
    v12_quick_engine.py:270 sma_200_1h entry hook is NEW_AUDIT filler, not a
    citable rule — deliberately not ported.
"""

from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np

SUPPORTED = (
    "VEC_REENTRY_DC4_EXITPRICE_ENABLED",
    "FOLLOW_THROUGH_REENTRY_ENABLED",
    "CHANNEL_REENTRY_STOP_ENABLED",
    "BTC_DEDICATED_FILTER_TF",
    "DELTA_GATE_BB_SQUEEZE",
    "WT_AGAINST_FILTER_ENABLED",
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


def _as_bool_arr(x: Any, n: int) -> np.ndarray | None:
    try:
        a = np.asarray(x, dtype=bool).reshape(-1)
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


def _as_int_arr(x: Any, n: int) -> np.ndarray | None:
    try:
        a = np.asarray(x, dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    if not np.all(np.isfinite(a[:n])):
        return None
    return a[:n].astype(int)


# === 1. VEC_REENTRY_DC4_EXITPRICE_ENABLED (v12_wide :7698-7715) ===
# Full fire mask: prior exit AND wt-leg AND (dc4-cross | exit-price-cross).
# Caller state: exit_px_arr + bars_since_exit_arr (required); prev_close_arr
# optional (exact roll of close when omitted).
def vec_dc4_armed(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    try:
        if not _b(cfg, "VEC_REENTRY_DC4_EXITPRICE_ENABLED", True):
            return None
        return np.ones(n, dtype=bool)
    except Exception:
        return None


def vec_dc4_wt_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    try:
        w1 = _col(npz, "wt1_15m", n)
        w2 = _col(npz, "wt2_15m", n)
        vel = _col(npz, "wt_velocity_15m", n)
        if w1 is None or w2 is None or vel is None:
            return None
        ok = np.isfinite(w1) & np.isfinite(w2) & np.isfinite(vel)
        if is_long:
            return ok & (w1 > w2) & (vel >= 0.0)
        return ok & (w1 < w2) & (vel <= 0.0)
    except Exception:
        return None


def vec_dc4_fire_mask(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
    exit_px_arr: Any = None,
    bars_since_exit_arr: Any = None,
    prev_close_arr: Any = None,
) -> np.ndarray | None:
    try:
        if not _b(cfg, "VEC_REENTRY_DC4_EXITPRICE_ENABLED", True):
            return None
        if exit_px_arr is None or bars_since_exit_arr is None:
            return None
        close = _col(npz, "close", n)
        ex = _as_float_arr(exit_px_arr, n)
        since = _as_int_arr(bars_since_exit_arr, n)
        if close is None or ex is None or since is None:
            return None
        if prev_close_arr is None:
            prev = np.roll(close, 1)
            prev[0] = np.nan
        else:
            prev = _as_float_arr(prev_close_arr, n)
            if prev is None:
                return None
        wt = vec_dc4_wt_mask(npz, n, is_long, cfg)
        if wt is None:
            return None
        use4 = _b(cfg, "VEC_REENTRY_DC_USE_4BAR", True)
        if is_long:
            dc = _col(npz, "dc_high4_5m" if use4 else "dc_high_5m", n)
        else:
            dc = _col(npz, "dc_low4_5m" if use4 else "dc_low_5m", n)
        if dc is None:
            return None
        hb = int(_f(cfg, "VEC_REENTRY_HOUR_BARS", 12))
        fin = np.isfinite(close) & np.isfinite(prev) & np.isfinite(dc)
        if is_long:
            dc_trig = fin & (close > dc) & (prev <= dc)
            ex_trig = (
                np.isfinite(close) & np.isfinite(prev) & (close >= ex) & (prev < ex)
            )
        else:
            dc_trig = fin & (close < dc) & (prev >= dc)
            ex_trig = (
                np.isfinite(close) & np.isfinite(prev) & (close <= ex) & (prev > ex)
            )
        trig = np.where(since <= hb, dc_trig, ex_trig)
        trig[0] = False
        return (ex > 0) & wt & trig
    except Exception:
        return None


# === 2. FOLLOW_THROUGH_REENTRY_ENABLED (per_sym_engine_crypto.py:1186-1193) ===
# Within window bars after exit, favorable move >= min_move*100 (pct points)
# re-enters the same side. Caller state: exit_px_arr + bars_since_exit_arr.
def follow_through_fire_mask(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
    exit_px_arr: Any = None,
    bars_since_exit_arr: Any = None,
) -> np.ndarray | None:
    try:
        if not _b(cfg, "FOLLOW_THROUGH_REENTRY_ENABLED", False):
            return None
        if exit_px_arr is None or bars_since_exit_arr is None:
            return None
        close = _col(npz, "close", n)
        ex = _as_float_arr(exit_px_arr, n)
        since = _as_int_arr(bars_since_exit_arr, n)
        if close is None or ex is None or since is None:
            return None
        window = int(_f(cfg, "FOLLOW_THROUGH_WINDOW_BARS", 5))
        thr = _f(cfg, "FOLLOW_THROUGH_MIN_MOVE_PCT", 0.05) * 100.0
        ok = np.isfinite(close) & (ex > 0) & (since >= 1) & (since <= window)
        with np.errstate(divide="ignore", invalid="ignore"):
            if is_long:
                fav = (close - ex) / ex * 100.0
            else:
                fav = (ex - close) / ex * 100.0
        return ok & np.isfinite(fav) & (fav >= thr)
    except Exception:
        return None


# === 3. CHANNEL_REENTRY_STOP_ENABLED (tight_breakout_stops.py:89-119) ===
# Exit leg: candidate AND ever_outside AND back-inside AND level > 0.
# Latch state ever_outside_arr is caller-owned (batch4 ChannelResult form).
def _channel_level(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    tf = str(getattr(cfg, "CHANNEL_REENTRY_STOP_TF", "1h"))
    field = str(getattr(cfg, "CHANNEL_REENTRY_STOP_FIELD", "dc_high"))
    if is_long:
        key = "bb_upper_%s" % tf if field == "bb_upper" else "dc_high_%s" % tf
    else:
        key = "bb_lower_%s" % tf if field == "bb_upper" else "dc_low_%s" % tf
    return _col(npz, key, n)


def channel_reentry_exit_mask(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
    ever_outside_arr: Any = None,
    candidate_arr: Any = None,
) -> np.ndarray | None:
    try:
        if not _b(cfg, "CHANNEL_REENTRY_STOP_ENABLED", False):
            return None
        if ever_outside_arr is None:
            return None
        ever = _as_bool_arr(ever_outside_arr, n)
        if ever is None:
            return None
        close = _col(npz, "close", n)
        level = _channel_level(npz, n, is_long, cfg)
        if close is None or level is None:
            return None
        if candidate_arr is None:
            cand = np.ones(n, dtype=bool)
        else:
            cand = _as_bool_arr(candidate_arr, n)
            if cand is None:
                return None
        fin = np.isfinite(close) & np.isfinite(level) & (level > 0)
        if is_long:
            outside = close > level
            inside = close < level
        else:
            outside = close < level
            inside = close > level
        return cand & ever & fin & ~outside & inside
    except Exception:
        return None


def channel_reentry_new_ever(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
    ever_outside_arr: Any = None,
) -> np.ndarray | None:
    try:
        if ever_outside_arr is None:
            return None
        ever = _as_bool_arr(ever_outside_arr, n)
        if ever is None:
            return None
        close = _col(npz, "close", n)
        level = _channel_level(npz, n, is_long, cfg)
        if close is None or level is None:
            return None
        fin = np.isfinite(close) & np.isfinite(level) & (level > 0)
        if is_long:
            outside = fin & (close > level)
        else:
            outside = fin & (close < level)
        return ever | outside
    except Exception:
        return None


# === 4. BTC_DEDICATED_FILTER_TF (ez :6992-6997; real set ez :6784) ===
# Entry allow-mask: LONG wt1_tf > wt2_tf / SHORT wt1_tf < wt2_tf.
# TF outside {1h,4h,D,W} (incl. default 15m) -> live skips the check -> allow-all.
def btc_dedicated_allow_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    try:
        tf = str(getattr(cfg, "BTC_DEDICATED_FILTER_TF", "15m"))
        if tf not in ("1h", "4h", "D", "W"):
            return np.ones(n, dtype=bool)
        w1 = _col(npz, "wt1_%s" % tf, n)
        w2 = _col(npz, "wt2_%s" % tf, n)
        if w1 is None or w2 is None:
            return None
        ok = np.isfinite(w1) & np.isfinite(w2)
        if is_long:
            return ok & (w1 > w2)
        return ok & (w1 < w2)
    except Exception:
        return None


# === 5. DELTA_GATE_BB_SQUEEZE (tm :16586; default True) ===
# LIVE GATED OFF by `and False`: the branch can never fire. Twin returns the
# WOULD-BLOCK predicate (block-all when enabled); parent MUST gate it.
def delta_gate_bb_squeeze_block_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    try:
        if not _b(cfg, "DELTA_GATE_BB_SQUEEZE", True):
            return None
        return np.ones(n, dtype=bool)
    except Exception:
        return None


# === 6. WT_AGAINST_FILTER_ENABLED (tm :17613-17617; LONG-only) ===
# LONG B11 allow: wt1_15m > wt2_15m. SHORT never read live -> None.
def wt_against_allow_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    try:
        if not is_long:
            return None
        if not _b(cfg, "WT_AGAINST_FILTER_ENABLED", False):
            return None
        w1 = _col(npz, "wt1_15m", n)
        w2 = _col(npz, "wt2_15m", n)
        if w1 is None or w2 is None:
            return None
        return np.isfinite(w1) & np.isfinite(w2) & (w1 > w2)
    except Exception:
        return None


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(
    switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any
) -> np.ndarray | None:
    if switch == "VEC_REENTRY_DC4_EXITPRICE_ENABLED":
        return vec_dc4_fire_mask(
            npz,
            n,
            is_long,
            cfg,
            exit_px_arr=kw.get("exit_px_arr"),
            bars_since_exit_arr=kw.get("bars_since_exit_arr"),
            prev_close_arr=kw.get("prev_close_arr"),
        )
    if switch == "FOLLOW_THROUGH_REENTRY_ENABLED":
        return follow_through_fire_mask(
            npz,
            n,
            is_long,
            cfg,
            exit_px_arr=kw.get("exit_px_arr"),
            bars_since_exit_arr=kw.get("bars_since_exit_arr"),
        )
    if switch == "CHANNEL_REENTRY_STOP_ENABLED":
        return channel_reentry_exit_mask(
            npz,
            n,
            is_long,
            cfg,
            ever_outside_arr=kw.get("ever_outside_arr"),
            candidate_arr=kw.get("candidate_arr"),
        )
    if switch == "BTC_DEDICATED_FILTER_TF":
        return btc_dedicated_allow_mask(npz, n, is_long, cfg)
    if switch == "DELTA_GATE_BB_SQUEEZE":
        return delta_gate_bb_squeeze_block_mask(npz, n, is_long, cfg)
    if switch == "WT_AGAINST_FILTER_ENABLED":
        return wt_against_allow_mask(npz, n, is_long, cfg)
    raise KeyError(f"unknown stub4 switch: {switch!r}")
