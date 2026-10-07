"""lane_vec_gapmoc.py — numpy twins for the GAP/EOD morning-reentry leg (Tradier stocks).

Live citations (tradier_manage.py — gap-aware loop + predicates):
  loop/weekend skip ................... :9916-9925 (weekday>=5 continue)
  mins_since_open ...................... :9933 ((h*60+m) - 570, ET, minute-truncated)
  morning window + ENABLED gate ........ :9947-9948 (0<=m<=GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN)
  vv skip ("not attractive") ........... :9966-9971 (via _is_near_dc4_high_with_wt_down)
  wt_ok / ha_ok ........................ :9972-9973 (wt1_15m>/<wt2_15m, ha_15m=='green'/'red')
  dip_ok (opposite-side small-top) ..... :9974 (via _is_small_top_for_gap_exit, not is_long)
  dc_breakout (spec E, corrected) ...... :9975-9982 (long: cur>dc_high; short: cur<dc_low)
  force_at_end ......................... :9983-9984 (m >= window-1, inside window branch)
  fire OR .............................. :9985 (wt_ok or ha_ok or dip_ok or dc or force)
  _is_small_top_for_gap_exit ........... :9501-9529 (wt15/wt5/ha/px/dc legs, REQUIRE_TOP)
  _is_near_dc4_high_with_wt_down ....... :9620-9642 (dc_4h edge + WT against, PROXIMITY_PCT)
Evening exit leg (gap-sentinel preamble, v12_quick_engine.py :11912-12038)
is the sibling vector hook; this module is the morning-reentry parity leg.

Design (mirrors lane_vec_scalp_v3.py):
  - ENABLED switches -> armed mask (all-True) or None when off (BIBLE 39).
  - Threshold/predicate switches -> pure sub-condition mask, gate-free except
    where the live predicate itself bears the gate (REQUIRE_TOP all-True when
    off per :9506; VV False/None when SAFETY off per :9621).
  - None ONLY when required keys are absent — never fabricated. Composite OR
    legs return None only when NO leg is computable (a missing leg equals live
    False inside the live OR, so skipping it is exact).
  - ha_15m/ha_5m accept int encoding (1=green, -1=red, v12 _ha_int :4021) and
    str encoding ('green'/'red'). Morning ha_ok uses live-exact == (:9973);
    small-top ha legs use live-exact .lower() (:9511/:9522).
  - Time masks use exact ET wall-clock from bar timestamps (live :9933,
    minute-truncated, weekday<5). ts absent/unparseable -> modular grid
    fallback (v12-preamble style :12000-12006), documented approximation.
  - Live `or`-chains are falsy-fallbacks (0/None/missing -> next). Vector
    mirrors with nonzero/non-nan selection. NPZ ADAPTATION (flagged): live
    price chains (current_price->close_3m->close_5m, :9514/:9976; vv
    current_price->close_5m, :9626) have a last-resort 'close' fallback here
    because frozen stock NPZ carry only base-tf 'close' (AAPL: 940 keys, no
    close_5m/close_3m/current_price). Without it the dc legs would be dead on
    every real stock NPZ. close_5m_prev missing -> one-5m-bar shift of
    close_5m (approximation, flagged); both missing -> leg skipped (= live
    False per :9512/:9523 guard).

NOT ported (stateful, orchestrator-owned): _GAP_MOC_PENDING_REENTRY add/drop
(vv skip DROPS pending :9969, holds retry :9995), _morning_done_today one-shot
(:9947/:10000-10002), Friday->Monday disk persistence (:9909). A mask cannot
express drop-vs-hold; the caller wires window & trigger & ~vv per bar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
import math

import numpy as np

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None

SUPPORTED = (
    "GAP_MORNING_REENTRY_ENABLED",
    "GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN",
    "GAP_MOC_REQUIRE_TOP",
    "GAP_MOC_DC_WT_SAFETY_ENABLED",
    "GAP_MOC_DC_PROXIMITY_PCT",
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


def _col_first(npz: Mapping[str, Any], keys: tuple, n: int) -> np.ndarray | None:
    """First present key (live `or`-chain across MISSING keys)."""
    for k in keys:
        a = _col(npz, k, n)
        if a is not None:
            return a
    return None


def _or_fallback(a: np.ndarray | None, b: np.ndarray | None) -> np.ndarray | None:
    """Live `x or y` per-bar: y wins where x is falsy (0/NaN)."""
    if a is None:
        return b
    if b is None:
        return a
    with np.errstate(invalid="ignore"):
        use_b = (a == 0) | ~np.isfinite(a)
    out = a.copy()
    out[use_b] = b[use_b]
    return out


def _raw(npz: Mapping[str, Any], key: str, n: int) -> np.ndarray | None:
    """Raw column (keeps dtype — for str/int ha); None when absent."""
    if not _have(npz, key):
        return None
    try:
        a = np.asarray(npz[key]).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return a[:n]


def _ha_is(arr: np.ndarray | None, want_green: bool, exact: bool) -> np.ndarray | None:
    """ha leg; None when key absent. int: 1=green/-1=red; str: live compare."""
    if arr is None:
        return None
    try:
        if arr.dtype.kind in ("i", "u", "f", "b"):
            v = np.asarray(arr, dtype=float)
            return np.isfinite(v) & (v == (1.0 if want_green else -1.0))
        want = "green" if want_green else "red"
        out = np.zeros(arr.shape[0], dtype=bool)
        for i, x in enumerate(arr.tolist()):
            s = str(x)
            out[i] = (s == want) if exact else (s.lower() == want)
        return out
    except Exception:
        return None


def _et_fields(ts: Any, n: int) -> tuple:
    """(mins_since_open, weekday) per live :9933, or (None, None) fail-open."""
    if ts is None or _ET is None or n <= 0:
        return None, None
    try:
        a = np.asarray(ts, dtype=float).reshape(-1)
    except Exception:
        return None, None
    if a.size < n:
        return None, None
    a = a[:n]
    if not bool(np.all(np.isfinite(a))):
        return None, None
    try:
        m = np.empty(n, dtype=np.int32)
        w = np.empty(n, dtype=np.int8)
        for i in range(n):
            dt = datetime.fromtimestamp(float(a[i]), tz=_ET)
            m[i] = dt.hour * 60 + dt.minute - 570
            w[i] = dt.weekday()
        return m, w
    except Exception:
        return None, None


def _window_min(cfg: Any) -> float:
    return _f(cfg, "GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN", 120.0)


def _modular_minutes(n: int, bmin: int, bars_per_day: int | None) -> np.ndarray:
    bpd = (
        bars_per_day
        if bars_per_day and bars_per_day > 0
        else max(1, round(390.0 / max(bmin, 1)))
    )
    return (np.arange(n, dtype=np.int32) % bpd) * bmin


# === GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN (live :9947) — morning window ===
def morning_window_mask(
    ts: Any, n: int, bmin: int = 15, bars_per_day: int | None = None, cfg: Any = None
) -> np.ndarray | None:
    window = _window_min(cfg)
    m, w = _et_fields(ts, n)
    if m is not None:
        return (w < 5) & (m >= 0) & (m <= window)
    if n <= 0:
        return None
    me = _modular_minutes(n, bmin, bars_per_day)
    return (me >= 0) & (me <= window)


# === force_at_end (live :9984, inside window branch :9947) ===
def force_at_end_mask(
    ts: Any, n: int, bmin: int = 15, bars_per_day: int | None = None, cfg: Any = None
) -> np.ndarray | None:
    window = _window_min(cfg)
    m, w = _et_fields(ts, n)
    if m is not None:
        return (w < 5) & (m >= 0) & (m <= window) & (m >= window - 1)
    if n <= 0:
        return None
    me = _modular_minutes(n, bmin, bars_per_day)
    return (me >= 0) & (me <= window) & (me >= window - 1)


# === GAP_MORNING_REENTRY_ENABLED (live :9948) — armed ===
def morning_reentry_armed(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    if not _b(cfg, "GAP_MORNING_REENTRY_ENABLED", True):
        return None
    return np.ones(n, dtype=bool)


def _cur_price(npz: Mapping[str, Any], n: int, extra_5m: bool) -> np.ndarray | None:
    """Live cur chain :9514/:9976 + NPZ 'close' last resort (flagged)."""
    keys = (
        ("current_price", "close_3m", "close_5m")
        if extra_5m
        else ("current_price", "close_5m")
    )
    a = _col_first(npz, keys, n)
    c = _col(npz, "close", n)
    if a is None:
        return c
    if c is None:
        return a
    return _or_fallback(a, c)


# === _is_small_top_for_gap_exit twin, same side (live :9501-9529) ===
def small_top_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    if not _b(cfg, "GAP_MOC_REQUIRE_TOP", True):
        return np.ones(n, dtype=bool)
    legs: list = []
    w1_15 = _col(npz, "wt1_15m", n)
    w2_15 = _col(npz, "wt2_15m", n)
    if w1_15 is not None and w2_15 is not None:
        legs.append((w1_15 < w2_15) if is_long else (w1_15 > w2_15))
    w1_5 = _col(npz, "wt1_5m", n)
    w2_5 = _col(npz, "wt2_5m", n)
    if w1_5 is not None and w2_5 is not None:
        legs.append((w1_5 < w2_5) if is_long else (w1_5 > w2_5))
    ha15 = _ha_is(_raw(npz, "ha_15m", n), want_green=(not is_long), exact=False)
    ha5 = _ha_is(_raw(npz, "ha_5m", n), want_green=(not is_long), exact=False)
    ha_legs = [x for x in (ha15, ha5) if x is not None]
    if ha_legs:
        ha = ha_legs[0]
        for x in ha_legs[1:]:
            ha = ha | x
        legs.append(ha)
    c5 = _col(npz, "close_5m", n)
    c5p = _col(npz, "close_5m_prev", n)
    if c5 is not None:
        if c5p is None:
            c5p = np.concatenate(([np.nan], c5[:-1]))
        with np.errstate(invalid="ignore"):
            has_prev = np.isfinite(c5p) & (c5p != 0)
            px = has_prev & ((c5 < c5p) if is_long else (c5 > c5p))
        legs.append(px)
    cur = _cur_price(npz, n, True)
    dc = _col_first(
        npz,
        ("dc_low_4h_3m", "dc_low_4h") if is_long else ("dc_high_4h_3m", "dc_high_4h"),
        n,
    )
    if cur is not None and dc is not None:
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(cur) & np.isfinite(dc) & (cur > 0) & (dc > 0)
            legs.append(ok & ((cur < dc) if is_long else (cur > dc)))
    if not legs:
        return None
    out = legs[0]
    for x in legs[1:]:
        out = out | x
    return out


# === dc_breakout, spec E corrected (live :9975-9982) ===
def dc_breakout_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    cur = _cur_price(npz, n, True)
    dc = _col_first(
        npz,
        ("dc_high_4h_3m", "dc_high_4h") if is_long else ("dc_low_4h_3m", "dc_low_4h"),
        n,
    )
    if cur is None or dc is None:
        return None
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(cur) & np.isfinite(dc) & (cur > 0) & (dc > 0)
        return ok & ((cur > dc) if is_long else (cur < dc))


# === GAP_MOC_REQUIRE_TOP (live :9974 + :9975-9982) — dip_ok ===
def dip_ok_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    opp = small_top_mask(npz, n, not is_long, cfg)
    dc = dc_breakout_mask(npz, n, is_long, cfg)
    if opp is None:
        return dc
    if dc is None:
        return opp
    return opp | dc


# === wt_ok (live :9972) ===
def wt_ok_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    w1 = _col(npz, "wt1_15m", n)
    w2 = _col(npz, "wt2_15m", n)
    if w1 is None or w2 is None:
        return None
    return (w1 > w2) if is_long else (w1 < w2)


# === ha_ok (live :9973, exact ==) ===
def ha_ok_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    return _ha_is(_raw(npz, "ha_15m", n), want_green=is_long, exact=True)


# === _is_near_dc4_high_with_wt_down twin (live :9620-9642) — vv danger ===
def vv_danger_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any
) -> np.ndarray | None:
    if not _b(cfg, "GAP_MOC_DC_WT_SAFETY_ENABLED", True):
        return None
    prox = _f(cfg, "GAP_MOC_DC_PROXIMITY_PCT", 0.5)
    if is_long:
        dc = _or_fallback(_col(npz, "dc_high_4h", n), _col(npz, "dc_high_4h_ant", n))
    else:
        dc = _or_fallback(_col(npz, "dc_low_4h", n), _col(npz, "dc_low_4h_ant", n))
    px = _cur_price(npz, n, False)
    if dc is None or px is None:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        valid = np.isfinite(dc) & np.isfinite(px) & (dc != 0) & (px != 0)
        pct = np.where(
            valid, (dc - px) / px * 100.0 if is_long else (px - dc) / px * 100.0, np.inf
        )
        near = valid & (pct <= prox)
    w1_15 = _col(npz, "wt1_15m", n)
    w2_15 = _col(npz, "wt2_15m", n)
    w1_1h = _col(npz, "wt1_1h", n)
    w2_1h = _col(npz, "wt2_1h", n)
    wt_legs = []
    if w1_15 is not None and w2_15 is not None:
        wt_legs.append((w1_15 < w2_15) if is_long else (w1_15 > w2_15))
    if w1_1h is not None and w2_1h is not None:
        wt_legs.append((w1_1h < w2_1h) if is_long else (w1_1h > w2_1h))
    if not wt_legs:
        return np.zeros(n, dtype=bool)
    against = wt_legs[0]
    for x in wt_legs[1:]:
        against = against | x
    return near & against


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(
    switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any
) -> np.ndarray | None:
    if switch == "GAP_MORNING_REENTRY_ENABLED":
        return morning_reentry_armed(npz, n, is_long, cfg)
    if switch == "GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN":
        return morning_window_mask(
            kw.get("ts"), n, kw.get("bmin", 15), kw.get("bars_per_day"), cfg
        )
    if switch == "GAP_MOC_REQUIRE_TOP":
        return dip_ok_mask(npz, n, is_long, cfg)
    if switch == "GAP_MOC_DC_WT_SAFETY_ENABLED":
        if not _b(cfg, "GAP_MOC_DC_WT_SAFETY_ENABLED", True):
            return None
        return np.ones(n, dtype=bool)
    if switch == "GAP_MOC_DC_PROXIMITY_PCT":
        return vv_danger_mask(npz, n, is_long, cfg)
    if switch == "GAP_MORNING_WINDOW":
        return morning_window_mask(
            kw.get("ts"), n, kw.get("bmin", 15), kw.get("bars_per_day"), cfg
        )
    if switch == "GAP_MORNING_FORCE_AT_END":
        return force_at_end_mask(
            kw.get("ts"), n, kw.get("bmin", 15), kw.get("bars_per_day"), cfg
        )
    if switch == "GAP_MORNING_WT_OK":
        return wt_ok_mask(npz, n, is_long, cfg)
    if switch == "GAP_MORNING_HA_OK":
        return ha_ok_mask(npz, n, is_long, cfg)
    if switch == "GAP_MORNING_DIP_OK":
        return dip_ok_mask(npz, n, is_long, cfg)
    if switch == "GAP_MORNING_DC_BREAKOUT":
        return dc_breakout_mask(npz, n, is_long, cfg)
    if switch == "GAP_MORNING_SMALL_TOP":
        return small_top_mask(npz, n, is_long, cfg)
    if switch == "GAP_MORNING_VV_DANGER":
        return vv_danger_mask(npz, n, is_long, cfg)
    raise KeyError(f"unknown gapmoc switch: {switch!r}")
