"""LH/LL structure-triggered top/bottom exit (EXPERIMENTAL, default OFF).

Idea: after the previous COMPLETED 4h (and/or D) bar prints a lower high and/or
lower low, a long is exited near the top of the bounce forming inside the NEXT 4h
bar instead of riding it down into the non-negotiable dc_low_4h - 0.25% stop.
Short side mirrors (higher high / higher low -> exit near the forming bottom
instead of the dc_high_4h + 0.25% stop).

The DC stop itself is NOT touched and stays the worst-case backstop: when no
bounce forms (straight continuation) the stop fires exactly as before. This exit
only wins the race when the lower-high bounce actually prints.

Causality: all HTF inputs are last-COMPLETED-bar values. In the vector engine
that is the post-htf_causal_align store (high_4h[i] = completed bar, high_4h_prev
= the one before it); live must pass completed-bar klines/snapshot values, never
the forming bar. The exit trigger (WT 15m rollover / 1h channel touch) is
evaluated on the current 15m bar only.

Knobs (all default inert; live behaviour unchanged until promoted):
  LH_LL_TOP_EXIT_ENABLED: bool = False            master switch
  LH_LL_TOP_EXIT_STRUCT_TF: str = "OFF"           "4h", "D" or "4h,D" (OR)
  LH_LL_TOP_EXIT_STRUCT_MODE: str = "LH_LL"       "LH" | "LL" | "LH_LL" | "LH_AND_LL"
                                                  (short mirrors to HH/HL/HH_HL/HH_AND_HL)
  LH_LL_TOP_EXIT_MODE: str = "EITHER"             "WT15M" | "DC1H" | "EITHER" | "BOTH"
  LH_LL_TOP_EXIT_DC1H_BUFFER_PCT: float = 0.10    touch band at the 1h channel edge
  LH_LL_TOP_EXIT_BOTH_TOL_PCT: float = 0.30       BOTH: WT cross counts only near edge
  LH_LL_TOP_EXIT_REQUIRE_PRICE_CONFIRM: bool = False  WT leg also needs px<pxp/px>pxp

Pure functions: no state, no I/O. Shared by v12_quick_engine (scalar in-loop via
check_lh_ll_top_exit, vector via build_exit_mask) and the live managers
(ez_manage/tradier_manage process_position via check_lh_ll_top_exit).
"""
from __future__ import annotations
import math
from typing import Any, Callable, Dict, List, Tuple
try:
    import numpy as np
except ImportedError:
    np = None

_STRUCT_TFS = ("4h", "D")
_STRUCT_MODES = ("LH", "LL", "LH_LL", "LH_AND_LL")
_EXIT_MODES = ("WT15M", "DC1H", "EITHER", "BOTH")

def parse_struct_tf(raw: Any) -> List[str]:
    if raw is None:
        return []
    s = str(raw).strip()
    if not s or s.upper() == "OFF":
        return []
    out: List[str] = []
    for p in s.replace("+", ",").replace("|", ",").replace(" ", ",").split(","):
        p = p.strip()
        if p in _STRUCT_TFS and p not in out:
            out.append(p)
    return out

def _f(v: Any, d: float) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d

def resolve_lh_ll_top_exit(get: Callable[[str, Any], Any]) -> Dict[str, Any]:
    enabled = bool(get("LH_LL_TOP_EXIT_ENABLED", False))
    tfs = parse_struct_tf(get("LH_LL_TOP_EXIT_STRUCT_TF", "OFF"))
    smode = str(get("LH_LL_TOP_EXIT_STRUCT_MODE", "LH_LL") or "LH_LL").strip().upper()
    if smode not in _STRUCT_MODES:
        smode = "LH_LL"
    mode = str(get("LH_LL_TOP_EXIT_MODE", "EITHER") or "EITHER").strip().upper()
    if mode not in _EXIT_MODES:
        mode = "EITHER"
    return {"enabled": enabled and bool(tfs), "tfs": tfs, "struct_mode": smode, "mode": mode, "buf": _f(get("LH_LL_TOP_EXIT_DC1H_BUFFER_PCT", 0.10), 0.10) / 100.0, "tol": _f(get("LH_LL_TOP_EXIT_BOTH_TOL_PCT", 0.30), 0.30) / 100.0, "price_confirm": bool(get("LH_LL_TOP_EXIT_REQUIRE_PRICE_CONFIRM", False))}

def struct_armed(hi: float, hip: float, lo: float, lop: float, is_long: bool, struct_mode: str) -> bool:
    if not (hi > 0 and hip > 0 and lo > 0 and lop > 0):
        return False
    if is_long:
        first = hi < hip
        second = lo < lop
    else:
        first = hi > hip
        second = lo > lop
    if struct_mode == "LH":
        return first
    if struct_mode == "LL":
        return second
    if struct_mode == "LH_AND_LL":
        return first and second
    return first or second

def wt_leg_fires(w1: float, w2: float, w1p: float, w2p: float, px: float, pxp: float, is_long: bool, price_confirm: bool) -> bool:
    if w1 == 0 or w2 == 0 or w1p == 0 or w2p == 0 or px <= 0:
        return False
    if is_long:
        fire = w1 < w2 and w1p >= w2p
        return fire and (px < pxp) if price_confirm else fire
    fire = w1 > w2 and w1p <= w2p
    return fire and (px > pxp) if price_confirm else fire

def dc_leg_fires(px: float, edge: float, is_long: bool, buf_frac: float) -> bool:
    if not (px > 0 and edge > 0):
        return False
    return px >= edge * (1 - buf_frac) if is_long else px <= edge * (1 + buf_frac)

