"""twin_p0_crypto_b.py — P0 LIVE-CONNECT batch B (crypto) scalar+vector twins.

Covers names 54..end of /tmp/p0_vec_only_crypto.txt (52 switches, ALL PROMOTED).
Vector reference: v12_quick_engine.py (exact line citations per twin). Live target:
the ez (crypto) path. Standalone: numpy only; never imports the engines
(they are heavyweight and import sibling vec_decisions.* modules).

Conventions (same as prior twin waves):
  * ``get(key, default)`` accessor — live passes a per-sym getter
    (``lambda k, d: _psym_get(sym, side, k, d)``); tests pass a dict-backed getter.
  * Scalar twins take ``ind`` (indicator dict of floats) + explicit side; vec twins
    take ``(npz, n, is_long, get)`` and return boolean numpy masks.
  * ``True`` = pass/allow/fire per the function name; veto twins return True when
    the entry survives (mirrors vec ``extra_ok`` / ``_base_entry`` AND-semantics).
  * Missing-data rules mirror the vec fails-open/closed behavior exactly and are
    documented per twin (vec array-level gates become key-presence gates in scalar).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Mapping, Sequence

import numpy as np

Get = Callable[[str, Any], Any]

# v12:4066 BASE_TF parity cut — crypto runs 15m, stocks overlay 5m.
_BASE_TF = {"crypto": "15m", "tradier": "5m"}

# v12:10885 — _DEFAULTS_625 anchor used by the K3M override branch (v12:9014).
_DEFAULTS_625_K3M_FLOOR = 30


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def _arr(npz: Mapping[str, Any], key: str, n: int, default: float = 0.0) -> np.ndarray:
    """Mirror of v12 _safe (v12:4007): missing key -> default-filled array."""
    try:
        if npz is not None and key in npz:
            a = np.asarray(npz[key], dtype=float)
            if a.shape == (n,):
                return np.where(np.isfinite(a), a, default)
            out = np.full(n, default, dtype=float)
            m = min(n, a.size)
            out[:m] = np.where(np.isfinite(a.flat[:m]), a.flat[:m], default)
            return out
    except Exception:
        pass
    return np.full(n, default, dtype=float)


def _base_key(field: str, get: Get) -> str:
    """Mirror of v12 _base_safe key selection (v12:4047): crypto 15m / tradier 5m."""
    mode = str(get("MODE", "crypto") or "crypto").lower()
    base = str(get("BASE_TF", "") or "").strip().lower()
    if base in ("3m", "5m", "15m", "1h", "4h", "d"):
        tf = base
    else:
        tf = _BASE_TF.get(mode, "15m")
    return f"{field}_{tf}"


def _present(ind: Mapping[str, Any] | None, key: str) -> bool:
    return bool(ind is not None and key in ind and ind[key] is not None)


_ALIAS_TFS = ("3m", "5m", "15m", "1h", "4h", "D")


def with_aliases(ind: Mapping[str, Any] | None) -> dict:
    """Backfill stoch_k/d <-> k/d spellings (live favors k_15m; vec favors
    stoch_k_15m; check_entry_alignment normalizes but belts-and-braces here).
    Never synthesizes prev-from-current (that would break fail-closed prev)."""
    if not ind:
        return {}
    out = dict(ind)
    for tf in _ALIAS_TFS:
        for long_k, short_k in ((f"stoch_k_{tf}", f"k_{tf}"),
                                (f"stoch_d_{tf}", f"d_{tf}"),
                                (f"stoch_k_{tf}_prev", f"k_{tf}_prev"),
                                (f"rsi_{tf}", f"rsi_{tf}")):
            if out.get(long_k) is None and out.get(short_k) is not None:
                out[long_k] = out[short_k]
            elif out.get(short_k) is None and out.get(long_k) is not None:
                out[short_k] = out[long_k]
    return out


# ─── 1. K3M_FLOOR (v12:9001-9025) — HOOKED ────────────────────────────────
def _k3m_floor_val(get: Get) -> tuple[bool, float, float]:
    """Returns (enabled, floor_enabled_branch, thr_default_branch).

    Enabled branch: ``float(getattr(cfg,'K3M_FLOOR',25.0) or 25.0)`` (v12:9010).
    Default branch: ``float(getattr(cfg,'K3M_FLOOR',0) or 0)`` vs _DEFAULTS_625 30
    (v12:9013-9014); direct ``cfg.K3M_FLOOR`` (QuickConfig 30) at v12:9025.
    """
    enabled = bool(get("K3M_FLOOR_ENABLED", False))
    raw = get("K3M_FLOOR", None)
    floor_en = _f(raw, 25.0) if raw else 25.0
    thr = _f(raw, 0.0) if raw else 0.0
    return enabled, floor_en, thr


def k3m_floor_ok_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    enabled, floor_en, thr = _k3m_floor_val(get)
    if enabled:
        if "stoch_k_15m" in (npz or {}):
            k = _arr(npz, "stoch_k_15m", n, 50)
        elif "stoch_k_1h" in (npz or {}):
            k = _arr(npz, "stoch_k_1h", n, 50)
        else:
            k = _arr(npz, _base_key("stoch_k", get), n, 50)
        return (k < (100 - floor_en)) if is_long else (k > floor_en)
    if abs(thr - float(_DEFAULTS_625_K3M_FLOOR)) > 1e-9:
        if "stoch_k_15m" in (npz or {}):
            k = _arr(npz, "stoch_k_15m", n, 50)
        elif "stoch_k_1h" in (npz or {}):
            k = _arr(npz, "stoch_k_1h", n, 50)
        else:
            k = _arr(npz, _base_key("stoch_k", get), n, 50)
        return (k < (100 - thr)) if is_long else (k > thr)
    k = _arr(npz, _base_key("stoch_k", get), n, 50)
    cfg_floor = _f(get("K3M_FLOOR", 30), 30)
    return (k < (100 - cfg_floor)) if is_long else (k > cfg_floor)


def k3m_floor_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> bool:
    ind = ind or {}
    enabled, floor_en, thr = _k3m_floor_val(get)
    if enabled or abs(thr - float(_DEFAULTS_625_K3M_FLOOR)) > 1e-9:
        floor = floor_en if enabled else thr
        if _present(ind, "stoch_k_15m"):
            k = _f(ind.get("stoch_k_15m"), 50)
        elif _present(ind, "stoch_k_1h"):
            k = _f(ind.get("stoch_k_1h"), 50)
        else:
            k = _f(ind.get(_base_key("stoch_k", get)), 50)
        return (k < (100 - floor)) if is_long else (k > floor)
    k = _f(ind.get(_base_key("stoch_k", get)), 50)
    cfg_floor = _f(get("K3M_FLOOR", 30), 30)
    return (k < (100 - cfg_floor)) if is_long else (k > cfg_floor)


# ─── 2. K_ZONE_VETO_ENABLED_TRADIER (v12:9224-9231) — HOOKED ───────────────
def kzone_veto_ok_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    if not bool(get("K_ZONE_VETO_ENABLED_TRADIER", False)):
        return np.ones(n, dtype=bool)
    k = _arr(npz, "stoch_k_4h", n, 50)
    lo_raw, hi_raw = get("K_ZONE_LONG_THRESHOLD_TRADIER", None), get("K_ZONE_SHORT_THRESHOLD_TRADIER", None)
    lo = _f(lo_raw, 100.0) if lo_raw else 100.0
    hi = _f(hi_raw, 0.0) if hi_raw else 0.0
    return (k < lo) if is_long else (k > hi)


def kzone_veto_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> bool:
    if not bool(get("K_ZONE_VETO_ENABLED_TRADIER", False)):
        return True
    k = _f((ind or {}).get("stoch_k_4h"), 50)
    lo_raw, hi_raw = get("K_ZONE_LONG_THRESHOLD_TRADIER", None), get("K_ZONE_SHORT_THRESHOLD_TRADIER", None)
    lo = _f(lo_raw, 100.0) if lo_raw else 100.0
    hi = _f(hi_raw, 0.0) if hi_raw else 0.0
    return (k < lo) if is_long else (k > hi)


# ─── 3. STOCH_CROSS_ENTRY_TRADIER veto slice (v12:9248-9255) — HOOKED ─────
# (Tradier threshold-select slice v12:8799-8804 lives in tradier_stoch_entry_ok.)
def stoch_cross_ok_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    if not bool(get("STOCH_CROSS_ENTRY_TRADIER", False)):
        return np.ones(n, dtype=bool)
    kk = _base_key("stoch_k", get)
    dd = _base_key("stoch_d", get)
    k = _arr(npz, kk, n, 50)
    d = _arr(npz, dd, n, 50)
    kp = np.roll(k, 1)
    kp[0] = k[0]
    if is_long:
        return (kp <= d) & (k > d)
    return (kp >= d) & (k < d)


def stoch_cross_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None,
                   k_prev: float | None = None) -> bool:
    """Scalar mirror. prev defaults to current (mirrors vec bar-0 ``kp[0]=k[0]``
    which never passes while the gate is on — fail-closed without history)."""
    if not bool(get("STOCH_CROSS_ENTRY_TRADIER", False)):
        return True
    ind = ind or {}
    k = _f(ind.get(_base_key("stoch_k", get)), 50)
    d = _f(ind.get(_base_key("stoch_d", get)), 50)
    if k_prev is None:
        pk = _base_key("stoch_k", get) + "_prev"
        k_prev = _f(ind.get(pk), k) if _present(ind, pk) else k
    if is_long:
        return (k_prev <= d) and (k > d)
    return (k_prev >= d) and (k < d)


# ─── 4. VWAP_FILTER_ENABLED (v12:9126-9129) — HOOKED ──────────────────────
def vwap_filter_ok_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get,
                       close: np.ndarray | None = None) -> np.ndarray:
    if not bool(get("VWAP_FILTER_ENABLED", False)):
        return np.ones(n, dtype=bool)
    vwap = _arr(npz, "vwap_D", n, 0.0)
    if not bool(vwap.sum() > 0):
        return np.ones(n, dtype=bool)
    c = _arr(npz, "close", n, 0.0) if close is None else np.asarray(close, dtype=float)
    return (c > vwap) if is_long else (c < vwap)


def vwap_filter_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None,
                   close: float = 0.0) -> bool:
    if not bool(get("VWAP_FILTER_ENABLED", False)):
        return True
    v = _f((ind or {}).get("vwap_D"), 0.0)
    if not (v > 0):  # scalar analog of vec ``vwap.sum() > 0`` (v12:9128)
        return True
    px = close if close else _f((ind or {}).get("close"), 0.0)
    return (px > v) if is_long else (px < v)


# ─── 5. WT_DC_STOCH_THRESHOLD_LONG/SHORT (v12:9318-9348) — HOOKED ─────────
def _wtdc_stoch_tf(get: Get) -> str:
    tf = str(get("WT_DC_STOCH_TF", "5m") or "5m").lower()
    if tf in ("5m", "3m"):  # v12:9324 15m-floor
        tf = "15m"
    return tf


def wtdc_stoch_ok_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    tf = _wtdc_stoch_tf(get)
    if tf not in ("5m", "15m", "1h", "4h"):
        return np.ones(n, dtype=bool)
    s = _arr(npz, f"stoch_k_{tf}", n, 50)
    if bool(np.all(s == 50)):
        return np.ones(n, dtype=bool)
    if is_long:
        return s < float(get("WT_DC_STOCH_THRESHOLD_LONG", 40.0))
    return s > float(get("WT_DC_STOCH_THRESHOLD_SHORT", 60.0))


def wtdc_stoch_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> bool:
    tf = _wtdc_stoch_tf(get)
    if tf not in ("5m", "15m", "1h", "4h"):
        return True
    key = f"stoch_k_{tf}"
    if not _present(ind, key):  # scalar analog of vec all-50 fails-open (v12:9347)
        return True
    s = _f((ind or {}).get(key), 50)
    if is_long:
        return s < float(get("WT_DC_STOCH_THRESHOLD_LONG", 40.0))
    return s > float(get("WT_DC_STOCH_THRESHOLD_SHORT", 60.0))


# ─── 6. WT_DC_HTF_GATE_MODE (v12:9349-9388) — HOOKED ──────────────────────
def _wtdc_htf_base_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get,
                       w1: dict, w2: dict) -> np.ndarray:
    raw = str(get("WT_DC_HTF_GATE", "none") or "none").lower()
    gate = "4h_d" if (not is_long) else (raw if raw != "none" else "4h_d")
    if gate == "1h":
        against = (w1["1h"] < w2["1h"]) if is_long else (w1["1h"] > w2["1h"])
        return ~against
    if gate == "4h":
        against = (w1["4h"] < w2["4h"]) if is_long else (w1["4h"] > w2["4h"])
        return ~against
    if gate == "4h_d":
        a4 = (w1["4h"] < w2["4h"]) if is_long else (w1["4h"] > w2["4h"])
        aD = (w1["D"] < w2["D"]) if is_long else (w1["D"] > w2["D"])
        return (~a4) & (~aD)
    return np.ones(n, dtype=bool)


def _wtdc_htf_arrays(npz: Mapping[str, Any], n: int) -> tuple[dict, dict]:
    w1, w2 = {}, {}
    for tf, suf in (("1h", "1h"), ("4h", "4h"), ("D", "D")):
        w1[tf] = _arr(npz, f"wt1_{suf}", n, 0)
        w2[tf] = _arr(npz, f"wt2_{suf}", n, 0)
    return w1, w2


def wtdc_htf_ok_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    # NOTE vec quirk (v12:9376-9377): f"wt1_{label.upper()}" builds "wt1_1H"/"wt1_4H"/
    # "wt1_15M" which never match npz keys ("wt1_1h"/...) -> all-zero skip -> ones.
    # Only TF=D binds (via the !='D' guard). Twin reproduces this EXACTLY.
    w1, w2 = _wtdc_htf_arrays(npz, n)
    out = _wtdc_htf_base_vec(npz, n, is_long, get, w1, w2)
    tf_htf = str(get("WT_DC_TF_HTF", "4h") or "4h").lower()
    tf_htf2 = str(get("WT_DC_TF_HTF2", "D") or "D").lower()
    if tf_htf != "4h" or tf_htf2 != "d":
        htf1 = np.ones(n, dtype=bool)
        htf2 = np.ones(n, dtype=bool)
        for label, slot in ((tf_htf, 1), (tf_htf2, 2)):
            if label in ("none", "off", ""):
                continue
            suf = "D" if label.upper() == "D" else label.upper()
            a1 = _arr(npz, f"wt1_{suf}", n, 0)
            a2 = _arr(npz, f"wt2_{suf}", n, 0)
            if bool(a1.sum() == 0 and a2.sum() == 0):
                continue
            ok = (a1 > a2) if is_long else (a1 < a2)
            if slot == 1:
                htf1 = ok
            else:
                htf2 = ok
        mode = str(get("WT_DC_HTF_GATE_MODE", "AND") or "AND").upper()
        exp = (htf1 & htf2) if mode == "AND" else (htf1 | htf2)
        if str(get("MODE", "crypto")) == "tradier" and bool(get("STOCKS_LIVE_ENTRY_STACK_ENABLED", False)):
            exp = (htf1 | htf2) if mode == "AND" else (htf1 & htf2)
        out = out & exp
    return out


def wtdc_htf_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> bool:
    ind = ind or {}
    raw = str(get("WT_DC_HTF_GATE", "none") or "none").lower()
    gate = "4h_d" if (not is_long) else (raw if raw != "none" else "4h_d")

    def _w(tf: str) -> tuple[float, float]:
        suf = tf
        return _f(ind.get(f"wt1_{suf}"), 0.0), _f(ind.get(f"wt2_{suf}"), 0.0)

    a1, a2 = _w("1h")
    b1, b2 = _w("4h")
    d1, d2 = _w("D")
    if gate == "1h":
        out = not ((a1 < a2) if is_long else (a1 > a2))
    elif gate == "4h":
        out = not ((b1 < b2) if is_long else (b1 > b2))
    elif gate == "4h_d":
        a4 = (b1 < b2) if is_long else (b1 > b2)
        aD = (d1 < d2) if is_long else (d1 > d2)
        out = (not a4) and (not aD)
    else:
        out = True
    tf_htf = str(get("WT_DC_TF_HTF", "4h") or "4h").lower()
    tf_htf2 = str(get("WT_DC_TF_HTF2", "D") or "D").lower()
    if tf_htf != "4h" or tf_htf2 != "d":
        vals = []
        for label in (tf_htf, tf_htf2):
            if label in ("none", "off", ""):
                vals.append(True)
                continue
            suf = "D" if label.upper() == "D" else label.upper()
            k1, k2 = f"wt1_{suf}", f"wt2_{suf}"
            if not _present(ind, k1) and not _present(ind, k2):
                vals.append(True)  # scalar analog of vec all-zero skip (v12:9379)
                continue
            x1, x2 = _f(ind.get(k1), 0.0), _f(ind.get(k2), 0.0)
            vals.append((x1 > x2) if is_long else (x1 < x2))
        mode = str(get("WT_DC_HTF_GATE_MODE", "AND") or "AND").upper()
        exp = (vals[0] and vals[1]) if mode == "AND" else (vals[0] or vals[1])
        if str(get("MODE", "crypto")) == "tradier" and bool(get("STOCKS_LIVE_ENTRY_STACK_ENABLED", False)):
            exp = (vals[0] or vals[1]) if mode == "AND" else (vals[0] and vals[1])
        out = out and exp
    return out


# ─── 7. WT_DC hard-short slices (v12:9392-9414) — HOOKED ──────────────────
def _wtdc_k5m_arr(npz: Mapping[str, Any], n: int) -> np.ndarray:
    if "stoch_k_5m" in (npz or {}):
        return _arr(npz, "stoch_k_5m", n, 50)
    return _arr(npz, "stoch_k_15m", n, 50)


def _wtdc_dcpos_arr(npz: Mapping[str, Any], n: int, close: np.ndarray) -> np.ndarray:
    hi = _arr(npz, "dc_high_15m", n)
    lo = _arr(npz, "dc_low_15m", n)
    return np.where((hi > 0) & (lo > 0) & (hi > lo),
                    (close - lo) / np.maximum(hi - lo, 1e-9), 0.5)


def _wtdc_final_arr(npz: Mapping[str, Any], n: int) -> np.ndarray:
    f = _arr(npz, "final_score_norm_lt", n, 0.5)
    if bool(np.all(f == 0.5)):
        f2 = _arr(npz, "trend_val_norm_lt", n, 0.5)
        if not bool(np.all(f2 == 0.5)):
            f = f2
    return f


def wtdc_hard_short_ok_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get,
                           close: np.ndarray | None = None) -> np.ndarray:
    """Full hard-short mask (v12:9393-9418). Long side = pass (v12:9417-9418)."""
    if is_long:
        return np.ones(n, dtype=bool)
    out = np.ones(n, dtype=bool)
    if bool(get("WT_DC_K5M_HARD_ENABLED", False)):
        out &= _wtdc_k5m_arr(npz, n) >= float(get("WT_DC_K5M_MIN_SHORT_HARD", 20.0))
    c = _arr(npz, "close", n, 0.0) if close is None else np.asarray(close, dtype=float)
    out &= _wtdc_dcpos_arr(npz, n, c) >= float(get("WT_DC_DC_POS_MIN", 0.20))
    out &= _wtdc_final_arr(npz, n) < float(get("WT_DC_FINAL_SCORE_MAX", 0.40))
    b1 = np.ones(n, dtype=bool)  # HTF bear>=2 slice needs wt arrays; applied below
    w1h = _arr(npz, "wt1_1h", n, 0)
    w2h = _arr(npz, "wt2_1h", n, 0)
    w14 = _arr(npz, "wt1_4h", n, 0)
    w24 = _arr(npz, "wt2_4h", n, 0)
    w1d = _arr(npz, "wt1_D", n, 0)
    w2d = _arr(npz, "wt2_D", n, 0)
    bear = (w1h < w2h).astype(int) + (w14 < w24).astype(int) + (w1d < w2d).astype(int)
    out &= bear >= int(float(get("DC_BREAK_LOW_HTF_ALIGN_MIN", 2)))
    return out & b1


def wtdc_k5m_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> bool:
    if is_long or not bool(get("WT_DC_K5M_HARD_ENABLED", False)):
        return True
    ind = ind or {}
    if _present(ind, "stoch_k_5m"):
        k = _f(ind.get("stoch_k_5m"), 50)
    else:
        k = _f(ind.get("stoch_k_15m"), 50)
    return k >= float(get("WT_DC_K5M_MIN_SHORT_HARD", 20.0))


def wtdc_dcpos_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None,
                  close: float = 0.0) -> bool:
    if is_long:
        return True
    ind = ind or {}
    hi = _f(ind.get("dc_high_15m"), 0.0)
    lo = _f(ind.get("dc_low_15m"), 0.0)
    px = close if close else _f(ind.get("close"), 0.0)
    pos = (px - lo) / max(hi - lo, 1e-9) if (hi > 0 and lo > 0 and hi > lo) else 0.5
    return pos >= float(get("WT_DC_DC_POS_MIN", 0.20))


def wtdc_final_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> bool:
    if is_long:
        return True
    ind = ind or {}
    if _present(ind, "final_score_norm_lt"):
        f = _f(ind.get("final_score_norm_lt"), 0.5)
    elif _present(ind, "trend_val_norm_lt"):
        f = _f(ind.get("trend_val_norm_lt"), 0.5)
    else:
        f = 0.5
    return f < float(get("WT_DC_FINAL_SCORE_MAX", 0.40))


# ─── 8. WT_DC_ENABLED B_WT_DC_LIVE proposal (v12:9424-9449) — HOOKED ──────
# Simple multi-TF scorer = exact scalar port of
# wt_dc_entry_scorer_vec.score_entry_multitf_vec (:233-284) + cross reader (:38-76).
def wtdc_cross_1h(ind: Mapping[str, Any] | None) -> int:
    """Scalar mirror of _vec_cross_direction 1h (scorer_vec:38-76)."""
    ind = ind or {}
    if _present(ind, "wt_cross_1h"):
        v = ind.get("wt_cross_1h")
        try:
            num = float(v)
            if math.isfinite(num):
                if num > 0:
                    return 1
                if num < 0:
                    return -1
                # numeric 0 falls through to bull/bear flags (vec: unresolved)
            else:
                raise ValueError
        except (TypeError, ValueError):
            t = str(v).strip().upper()
            if t == "BULL":
                return 1
            if t == "BEAR":
                return -1
            # unresolved text falls through to bull/bear flags
    if _f(ind.get("wt_cross_bull_1h"), 0.0) != 0:
        return 1
    if _f(ind.get("wt_cross_bear_1h"), 0.0) != 0:
        return -1
    return 0


def wtdc_multitf_score(is_long: bool, ind: Mapping[str, Any] | None) -> float:
    """Exact scalar port of score_entry_multitf_vec long/short arms (:272-284).

    Valid gate (:263-270): all six inputs finite, else 0.0. Missing keys count
    as invalid (vec fills NaN via _vec_float_field default for absent keys).
    """
    ind = ind or {}
    need = ("wt1_D", "wt2_D", "wt1_4h", "wt2_4h", "dc_position_1h", "stoch_k_5m")
    vals: dict[str, float] = {}
    for k in need:
        if not _present(ind, k):
            return 0.0
        try:
            v = float(ind[k])
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(v):
            return 0.0
        vals[k] = v
    cross = wtdc_cross_1h(ind)
    s = 0.0
    if is_long:
        s += 25.0 * (vals["wt1_D"] > vals["wt2_D"])
        s += 25.0 * (vals["wt1_4h"] > vals["wt2_4h"])
        s += 30.0 * (cross == 1)
        s += 10.0 * (vals["dc_position_1h"] < 0.5)
        s += 10.0 * (vals["stoch_k_5m"] < 40.0)
    else:
        s += 25.0 * (vals["wt1_D"] < vals["wt2_D"])
        s += 25.0 * (vals["wt1_4h"] < vals["wt2_4h"])
        s += 30.0 * (cross == -1)
        s += 10.0 * (vals["dc_position_1h"] > 0.5)
        s += 10.0 * (vals["stoch_k_5m"] > 60.0)
    return s


def wtdc_entry_threshold(get: Get) -> tuple[float, bool]:
    """Threshold + TF adjustment (v12:9430-9443). Returns (thr, detailed)."""
    detailed = bool(get("WT_DC_DETAILED_SCORER_ENABLED", False))
    if detailed:
        thr = float(get("WT_DC_DETAILED_ENTRY_THRESHOLD", 43))
    else:
        thr = float(get("WT_DC_ENTRY_THRESHOLD", 45))
    tf = str(get("WT_DC_TF_ENTRY", "1h") or "1h").lower()
    if tf == "15m":
        thr = max(20, thr - 10)
    elif tf == "4h":
        thr = min(85, thr + 10)
    elif tf == "d":
        thr = min(85, thr + 15)
    return float(thr), detailed


def wtdc_live_entry_fire(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Scalar B_WT_DC_LIVE (v12:9424-9449). Tradier final-condition (v12:9450-9489)
    is tradier-only and encoded as a MODE gate (crypto skips it).

    Detailed-scorer path delegates to wt_dc_entry_scorer_vec with an n=1 wrap;
    any failure fails CLOSED (mirrors vec try/except at v12:9490-9491 which adds
    no block on exception).
    """
    if not bool(get("WT_DC_ENABLED", False)):
        return False, ""
    thr, detailed = wtdc_entry_threshold(get)
    try:
        if detailed:
            import wt_dc_entry_scorer_vec as _wv
            wrap = {k: np.array([_f((ind or {}).get(k), 0.0)]) for k in (
                "wt1_D", "wt2_D", "wt1_4h", "wt2_4h", "wt1_1h", "wt2_1h",
                "dc_position_1h", "stoch_k_5m", "wt_cross_1h", "wt_velocity_1h",
                "wt_velocity_4h", "close", "dc_position_15m")}
            score = float(np.asarray(_wv.score_entry_detailed_vec(wrap, is_long, n=1))[0])
        else:
            score = wtdc_multitf_score(is_long, ind)
    except Exception:
        return False, "WT_DC_ERR"
    if score >= thr:
        return True, f"WT_DC_LIVE({score:.0f}>={thr:.0f})"
    return False, ""


