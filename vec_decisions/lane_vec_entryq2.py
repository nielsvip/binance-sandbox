"""lane_vec_entryq2.py — numpy twins for the ENTRY-QUALITY LIVE_ONLY family (22 switches).

Live citations (shared live path ez_positions_quick.py + managers — BOTH venues):
  HA_WICK_QUALITY_ENABLED .... eq:4502 (bc144 gate) cfg 2981 False / tcfg 1864 False
  HA_WICK_QUALITY_SCORE ...... eq:4506/4508 (+15 LONG streak>=3 / SHORT streak<=-3) cfg 2982 15
  HA_WICK_QUALITY_TF ......... eq:4503 (TF for ha_streak read) cfg 2983 '1h'
  MACD_ZERO_CROSS_ENABLED .... eq:4484 (bc131 gate) cfg 2974 False / tcfg 2062 False
  MACD_ZERO_CROSS_SCORE ...... eq:4491/4493 (+15 LONG co&macd<0&px>sma / SHORT mirror) cfg 2975 15
  MACD_ZERO_CROSS_TF ......... eq:4485 (TF for macd/sma reads) cfg 2976 '1h'
  WT_COMPOSITE_ENTRY_GOOD .... eq:2529-2534 (side_comp>=30 +5) cfg 1931 30.0
  WT_COMPOSITE_ENTRY_OK ...... eq:2530-2535 (side_comp>=10 +2) cfg 1932 10.0
  WT_COMPOSITE_ENTRY_STRONG .. eq:2528-2533 (side_comp>=50 +8) cfg 1930 50.0
  MI_ENTRY_ENABLED ........... eq:2819 (MI bonus gate) cfg 1475 False / tcfg 2128 False
  MI_ENTRY_EXHAUST_BONUS ..... eq:2821-2833 (+8 EXHAUST/DIV legs 1h/4h) cfg 1484 8
  MI_ENTRY_STRUCT_BONUS ...... eq:2820-2832 (+10 HL/LH legs 1h/4h) cfg 1483 10
  RULE_B_3M_EXIT_ENABLED ..... eq:14911 (3m LL/LH REDUCE gate) cfg 203 True / tcfg 2598 True
  WRONG_SIDE_WT_TFS_REQUIRED . ez:51820 (WT-against count>=4 over 3m/15m/1h/4h/D) cfg 1454 4
  WT_EXHAUST_EXIT_MIN_GAIN_PCT ez:51312 (gain>=0.5 leg) cfg 3290 0.5
  WT_EXHAUST_EXIT_REQUIRE_GAIN ez:51311 (gain>0 leg, default off) cfg 3289 False
  WT_EXIT_MIN_TFS_TRADIER .... tm:19508 (WT-against count>=5 over cfg TF list) tcfg 4233 5
  WT_EXIT_TFS_TRADIER ........ tm:19507 (TF list, default '5m+15m+1h+4h+D') tcfg 4232
  TREND_EXIT_SCORE_FLIP ...... eq:14564 (LONG score<=flip / SHORT score>=-flip) cfg 2648 0
  TREND_MIN_GAIN_EXIT ........ eq:14563 (gain>=0.10 leg) cfg 2649 0.10
  EXIT_R1_R2_FILTER_TF ....... ez:48693 via r2_eff_tfs (tyf:1066) cfg 5168 '15m'
  MTF_ATR_TRAIL_FILTER_TF .... ez:49214 via mtf_atr_trail_tf (tyf:1021) cfg 5195 '15m'

Design (mirrors lane_vec_scalp_v3.py):
  - ENABLED switches -> armed mask (all-True) or None when off (BIBLE 39).
  - SCORE/bonus-amount switches -> gate-free FIRING-CONDITION mask (live adds a
    fixed bonus where the condition holds; the amount itself has no bar
    predicate — multiply by the cfg bonus at the call site).
  - TF-selector switches -> gate-free INPUT-VALID mask (bars where the columns
    the family reads at the resolved TF are present+finite).
  - Threshold/count switches -> pure sub-condition mask, gate-free (parent ANDs
    with the armed mask). None ONLY when required NPZ keys / position state
    are absent — never fabricated.
NPZ code maps (backtest_v8_precompute.py, NOT invented here):
  - wt_peak_structure: -1=LH +1=HH (:1281-1286, :2021-2022); wt_trough_structure:
    +1=HL -1=LL (:2026-2029); wt_divergence: +1=BULL -1=BEAR (:1289-1293, :1991-1993).
  - wt_momentum_state int8 does NOT encode EXHAUST — EXHAUST_UP/DOWN are derived
    from wt_velocity/wt_acceleration with live's exact predicate
    (ez_indicators.py:1764-1772: vel>0&acc<=0 UP / vel<=0&acc>=0 DOWN).
  - ha_<tf>: +1=green -1=red (STR_MAP :460, encoder :1337-1338).
"""
from __future__ import annotations