def near_edge(px: float, edge: float, tol_frac: float) -> bool:
    if not (px > 0 and edge > 0):
        return False
    return abs(px - edge) / edge <= max(tol_frac, 0.0)

def _arm_reason(is_long: bool, struct_mode: str, hi: float, hip: float, lo: float, lop: float) -> str:
    if is_long:
        bits = ("LH" if hi < hip else "", "LL" if lo < lop else "")
    else:
        bits = ("HH" if hi > hip else "", "HL" if lo > lop else "")
    return "+".join(b for b in bits if b) or struct_mode

def check_lh_ll_top_exit(spec: Dict[str, Any], ind: Dict[str, Any], px: float, pxp: float, w1p: float, w2p: float, is_long: bool) -> Tuple[bool, str]:
    if not spec.get("enabled"):
        return False, ""
    px = _f(px, 0.0)
    if px <= 0:
        return False, ""
    g = lambda k: _f((ind or {}).get(k), 0.0)
    smode = str(spec.get("struct_mode", "LH_LL"))
    armed_tf = ""
    armed_how = ""
    for tf in spec.get("tfs", []):
        hi, hip, lo, lop = g(f"high_{tf}"), g(f"high_{tf}_prev"), g(f"low_{tf}"), g(f"low_{tf}_prev")
        if struct_armed(hi, hip, lo, lop, is_long, smode):
            armed_tf = tf
            armed_how = _arm_reason(is_long, smode, hi, hip, lo, lop)
            break
    if not armed_tf:
        return False, ""
    mode = str(spec.get("mode", "EITHER"))
    w1, w2 = g("wt1_15m"), g("wt2_15m")
    wt = wt_leg_fires(w1, w2, _f(w1p, 0.0), _f(w2p, 0.0), px, _f(pxp, 0.0), is_long, bool(spec.get("price_confirm")))
    edge = g("dc_high_1h") if is_long else g("dc_low_1h")
    dc = dc_leg_fires(px, edge, is_long, float(spec.get("buf", 0.001)))
    if mode == "WT15M":
        fire, leg = wt, "WT15M"
    elif mode == "DC1H":
        fire, leg = dc, "DC1H"
    elif mode == "BOTH":
        fire, leg = bool(wt and near_edge(px, edge, float(spec.get("tol", 0.003)))), "BOTH"
    else:
        fire, leg = bool(wt or dc), ("WT15M" if wt else ("DC1H" if dc else "EITHER"))
    if not fire:
        return False, ""
    return True, f"LH_LL_TOP_EXIT arm={armed_tf}_{armed_how} leg={leg}"

def build_exit_mask(npz: dict, n: int, is_long: bool, spec: Dict[str, Any], safe: Callable[[dict, str, int, float], Any]) -> Any:
    assert np is not None, "numpy required"
    off = np.zeros(n, dtype=bool)
    if not spec.get("enabled"):
        return off
    smode = str(spec.get("struct_mode", "LH_LL"))
    armed = np.zeros(n, dtype=bool)
    for tf in spec.get("tfs", []):
        hi = np.asarray(safe(npz, f"high_{tf}", n, 0.0), dtype=float)
        hip = np.asarray(safe(npz, f"high_{tf}_prev", n, 0.0), dtype=float)
        lo = np.asarray(safe(npz, f"low_{tf}", n, 0.0), dtype=float)
        lop = np.asarray(safe(npz, f"low_{tf}_prev", n, 0.0), dtype=float)
        valid = (hi > 0) & (hip > 0) & (lo > 0) & (lop > 0)
        if is_long:
            first, second = hi < hip, lo < lop
        else:
            first, second = hi > hip, lo > lop
        if smode == "LH":
            m = first
        elif smode == "LL":
            m = second
        elif smode == "LH_AND_LL":
            m = first & second
        else:
            m = first | second
        armed |= valid & m
    if not bool(armed.any()):
        return off
    px = np.asarray(safe(npz, "close", n, 0.0), dtype=float)
    if not bool((px > 0).any()):
        px = np.asarray(safe(npz, "close_15m", n, 0.0), dtype=float)
    w1 = np.asarray(safe(npz, "wt1_15m", n, 0.0), dtype=float)
    w2 = np.asarray(safe(npz, "wt2_15m", n, 0.0), dtype=float)
    w1p = np.concatenate(([0.0], w1[:-1])) if n else w1
    w2p = np.concatenate(([0.0], w2[:-1])) if n else w2
    pxp = np.concatenate(([0.0], px[:-1])) if n else px
    wok = (w1 != 0) & (w2 != 0) & (w1p != 0) & (w2p != 0) & (px > 0)
    if is_long:
        wt = wok & (w1 < w2) & (w1p >= w2p)
        if spec.get("price_confirm"):
            wt &= px < pxp
        edge = np.asarray(safe(npz, "dc_high_1h", n, 0.0), dtype=float)
        dc = (px > 0) & (edge > 0) & (px >= edge * (1 - float(spec.get("buf", 0.001))))
    else:
        wt = wok & (w1 > w2) & (w1p <= w2p)
        if spec.get("price_confirm"):
            wt &= px > pxp
        edge = np.asarray(safe(npz, "dc_low_1h", n, 0.0), dtype=float)
        dc = (px > 0) & (edge > 0) & (px <= edge * (1 + float(spec.get("buf", 0.001))))
    mode = str(spec.get("mode", "EITHER"))
    if mode == "WT15M":
        trig = wt
    elif mode == "DC1H":
        trig = dc
    elif mode == "BOTH":
        tol = max(float(spec.get("tol", 0.003)), 0.0)
        trig = wt & (px > 0) & (edge > 0) & (np.abs(px - edge) / np.where(edge > 0, edge, 1.0) <= tol)
    else:
        trig = wt | dc
    return armed & trig