# ─── 9. Daytrade entry B_DAYTRADE (v12:8673-8679) — HOOKED ────────────────
# Knobs: TRADIER_DC_DAYTRADE_ENABLED + TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION
# (TRADIER_DC_POSITION_ENTRY_THRESHOLD selects the tradier branch only).
def daytrade_entry_fire_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    if not (bool(get("DC_DAYTRADE_ENABLED", False)) or bool(get("TRADIER_DC_DAYTRADE_ENABLED", False))):
        return np.zeros(n, dtype=bool)
    tradier = str(get("MODE", "crypto")) == "tradier"
    thr = float(get("TRADIER_DC_POSITION_ENTRY_THRESHOLD", 0.15)) if tradier else float(get("DC_POSITION_ENTRY_THRESHOLD", 0.15))
    dc_pos = _arr(npz, "dc_position_15m", n)
    if bool(get("TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION", True)):
        w = _arr(npz, "dc_width_1h", n)
        wp = np.roll(w, 1)
        wp[0] = w[0]
        exp_ok = w > wp
    else:
        exp_ok = np.ones(n, dtype=bool)
    base = (dc_pos < thr) if is_long else (dc_pos > (1 - thr))
    return base & exp_ok


def daytrade_entry_fire(get: Get, is_long: bool, ind: Mapping[str, Any] | None,
                        dc_width_prev: float | None = None) -> tuple[bool, str]:
    if not (bool(get("DC_DAYTRADE_ENABLED", False)) or bool(get("TRADIER_DC_DAYTRADE_ENABLED", False))):
        return False, ""
    ind = ind or {}
    tradier = str(get("MODE", "crypto")) == "tradier"
    thr = float(get("TRADIER_DC_POSITION_ENTRY_THRESHOLD", 0.15)) if tradier else float(get("DC_POSITION_ENTRY_THRESHOLD", 0.15))
    dc_pos = _f(ind.get("dc_position_15m"), 0.0)
    if bool(get("TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION", True)):
        w = _f(ind.get("dc_width_1h"), 0.0)
        wp = _f(ind.get("dc_width_1h_prev"), w) if _present(ind, "dc_width_1h_prev") else (dc_width_prev if dc_width_prev is not None else w)
        if not (w > wp):  # mirrors vec bar-0 (w[0] > w[0] == False)
            return False, ""
    if is_long and dc_pos < thr:
        return True, f"DAYTRADE(dcpos15m={dc_pos:.3f}<{thr})"
    if (not is_long) and dc_pos > (1 - thr):
        return True, f"DAYTRADE(dcpos15m={dc_pos:.3f}>{1 - thr:.3f})"
    return False, ""