import functools
import math
from typing import Any, Mapping

import numpy as np

SUPPORTED = (
    "HA_WICK_QUALITY_ENABLED",
    "HA_WICK_QUALITY_SCORE",
    "HA_WICK_QUALITY_TF",
    "MACD_ZERO_CROSS_ENABLED",
    "MACD_ZERO_CROSS_SCORE",
    "MACD_ZERO_CROSS_TF",
    "WT_COMPOSITE_ENTRY_GOOD",
    "WT_COMPOSITE_ENTRY_OK",
    "WT_COMPOSITE_ENTRY_STRONG",
    "MI_ENTRY_ENABLED",
    "MI_ENTRY_EXHAUST_BONUS",
    "MI_ENTRY_STRUCT_BONUS",
    "RULE_B_3M_EXIT_ENABLED",
    "WRONG_SIDE_WT_TFS_REQUIRED",
    "WT_EXHAUST_EXIT_MIN_GAIN_PCT",
    "WT_EXHAUST_EXIT_REQUIRE_GAIN",
    "WT_EXIT_MIN_TFS_TRADIER",
    "WT_EXIT_TFS_TRADIER",
    "TREND_EXIT_SCORE_FLIP",
    "TREND_MIN_GAIN_EXIT",
    "EXIT_R1_R2_FILTER_TF",
    "MTF_ATR_TRAIL_FILTER_TF",
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


def _parse_tf_list(raw: Any, default: str) -> list:
    try:
        s = str(raw if raw is not None else default)
    except Exception:
        s = default
    if not s.strip():
        s = default
    return [t.strip() for t in s.replace("+", ",").split(",") if t.strip()]


def _r2_eff_tfs(filter_raw: Any, family_raw: Any) -> tuple:
    """Inline mirror of twin_yellow_filters.r2_eff_tfs (:1066-1072)."""
    try:
        fam = family_raw
        if isinstance(fam, str):
            fam = _parse_tf_list(fam, "15m")
        fam = tuple(fam or ("15m",))
    except Exception:
        fam = ("15m",)
    try:
        r = str(filter_raw if filter_raw is not None else "").strip()
    except Exception:
        r = ""
    if r == "" or r.upper() == "OFF":
        return fam
    if r == "15m":
        return fam
    return (r,)


def _resolve_mtf_atr_tf(filter_raw: Any, family_raw: Any) -> str:
    """Inline mirror of resolve_filter_tf (tyf:155-172) + `or family` (ez:49214)."""
    try:
        r = str(filter_raw if filter_raw is not None else "").strip()
    except Exception:
        r = ""
    try:
        fam = str(family_raw if family_raw is not None else "15m").strip() or "15m"
    except Exception:
        fam = "15m"
    if r == "" or r.upper() == "OFF":
        return fam
    if r == "15m":
        if fam == "" or fam.upper() == "OFF":
            return fam
        return fam
    return r


def _exhaust_up_mask(npz: Mapping[str, Any], tf: str, n: int) -> np.ndarray | None:
    """EXHAUST_UP leg (ez_indicators.py:1768): vel>0 & acc<=0. None if keys absent."""
    vel = _col(npz, f"wt_velocity_{tf}", n)
    acc = _col(npz, f"wt_acceleration_{tf}", n)
    if vel is None or acc is None:
        return None
    return np.isfinite(vel) & np.isfinite(acc) & (vel > 0) & (acc <= 0)


def _exhaust_down_mask(npz: Mapping[str, Any], tf: str, n: int) -> np.ndarray | None:
    """EXHAUST_DOWN leg (ez_indicators.py:1771-1772 else): vel<=0 & acc>=0."""
    vel = _col(npz, f"wt_velocity_{tf}", n)
    acc = _col(npz, f"wt_acceleration_{tf}", n)
    if vel is None or acc is None:
        return None
    return np.isfinite(vel) & np.isfinite(acc) & (vel <= 0) & (acc >= 0)


def _htf_trend_score(npz: Mapping[str, Any], n: int) -> np.ndarray | None:
    """Vec mirror of check_htf_trend (ez_manage.py:941-1074). None if any key absent."""
    need = [
        "k_1h", "d_1h", "k_4h", "d_4h", "k_D", "d_D",
        "ha_1h", "ha_4h", "ha_D",
        "dc_basis_1h", "dc_basis_4h", "sma_200_1h",
        "high_1h", "high_1h_prev", "low_1h", "low_1h_prev",
        "high_4h", "high_4h_prev", "low_4h", "low_4h_prev",
        "rsi_1h", "rsi_4h", "mfi_1h", "mfi_4h", "mfi_D", "close",
    ]
    for _tf in ("1h", "4h", "D"):
        need += [f"bar_direction_{_tf}", f"bar_strength_{_tf}", f"bar_vol_confirm_{_tf}", f"bar_swing_bull_{_tf}", f"bar_swing_bear_{_tf}", f"bar_streak_{_tf}", f"bar_vol_spike_{_tf}"]
    c = {}
    for k in need:
        c[k] = _col(npz, k, n)
        if c[k] is None:
            return None
    bull = np.zeros(n, dtype=float)
    bear = np.zeros(n, dtype=float)
    for _tf in ("1h", "4h", "D"):
        bull += np.where(c[f"k_{_tf}"] > c[f"d_{_tf}"], 1.0, 0.0)
        bear += np.where(c[f"k_{_tf}"] > c[f"d_{_tf}"], 0.0, 1.0)
    for _tf in ("1h", "4h", "D"):
        bull += np.where(c[f"ha_{_tf}"] == 1, 1.0, 0.0)
        bear += np.where(c[f"ha_{_tf}"] == -1, 1.0, 0.0)
    for _k in ("dc_basis_1h", "dc_basis_4h", "sma_200_1h"):
        bull += np.where((c[_k] > 0) & (c["close"] > c[_k]), 1.0, 0.0)
        bear += np.where((c[_k] > 0) & ~(c["close"] > c[_k]), 1.0, 0.0)
    for _tf in ("1h", "4h"):
        ok = (c[f"high_{_tf}"] > 0) & (c[f"high_{_tf}_prev"] > 0) & (c[f"low_{_tf}"] > 0) & (c[f"low_{_tf}_prev"] > 0)
        bull += np.where(ok & (c[f"high_{_tf}"] > c[f"high_{_tf}_prev"]) & (c[f"low_{_tf}"] > c[f"low_{_tf}_prev"]), 3.0, 0.0)
        bear += np.where(ok & (c[f"high_{_tf}"] < c[f"high_{_tf}_prev"]) & (c[f"low_{_tf}"] < c[f"low_{_tf}_prev"]), 3.0, 0.0)
    bull += np.where(c["rsi_1h"] > 55, 1.0, 0.0)
    bear += np.where(c["rsi_1h"] < 45, 1.0, 0.0)
    bull += np.where(c["rsi_4h"] > 55, 2.0, 0.0)
    bear += np.where(c["rsi_4h"] < 45, 2.0, 0.0)
    for _k, _w in (("mfi_1h", 1.0), ("mfi_4h", 2.0), ("mfi_D", 2.0)):
        bull += np.where(c[_k] > 60, _w, 0.0)
        bear += np.where(c[_k] < 40, _w, 0.0)
    for _tf, _w in (("1h", 1.0), ("4h", 2.0), ("D", 3.0)):
        d = c[f"bar_direction_{_tf}"]
        st = c[f"bar_strength_{_tf}"]
        vc = c[f"bar_vol_confirm_{_tf}"] != 0
        pts = _w * (1 + vc.astype(float)) * np.where(st >= 0.6, 1.0, 0.5)
        fire = (d != 0) & (st >= 0.3)
        bull += np.where(fire & (d > 0), pts, 0.0)
        bear += np.where(fire & (d < 0), pts, 0.0)
        bull += np.where(c[f"bar_swing_bull_{_tf}"] != 0, _w * 2.0, 0.0)
        bear += np.where(c[f"bar_swing_bear_{_tf}"] != 0, _w * 2.0, 0.0)
        bull += np.where(c[f"bar_streak_{_tf}"] >= 3, _w, 0.0)
        bear += np.where(c[f"bar_streak_{_tf}"] <= -3, _w, 0.0)
        vs = c[f"bar_vol_spike_{_tf}"] != 0
        bull += np.where(vs & (d != 0) & (d > 0), _w, 0.0)
        bear += np.where(vs & (d != 0) & (d < 0), _w, 0.0)
    return bull - bear


# === 1. HA_WICK_QUALITY_ENABLED (eq:4502) — armed gate ===
@_fail_open
def ha_wick_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "HA_WICK_QUALITY_ENABLED", False):
        return None
    return np.ones(n, dtype=bool)


# === 2. HA_WICK_QUALITY_SCORE (eq:4506/4508) — firing condition (gate-free) ===
@_fail_open
def ha_wick_score_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    streak = _col(npz, f"ha_streak_{_s(cfg, 'HA_WICK_QUALITY_TF', '1h')}", n)
    if streak is None:
        return None
    if is_long:
        return np.isfinite(streak) & (streak >= 3)
    return np.isfinite(streak) & (streak <= -3)


# === 3. HA_WICK_QUALITY_TF (eq:4503) — input-valid at resolved TF ===
@_fail_open
def ha_wick_tf_valid(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    streak = _col(npz, f"ha_streak_{_s(cfg, 'HA_WICK_QUALITY_TF', '1h')}", n)
    if streak is None:
        return None
    return np.isfinite(streak)


# === 4. MACD_ZERO_CROSS_ENABLED (eq:4484) — armed gate ===
@_fail_open
def macd_zero_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "MACD_ZERO_CROSS_ENABLED", False):
        return None
    return np.ones(n, dtype=bool)


# === 5. MACD_ZERO_CROSS_SCORE (eq:4491/4493) — firing condition (gate-free) ===
@_fail_open
def macd_zero_score_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    tf = _s(cfg, "MACD_ZERO_CROSS_TF", "1h")
    macd = _col(npz, f"macd_{tf}", n)
    sma = _col(npz, f"sma_200_{tf}", n)
    px = _col(npz, "close", n)
    if macd is None or sma is None or px is None:
        return None
    if is_long:
        co = _col(npz, f"macd_crossover_{tf}", n)
        if co is None:
            return None
        return (co != 0) & np.isfinite(macd) & (macd < 0) & np.isfinite(sma) & (sma > 0) & np.isfinite(px) & (px > sma)
    cu = _col(npz, f"macd_crossunder_{tf}", n)
    if cu is None:
        return None
    return (cu != 0) & np.isfinite(macd) & (macd > 0) & np.isfinite(sma) & (sma > 0) & np.isfinite(px) & (px < sma)