# ─── 10. SATOSHIT exit (v12:10001-10015) — HOOKED ─────────────────────────
# Knobs: SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER / LONG_STOCH_K_MIN / SHORT_RSI_MAX /
# SHORT_STOCH_K_MAX. Base k = 15m on crypto (v12:10003 via _base_safe).
def satoshit_exit_fire_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    if not bool(get("SATOSHIT_EXIT_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    rsi = _arr(npz, "rsi_1h", n, 50)
    k = _arr(npz, _base_key("stoch_k", get), n, 50)
    if is_long:
        rsi_hit = rsi >= float(get("SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER", 55.0))
        stoch_hit = k >= float(get("SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER", 60.0))
    else:
        rsi_hit = rsi <= float(get("SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER", 42.0))
        stoch_hit = k <= float(get("SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER", 50.0))
    need = get("SATOSHIT_MIN_VOTES_TRADIER", 3)
    if need >= 3:
        return rsi_hit & stoch_hit
    return rsi_hit | stoch_hit


def satoshit_exit_fire(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not bool(get("SATOSHIT_EXIT_ENABLED", False)):
        return False, ""
    ind = ind or {}
    rsi = _f(ind.get("rsi_1h"), 50)
    k = _f(ind.get(_base_key("stoch_k", get)), 50)
    if is_long:
        rsi_hit = rsi >= float(get("SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER", 55.0))
        stoch_hit = k >= float(get("SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER", 60.0))
    else:
        rsi_hit = rsi <= float(get("SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER", 42.0))
        stoch_hit = k <= float(get("SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER", 50.0))
    need = get("SATOSHIT_MIN_VOTES_TRADIER", 3)
    fire = (rsi_hit and stoch_hit) if need >= 3 else (rsi_hit or stoch_hit)
    if fire:
        return True, f"SATOSHIT_VEC_EXIT(rsi={rsi:.0f}_k={k:.0f})"
    return False, ""


# ═══════════ NEEDS-OPERATOR-DECISION twins (twin delivered, no hook) ══════

# ─── 11. Tradier-gated entry twins ────────────────────────────────────────
def kzone_entry_fire_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    """B_KZONE (v12:8428-8432). TRADIER_K_ZONE_* thresholds bind tradier only;
    crypto uses K_ZONE_LONG/SHORT_THRESHOLD."""
    if not bool(get("K_ZONE_ENTRY_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    tradier = str(get("MODE", "crypto")) == "tradier"
    lo = float(get("TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER", 35)) if tradier else float(get("K_ZONE_LONG_THRESHOLD", 35))
    hi = float(get("TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER", 65)) if tradier else float(get("K_ZONE_SHORT_THRESHOLD", 65))
    k = _arr(npz, _base_key("stoch_k", get), n, 50)
    return (k < lo) if is_long else (k > hi)


def kzone_entry_fire(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not bool(get("K_ZONE_ENTRY_ENABLED", False)):
        return False, ""
    tradier = str(get("MODE", "crypto")) == "tradier"
    lo = float(get("TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER", 35)) if tradier else float(get("K_ZONE_LONG_THRESHOLD", 35))
    hi = float(get("TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER", 65)) if tradier else float(get("K_ZONE_SHORT_THRESHOLD", 65))
    k = _f((ind or {}).get(_base_key("stoch_k", get)), 50)
    if is_long and k < lo:
        return True, f"KZONE(k={k:.0f}<{lo:.0f})"
    if (not is_long) and k > hi:
        return True, f"KZONE(k={k:.0f}>{hi:.0f})"
    return False, ""


def tradier_rsi_entry_fire_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    """B_TRADIERRSI (v12:8791-8798). Tradier-only (MODE gate v12:8791)."""
    if str(get("MODE", "crypto")) != "tradier":
        return np.zeros(n, dtype=bool)
    rsi = _arr(npz, "rsi_1h", n, 50)
    if is_long:
        thr = float(get("TRADIER_RSI_ENTRY_LONG_TRADIER", -1.0))
        if thr >= 0:
            return rsi < thr
    else:
        thr = float(get("TRADIER_RSI_ENTRY_SHORT_TRADIER", 70.0))
        if thr <= 100:
            rvol = _arr(npz, "rel_vol_1h", n, 0.0)
            return (rsi > thr) & (rvol >= float(get("TRADIER_RSI_SHORT_REL_VOLUME_MIN", 1.2)))
    return np.zeros(n, dtype=bool)


def tradier_rsi_entry_fire(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> tuple[bool, str]:
    if str(get("MODE", "crypto")) != "tradier":
        return False, ""
    ind = ind or {}
    rsi = _f(ind.get("rsi_1h"), 50)
    if is_long:
        thr = float(get("TRADIER_RSI_ENTRY_LONG_TRADIER", -1.0))
        if thr >= 0 and rsi < thr:
            return True, f"TRSI(rsi={rsi:.0f}<{thr:.0f})"
    else:
        thr = float(get("TRADIER_RSI_ENTRY_SHORT_TRADIER", 70.0))
        if thr <= 100 and rsi > thr and _f(ind.get("rel_vol_1h"), 0.0) >= float(get("TRADIER_RSI_SHORT_REL_VOLUME_MIN", 1.2)):
            return True, f"TRSI(rsi={rsi:.0f}>{thr:.0f})"
    return False, ""


def tradier_stoch_entry_fire_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    """B_TRADIERSTOCH (v12:8799-8804). Tradier-only; STOCH_CROSS off-select.
    NOTE code-default/codec divergence: getattr short default 70 (v12:8801) vs
    QuickConfig TRADIER_STOCH_ENTRY_SHORT_TRADIER=52 (v12:4498). Code wins when
    the config lacks the key; twin mirrors the getattr chain exactly."""
    if str(get("MODE", "crypto")) != "tradier":
        return np.zeros(n, dtype=bool)
    if bool(get("STOCH_CROSS_ENTRY_TRADIER", False)):
        return np.zeros(n, dtype=bool)
    k = _arr(npz, _base_key("stoch_k", get), n, 50)
    if is_long:
        return (k < float(get("TRADIER_STOCH_ENTRY_LONG_TRADIER", 30))) & (k > float(get("TRADIER_STOCH_EXTREME_LONG_TRADIER", 15)))
    return (k > float(get("TRADIER_STOCH_ENTRY_SHORT_TRADIER", 70))) & (k < float(get("TRADIER_STOCH_EXTREME_SHORT_TRADIER", 85)))


def tradier_stoch_entry_fire(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> tuple[bool, str]:
    if str(get("MODE", "crypto")) != "tradier":
        return False, ""
    if bool(get("STOCH_CROSS_ENTRY_TRADIER", False)):
        return False, ""
    k = _f((ind or {}).get(_base_key("stoch_k", get)), 50)
    if is_long:
        lo, hi = float(get("TRADIER_STOCH_ENTRY_LONG_TRADIER", 30)), float(get("TRADIER_STOCH_EXTREME_LONG_TRADIER", 15))
        if k < lo and k > hi:
            return True, f"TSTOCH(k={k:.0f})"
    else:
        lo, hi = float(get("TRADIER_STOCH_ENTRY_SHORT_TRADIER", 70)), float(get("TRADIER_STOCH_EXTREME_SHORT_TRADIER", 85))
        if k > lo and k < hi:
            return True, f"TSTOCH(k={k:.0f})"
    return False, ""


def tf_alignment_ok_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    """TF alignment gate (v12:9257-9266). Tradier-only + BACKTEST_VALIDATED gate."""
    if not bool(get("BACKTEST_VALIDATED_GATES_TRADIER", False)) or str(get("MODE", "crypto")) != "tradier":
        return np.ones(n, dtype=bool)
    w1h, w2h = _arr(npz, "wt1_1h", n), _arr(npz, "wt2_1h", n)
    w14, w24 = _arr(npz, "wt1_4h", n), _arr(npz, "wt2_4h", n)
    w1d, w2d = _arr(npz, "wt1_D", n), _arr(npz, "wt2_D", n)
    if is_long:
        cnt = (w1h > w2h).astype(int) + (w14 > w24).astype(int) + (w1d > w2d).astype(int)
    else:
        cnt = (w1h < w2h).astype(int) + (w14 < w24).astype(int) + (w1d < w2d).astype(int)
    total = get("TF_ALIGNMENT_MIN_TOTAL", 0)
    need = max(1, min(3, int(total // 4))) if total > 0 else 1
    return cnt >= need


def tf_alignment_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> bool:
    if not bool(get("BACKTEST_VALIDATED_GATES_TRADIER", False)) or str(get("MODE", "crypto")) != "tradier":
        return True
    ind = ind or {}
    c = 0
    for tf in ("1h", "4h", "D"):
        a, b = _f(ind.get(f"wt1_{tf}"), 0.0), _f(ind.get(f"wt2_{tf}"), 0.0)
        c += 1 if ((a > b) if is_long else (a < b)) else 0
    total = get("TF_ALIGNMENT_MIN_TOTAL", 0)
    need = max(1, min(3, int(total // 4))) if total > 0 else 1
    return c >= need


def tradier_entry_score_ok_vec(npz: Mapping[str, Any], n: int, is_long: bool, get: Get) -> np.ndarray:
    """MFI_D filter (v12:9284-9288). Tradier-only, threshold>=24 arms."""
    emin = get("TRADIER_ENTRY_SCORE_THRESHOLD", 0)
    if not (emin >= 24 and str(get("MODE", "crypto")) == "tradier"):
        return np.ones(n, dtype=bool)
    mfi = _arr(npz, "mfi_D", n, 50)
    return (mfi >= 40) if is_long else (mfi <= 60)


def tradier_entry_score_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> bool:
    emin = get("TRADIER_ENTRY_SCORE_THRESHOLD", 0)
    if not (emin >= 24 and str(get("MODE", "crypto")) == "tradier"):
        return True
    mfi = _f((ind or {}).get("mfi_D"), 50)
    return (mfi >= 40) if is_long else (mfi <= 60)


# ─── 12. STRENGTH filter (v12:9144-9162) — twin only, no live block plumbing
STRENGTH_WEIGHTS = {"B15": 4, "B04": 3, "B11": 3, "B02": 2, "B10": 1, "B12": 1,
                    "B14": 1, "B_PULL1": 1, "B_PULL2": 1, "B_PULL3": 1, "B_PULL4": 1,
                    "B09": 1, "B_REENTRY2": 1, "B_BBSQUEEZE2": 1, "B_SQUEEZE": 1}


def strength_score(blocks: Mapping[str, Any]) -> float:
    s = 0.0
    for name, fired in (blocks or {}).items():
        if fired:
            s += STRENGTH_WEIGHTS.get(name, 1)
    return s


def strength_ok_vec(blocks: Mapping[str, np.ndarray], n: int, get: Get) -> np.ndarray:
    if not bool(get("STRENGTH_FILTER_ENABLED", True)):
        return np.ones(n, dtype=bool)
    score = np.zeros(n, dtype=np.float32)
    for name, arr in (blocks or {}).items():
        score = score + np.asarray(arr, dtype=np.float32) * STRENGTH_WEIGHTS.get(name, 1)
    return score >= float(get("STRENGTH_MIN_SCORE", 5.0))


def strength_ok(get: Get, blocks: Mapping[str, Any] | None) -> bool:
    if not bool(get("STRENGTH_FILTER_ENABLED", True)):
        return True
    return strength_score(blocks) >= float(get("STRENGTH_MIN_SCORE", 5.0))


# ─── 13. TF_FOCUS (v12:8755-8766) — DEAD in vector (focus_mult never applied)
def tf_focus_note() -> str:
    return ("DEAD: v12:8760-8766 computes focus_mult but no read site exists "
            "(grep focus_mult = defs only); gate+weight cannot move the ledger.")


def tf_focus_ok_vec(n: int) -> np.ndarray:
    return np.ones(n, dtype=bool)


def tf_focus_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None) -> bool:
    return True


# ─── 14. MIN_HOLD (v12:12507,12518-12519 + held>=min_hold loop gates) ─────
def min_hold_bars(get: Get) -> int:
    m = max(int(get("MIN_HOLD_BARS", 3) or 0), int(get("MIN_HOLD_BARS_BEFORE_EXIT", 0) or 0))
    if str(get("MODE", "crypto")) == "tradier" and float(get("MIN_HOLD_MINUTES_TRADIER", 0.0) or 0.0) > 0:
        tf = str(get("BASE_TF", "15m") or "15m")
        try:
            bmin = int("".join(c for c in tf if c.isdigit()) or 3)
        except Exception:
            bmin = 3
        m = max(m, int(round(float(get("MIN_HOLD_MINUTES_TRADIER", 0.0)) / max(bmin, 1))))
    return m


def min_hold_ok(get: Get, held_bars: int) -> bool:
    return held_bars >= min_hold_bars(get)


# ─── 15. REENTRY filter (v12:12469-12470 + 13179-13180) ───────────────────
def reentry_filter_need(get: Get, n_masks: int) -> int:
    if n_masks <= 0:
        return 0
    return min(max(1, int(get("REENTRY_FILTER_MIN_PASS", 1) or 1)), n_masks)


def reentry_fire_allowed(get: Get, passes: Sequence[bool], has_closed_before: bool,
                         entry_sig: bool, fire: bool = True) -> bool:
    """Mirrors v12:13179-13180: reentries (not fresh entries) must pass >= need
    of the active entry-filter masks when REENTRY_ENTRY_FILTER_ENABLED."""
    masks = list(passes or [])
    on = bool(get("REENTRY_ENTRY_FILTER_ENABLED", False)) and bool(masks)
    if fire and on and has_closed_before and not entry_sig:
        if sum(1 for m in masks if bool(m)) < reentry_filter_need(get, len(masks)):
            return False
    return fire


# ─── 16. SIMPLE_PRICE_GT0 (v12:12509-12513) — test switch, never auto-hook
def simple_gt0_vec(n: int, close: np.ndarray) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Returns (entry_sig, exit_sig, cooldown_bars, min_hold) per v12:12509-12513."""
    c = np.asarray(close, dtype=float)
    return (c > 0), ((c > 0) & (np.arange(n) % 2 == 0)), 0, 1


def simple_gt0(get: Get, close_px: float, bar_index: int) -> tuple[bool, bool]:
    if not bool(get("SIMPLE_PRICE_GT0_ENABLED", False)):
        return False, False
    e = close_px > 0
    return e, bool(e and (bar_index % 2 == 0))


# ─── 17. REENTRY_TIER1_SIZE_MULT_TRADIER (v12:13268) — tradier-only ───────
def reentry_tier1_mult(get: Get, has_closed_before: bool) -> float:
    if has_closed_before and str(get("MODE", "crypto")) == "tradier":
        return float(get("REENTRY_TIER1_SIZE_MULT_TRADIER", 1.0))
    return 1.0


# ─── 18. MTF_ATR_TRAIL_TF_TRADIER (v12:12655) — tradier slice only ────────
def mtf_atr_trail_tf(get: Get) -> str:
    if str(get("MODE", "crypto")) == "tradier":
        return str(get("MTF_ATR_TRAIL_TF_TRADIER", "1h") or "OFF").strip()
    return str(get("MTF_ATR_TRAIL_TF", "15m") or "OFF").strip()


# ─── 19. Dead daytrade knobs — assigned but never consumed ────────────────
def daytrade_pct_note() -> str:
    return ("DEAD: TRADIER_DC_DAYTRADE_STOP_PCT/TARGET_PCT assigned v12:12527-12528 "
            "but the fixed-% branches were DELETED (v12:13886-13887 USER SPEC); "
            "only DC-channel/ATR exits remain. Twin is inert by construction.")


def daytrade_pct_resolve(get: Get) -> tuple[float, float]:
    """The assigned (unused) values, for testability: pct*100 like vec."""
    tradier = str(get("MODE", "crypto")) == "tradier"
    if tradier:
        return (float(get("TRADIER_DC_DAYTRADE_STOP_PCT", 0.005)) * 100,
                float(get("TRADIER_DC_DAYTRADE_TARGET_PCT", 0.005)) * 100)
    return (float(get("DC_DAYTRADE_STOP_PCT", 0.015)) * 100,
            float(get("DC_DAYTRADE_TARGET_PCT", 0.01)) * 100)


def daytrade_pct_fires() -> bool:
    return False


def daytrade_use_dc_note() -> str:
    return ("DEAD: TRADIER_DC_DAYTRADE_STOP/TARGET_USE_DC[_4]_15M have field defs "
            "only (v12:4724-4727); zero functional reads repo-wide outside "
            "tradier_manage stocks-live (33345-33348). Twin is inert by construction.")


def daytrade_use_dc_flags(get: Get) -> dict[str, bool]:
    return {
        "STOP_USE_DC_15M": bool(get("TRADIER_DC_DAYTRADE_STOP_USE_DC_15M", False)),
        "STOP_USE_DC4_15M": bool(get("TRADIER_DC_DAYTRADE_STOP_USE_DC4_15M", False)),
        "TARGET_USE_DC_15M": bool(get("TRADIER_DC_DAYTRADE_TARGET_USE_DC_15M", False)),
        "TARGET_USE_DC4_15M": bool(get("TRADIER_DC_DAYTRADE_TARGET_USE_DC4_15M", False)),
    }


# ─── 20. PARTIAL_PROFIT_LOCK_*_TRADIER — stocks-live reference twin ───────
# No functional vector predicate exists in this checkout: v12 delegates to
# vec_decisions.reduce_profit_lock.ppl_step (v12:13758) whose module is absent
# here. Twin mirrors the stocks-live reference tradier_manage:19973-20027.
def ppl_params(get: Get) -> tuple[float, float, float, float]:
    """(min_gain, arm_gain, be_buffer, frac) — tradier_manage:19973-19976."""
    return (float(get("PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER", 0.5)),
            float(get("PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER", 0.75)),
            float(get("PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER", 0.02)),
            float(get("PARTIAL_PROFIT_LOCK_FRAC_TRADIER", 0.5)))


def ppl_tp_fire(get: Get, gain_pct: float, qty: float, min_qty: float,
                entry_px: float, fired: bool) -> tuple[bool, float, str]:
    """TP reduce (tradier_manage:19992-20017). Returns (fire, reduce_qty, why).

    Whole-share floor + keep>=max(1,min_qty) skip (19996-20003) included."""
    if fired:
        return False, 0.0, ""
    min_gain, _arm, _buf, frac = ppl_params(get)
    if not (gain_pct >= min_gain and qty > min_qty and entry_px > 0):
        return False, 0.0, ""
    reduce_qty = float(int(qty * frac))
    keep = qty - reduce_qty
    if reduce_qty >= qty or keep < max(1.0, min_qty):
        return False, 0.0, "PPL_SKIP_WOULD_FULL_CLOSE"
    if reduce_qty >= min_qty and keep >= min_qty:
        return True, reduce_qty, f"PPL_TP_gain{gain_pct:.2f}"
    return False, 0.0, ""


def ppl_be_stop(get: Get, is_long: bool, entry_px: float) -> float:
    """BE stop w/ buffer (tradier_manage:20005)."""
    _min_gain, _arm, buf, _frac = ppl_params(get)
    return entry_px * (1.0 + buf / 100.0) if is_long else entry_px * (1.0 - buf / 100.0)


def ppl_arm_upgrade(get: Get, gain_pct: float, fired: bool, upgraded: bool,
                    first_exit_px: float) -> bool:
    """Stop upgrade BE->first_exit (tradier_manage:20018)."""
    _min_gain, arm, _buf, _frac = ppl_params(get)
    return bool(fired and not upgraded and gain_pct >= arm and first_exit_px > 0)


def ppl_sl_hit(get: Get, is_long: bool, px: float, stop_level: float,
               fired: bool, qty: float, min_qty: float) -> bool:
    """Remainder stop hit (tradier_manage:20021-20023)."""
    if not (fired and stop_level > 0 and qty > min_qty):
        return False
    return (px <= stop_level) if is_long else (px >= stop_level)


# ─── 21. LR_BAND_LADDER_STOCH_EXTREME — stocks-live reference twin ────────
# No functional vector read (v12 defaults only: 4606/6118). Twin mirrors the
# per-TF stoch slice of tradier_manage:1543-1569 (limit default 30.0).
def lr_ladder_stoch_ok(get: Get, is_long: bool, ind: Mapping[str, Any] | None,
                       tf: str = "1h") -> bool:
    ind = ind or {}
    limit = float(get("LR_BAND_LADDER_STOCH_EXTREME", 30.0))
    if _present(ind, f"_ladder_stoch_k_{tf}"):
        stoch = _f(ind.get(f"_ladder_stoch_k_{tf}"), 50.0)
    else:
        stoch = _f(ind.get(f"stoch_k_{tf}"), 50.0)
    if _present(ind, f"_ladder_stoch_k_{tf}_prev"):
        prev = _f(ind.get(f"_ladder_stoch_k_{tf}_prev"), stoch)
    elif _present(ind, f"stoch_k_{tf}_prev"):
        prev = _f(ind.get(f"stoch_k_{tf}_prev"), stoch)
    else:
        prev = stoch
    if is_long:
        return (stoch <= limit) and (stoch > prev)
    return (stoch >= 100.0 - limit) and (stoch < prev)


# ─── 22. WT_D_BOUNCE_DD_STOP_ENABLED — stocks-live reference twin ─────────
# No functional vector read in this checkout (v12 delegates to the absent
# reduce_profit_lock.dd_bounce_stop_fires, v12:13810). Twin mirrors
# tradier_manage:12091.
def wt_d_bounce_dd_stop_fire(get: Get, is_long: bool, px: float, aug_px: float,
                             aug_qty: float, market_open: bool = True) -> bool:
    return bool(aug_qty >= 0.5 and aug_px > 0 and market_open
                and bool(get("WT_D_BOUNCE_DD_STOP_ENABLED", True))
                and ((px < aug_px) if is_long else (px > aug_px)))


# ─── 23. WIN_TRAIL_EROSION_PCT (v12:12616 + 13890) ────────────────────────
def win_trail_erosion_fire_vec(peak: np.ndarray, live: np.ndarray, get: Get,
                               confirm: np.ndarray | None = None) -> np.ndarray:
    erosion = float(get("WIN_TRAIL_EROSION_PCT", 0.0))
    if not erosion > 0:
        return np.zeros_like(np.asarray(peak, dtype=float), dtype=bool)
    p, lv = np.asarray(peak, dtype=float), np.asarray(live, dtype=float)
    fire = (p > 0) & ((p - lv) >= p * erosion)
    if confirm is not None:
        fire = fire & np.asarray(confirm, dtype=bool)
    return fire


def win_trail_erosion_fire(get: Get, peak_pnl_pct: float, live_pnl_pct: float,
                           confirm: bool | None = None) -> bool:
    erosion = float(get("WIN_TRAIL_EROSION_PCT", 0.0))
    if not erosion > 0:
        return False
    if not (peak_pnl_pct > 0 and (peak_pnl_pct - live_pnl_pct) >= peak_pnl_pct * erosion):
        return False
    return True if confirm is None else bool(confirm)