# === 6. MACD_ZERO_CROSS_TF (eq:4485) — input-valid at resolved TF ===
@_fail_open
def macd_zero_tf_valid(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    macd = _col(npz, f"macd_{_s(cfg, 'MACD_ZERO_CROSS_TF', '1h')}", n)
    if macd is None:
        return None
    return np.isfinite(macd)


# === 7/8/9. WT_COMPOSITE_ENTRY tiers (eq:2528-2535) — pure threshold legs ===
@_fail_open
def wt_comp_good_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    comp = _col(npz, "wt_composite_long" if is_long else "wt_composite_short", n)
    if comp is None:
        return None
    return np.isfinite(comp) & (comp >= _f(cfg, "WT_COMPOSITE_ENTRY_GOOD", 30.0))


@_fail_open
def wt_comp_ok_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    comp = _col(npz, "wt_composite_long" if is_long else "wt_composite_short", n)
    if comp is None:
        return None
    return np.isfinite(comp) & (comp >= _f(cfg, "WT_COMPOSITE_ENTRY_OK", 10.0))


@_fail_open
def wt_comp_strong_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    comp = _col(npz, "wt_composite_long" if is_long else "wt_composite_short", n)
    if comp is None:
        return None
    return np.isfinite(comp) & (comp >= _f(cfg, "WT_COMPOSITE_ENTRY_STRONG", 50.0))


# === 10. MI_ENTRY_ENABLED (eq:2819) — armed gate ===
@_fail_open
def mi_entry_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "MI_ENTRY_ENABLED", False):
        return None
    return np.ones(n, dtype=bool)


# === 11. MI_ENTRY_EXHAUST_BONUS (eq:2821-2833) — firing condition (gate-free) ===
@_fail_open
def mi_exhaust_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if is_long:
        legs = [_exhaust_down_mask(npz, "1h", n), _exhaust_down_mask(npz, "4h", n)]
        div = _col(npz, "wt_divergence_1h", n)
        legs.append((np.isfinite(div) & (div == 1)) if div is not None else None)
    else:
        legs = [_exhaust_up_mask(npz, "1h", n), _exhaust_up_mask(npz, "4h", n)]
        div = _col(npz, "wt_divergence_1h", n)
        legs.append((np.isfinite(div) & (div == -1)) if div is not None else None)
    have = [m for m in legs if m is not None]
    if not have:
        return None
    out = have[0]
    for m in have[1:]:
        out = out | m
    return out


# === 12. MI_ENTRY_STRUCT_BONUS (eq:2820-2832) — firing condition (gate-free) ===
@_fail_open
def mi_struct_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if is_long:
        t1 = _col(npz, "wt_trough_structure_1h", n)
        t4 = _col(npz, "wt_trough_structure_4h", n)
        legs = [(np.isfinite(t1) & (t1 == 1)) if t1 is not None else None, (np.isfinite(t4) & (t4 == 1)) if t4 is not None else None]
    else:
        p1 = _col(npz, "wt_peak_structure_1h", n)
        p4 = _col(npz, "wt_peak_structure_4h", n)
        legs = [(np.isfinite(p1) & (p1 == -1)) if p1 is not None else None, (np.isfinite(p4) & (p4 == -1)) if p4 is not None else None]
    have = [m for m in legs if m is not None]
    if not have:
        return None
    return have[0] | have[1] if len(have) == 2 else have[0]


# === 13. RULE_B_3M_EXIT_ENABLED (eq:14911) — armed gate ===
@_fail_open
def rule_b_3m_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "RULE_B_3M_EXIT_ENABLED", True):
        return None
    return np.ones(n, dtype=bool)


# === 14. WRONG_SIDE_WT_TFS_REQUIRED (ez:51802-51823) — WT-against count leg ===
@_fail_open
def wrong_side_wt_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    req = _i(cfg, "WRONG_SIDE_WT_TFS_REQUIRED", 4)
    against = np.zeros(n, dtype=int)
    pairs = 0
    for _tf in ("3m", "15m", "1h", "4h", "D"):
        w1 = _col(npz, f"wt1_{_tf}", n)
        w2 = _col(npz, f"wt2_{_tf}", n)
        if w1 is None or w2 is None:
            continue
        pairs += 1
        ok = np.isfinite(w1) & np.isfinite(w2) & ~((w1 == 0) & (w2 == 0))
        if is_long:
            against += (ok & (w1 < w2)).astype(int)
        else:
            against += (ok & (w1 > w2)).astype(int)
    if pairs == 0:
        return None
    return against >= req


# === 15. WT_EXHAUST_EXIT_MIN_GAIN_PCT (ez:51312) — gain>=min leg (gate-free) ===
@_fail_open
def wt_exhaust_min_gain_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, gain_arr: Any = None) -> np.ndarray | None:
    if gain_arr is None:
        return None
    g = _as_float_arr(gain_arr, n)
    if g is None:
        return None
    return np.isfinite(g) & (g >= _f(cfg, "WT_EXHAUST_EXIT_MIN_GAIN_PCT", 0.5))


# === 16. WT_EXHAUST_EXIT_REQUIRE_GAIN (ez:51311) — gain>0 leg, None when off ===
@_fail_open
def wt_exhaust_require_gain_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, gain_arr: Any = None) -> np.ndarray | None:
    if not _b(cfg, "WT_EXHAUST_EXIT_REQUIRE_GAIN", False):
        return None
    if gain_arr is None:
        return None
    g = _as_float_arr(gain_arr, n)
    if g is None:
        return None
    return np.isfinite(g) & (g > 0)


# === 17. WT_EXIT_MIN_TFS_TRADIER (tm:19507-19526) — WT-against count over cfg TFs ===
@_fail_open
def wt_exit_min_tfs_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    tfs = _parse_tf_list(getattr(cfg, "WT_EXIT_TFS_TRADIER", "5m+15m+1h+4h+D"), "5m+15m+1h+4h+D")
    need = _i(cfg, "WT_EXIT_MIN_TFS_TRADIER", 5)
    against = np.zeros(n, dtype=int)
    pairs = 0
    for _tf in tfs:
        w1 = _col(npz, f"wt1_{_tf}", n)
        w2 = _col(npz, f"wt2_{_tf}", n)
        if w1 is None or w2 is None:
            continue
        pairs += 1
        ok = np.isfinite(w1) & np.isfinite(w2)
        if is_long:
            against += (ok & (w1 < w2)).astype(int)
        else:
            against += (ok & (w1 > w2)).astype(int)
    if pairs == 0 or need <= 0 or not tfs:
        return None
    return against >= need


# === 18. WT_EXIT_TFS_TRADIER (tm:19507) — input-valid on >=1 configured pair ===
@_fail_open
def wt_exit_tfs_valid(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    tfs = _parse_tf_list(getattr(cfg, "WT_EXIT_TFS_TRADIER", "5m+15m+1h+4h+D"), "5m+15m+1h+4h+D")
    out = None
    for _tf in tfs:
        w1 = _col(npz, f"wt1_{_tf}", n)
        w2 = _col(npz, f"wt2_{_tf}", n)
        if w1 is None or w2 is None:
            continue
        leg = np.isfinite(w1) & np.isfinite(w2)
        out = leg if out is None else (out | leg)
    return out


# === 19. TREND_EXIT_SCORE_FLIP (eq:14564-14569) — HTF-score flip leg (gate-free) ===
@_fail_open
def trend_flip_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    score = _htf_trend_score(npz, n)
    if score is None:
        return None
    flip = _f(cfg, "TREND_EXIT_SCORE_FLIP", 0.0)
    if is_long:
        return score <= flip
    return score >= -flip


# === 20. TREND_MIN_GAIN_EXIT (eq:14565) — gain>=min leg (gate-free) ===
@_fail_open
def trend_min_gain_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, gain_arr: Any = None) -> np.ndarray | None:
    if gain_arr is None:
        return None
    g = _as_float_arr(gain_arr, n)
    if g is None:
        return None
    return np.isfinite(g) & (g >= _f(cfg, "TREND_MIN_GAIN_EXIT", 0.10))


# === 21. EXIT_R1_R2_FILTER_TF (ez:48693/48707-48715) — vel/acc valid on eff TFs ===
@_fail_open
def exit_r1_r2_tf_valid(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    eff = _r2_eff_tfs(getattr(cfg, "EXIT_R1_R2_FILTER_TF", "15m"), getattr(cfg, "R2_TF_LIST", ("15m",)))
    out = None
    for _tf in eff:
        v = _col(npz, f"wt_velocity_{_tf}", n)
        a = _col(npz, f"wt_acceleration_{_tf}", n)
        if v is None or a is None:
            continue
        leg = np.isfinite(v) & np.isfinite(a)
        out = leg if out is None else (out | leg)
    return out


# === 22. MTF_ATR_TRAIL_FILTER_TF (ez:49214-49222) — atr>0 at resolved TF ===
@_fail_open
def mtf_atr_trail_tf_valid(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    eff = _resolve_mtf_atr_tf(getattr(cfg, "MTF_ATR_TRAIL_FILTER_TF", "15m"), getattr(cfg, "MTF_ATR_TRAIL_TF", "15m"))
    atr = _col(npz, f"atr_{eff}", n)
    if atr is None:
        return None
    return np.isfinite(atr) & (atr > 0)


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any) -> np.ndarray | None:
    if switch == "HA_WICK_QUALITY_ENABLED":
        return ha_wick_armed(npz, n, is_long, cfg)
    if switch == "HA_WICK_QUALITY_SCORE":
        return ha_wick_score_mask(npz, n, is_long, cfg)
    if switch == "HA_WICK_QUALITY_TF":
        return ha_wick_tf_valid(npz, n, is_long, cfg)
    if switch == "MACD_ZERO_CROSS_ENABLED":
        return macd_zero_armed(npz, n, is_long, cfg)
    if switch == "MACD_ZERO_CROSS_SCORE":
        return macd_zero_score_mask(npz, n, is_long, cfg)
    if switch == "MACD_ZERO_CROSS_TF":
        return macd_zero_tf_valid(npz, n, is_long, cfg)
    if switch == "WT_COMPOSITE_ENTRY_GOOD":
        return wt_comp_good_mask(npz, n, is_long, cfg)
    if switch == "WT_COMPOSITE_ENTRY_OK":
        return wt_comp_ok_mask(npz, n, is_long, cfg)
    if switch == "WT_COMPOSITE_ENTRY_STRONG":
        return wt_comp_strong_mask(npz, n, is_long, cfg)
    if switch == "MI_ENTRY_ENABLED":
        return mi_entry_armed(npz, n, is_long, cfg)
    if switch == "MI_ENTRY_EXHAUST_BONUS":
        return mi_exhaust_mask(npz, n, is_long, cfg)
    if switch == "MI_ENTRY_STRUCT_BONUS":
        return mi_struct_mask(npz, n, is_long, cfg)
    if switch == "RULE_B_3M_EXIT_ENABLED":
        return rule_b_3m_armed(npz, n, is_long, cfg)
    if switch == "WRONG_SIDE_WT_TFS_REQUIRED":
        return wrong_side_wt_mask(npz, n, is_long, cfg)
    if switch == "WT_EXHAUST_EXIT_MIN_GAIN_PCT":
        return wt_exhaust_min_gain_mask(npz, n, is_long, cfg, gain_arr=kw.get("gain_arr"))
    if switch == "WT_EXHAUST_EXIT_REQUIRE_GAIN":
        return wt_exhaust_require_gain_mask(npz, n, is_long, cfg, gain_arr=kw.get("gain_arr"))
    if switch == "WT_EXIT_MIN_TFS_TRADIER":
        return wt_exit_min_tfs_mask(npz, n, is_long, cfg)
    if switch == "WT_EXIT_TFS_TRADIER":
        return wt_exit_tfs_valid(npz, n, is_long, cfg)
    if switch == "TREND_EXIT_SCORE_FLIP":
        return trend_flip_mask(npz, n, is_long, cfg)
    if switch == "TREND_MIN_GAIN_EXIT":
        return trend_min_gain_mask(npz, n, is_long, cfg, gain_arr=kw.get("gain_arr"))
    if switch == "EXIT_R1_R2_FILTER_TF":
        return exit_r1_r2_tf_valid(npz, n, is_long, cfg)
    if switch == "MTF_ATR_TRAIL_FILTER_TF":
        return mtf_atr_trail_tf_valid(npz, n, is_long, cfg)
    raise KeyError(f"unknown entryq2 switch: {switch!r}")
