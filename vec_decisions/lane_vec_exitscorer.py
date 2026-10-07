"""lane_vec_exitscorer.py — numpy twin for the WT/DC EXIT_SCORER family (5 switches).

Live rule: wt_dc_exit_scorer.score_exit (indicators, side, px, cfg) N-of-5 gate.
Callers: tradier_manage.py:20333/:21325/:23350 (exit when score >= WT_DC_EXIT_THRESHOLD=20).
  EXIT_SCORER_MIN_CONDITIONS ... :67 (default 5; hits>=min -> FULL tier)
  EXIT_SCORER_K_EXTREME ......... :68 (default 75; c4 K extreme 1h/4h)
  EXIT_SCORER_DC_EXTREME ........ :69 (default 0.80; c5 DC-pos extreme 1h/4h)
  EXIT_SCORER_PARTIAL_SCORE ..... :70 (default 40; hits>=max(min-1,3) tier)
  EXIT_SCORER_FULL_SCORE ........ :71 (default 100; hits>=min tier)
Conditions (live :93-108): c1 1h WT cross against; c2 4h WT against; c3 D WT
against; c4 K extreme; c5 DC-pos extreme. SHORT mirrors (100-k, 1-dc).
Live _get defaults (neutral = condition False) are mirrored when NPZ keys are
absent — never fabricated, never None-collapsed: c4/c5 default False exactly
as live does with missing indicators.
"""
from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np

SUPPORTED = (
    "EXIT_SCORER_MIN_CONDITIONS",
    "EXIT_SCORER_K_EXTREME",
    "EXIT_SCORER_DC_EXTREME",
    "EXIT_SCORER_PARTIAL_SCORE",
    "EXIT_SCORER_FULL_SCORE",
)


def _f(cfg: Any, name: str, default: float) -> float:
    try:
        v = float(getattr(cfg, name, default))
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _i(cfg: Any, name: str, default: int) -> int:
    try:
        return int(getattr(cfg, name, default))
    except (TypeError, ValueError):
        return default


def _col(npz: Mapping[str, Any], key: str, n: int) -> np.ndarray | None:
    try:
        if key not in npz:
            return None
        a = np.asarray(npz[key], dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return a[:n]


def _c1_ltf_cross(npz: Mapping[str, Any], n: int, is_long: bool) -> np.ndarray:
    for key in ("wt_cross_1h", "wt_signal_1h"):
        v = _col(npz, key, n)
        if v is not None:
            ok = np.isfinite(v) & (v != 0)
            return ok & ((v < 0) if is_long else (v > 0))
    w1 = _col(npz, "wt1_1h", n)
    w2 = _col(npz, "wt2_1h", n)
    if w1 is None or w2 is None:
        return np.zeros(n, dtype=bool)
    ok = np.isfinite(w1) & np.isfinite(w2) & ~((w1 == 0) & (w2 == 0))
    return ok & ((w1 < w2) if is_long else (w1 > w2))


def _against(npz: Mapping[str, Any], tf: str, n: int, is_long: bool) -> np.ndarray:
    w1 = _col(npz, f"wt1_{tf}", n)
    w2 = _col(npz, f"wt2_{tf}", n)
    if w1 is None or w2 is None:
        return np.zeros(n, dtype=bool)
    ok = np.isfinite(w1) & np.isfinite(w2)
    return ok & ((w1 < w2) if is_long else (w1 > w2))


def _c4_k_extreme(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray:
    kx = _f(cfg, "EXIT_SCORER_K_EXTREME", 75.0)
    out = np.zeros(n, dtype=bool)
    for key in ("stoch_k_1h", "stoch_k_4h"):
        k = _col(npz, key, n)
        if k is None:
            continue
        leg = (k >= kx) if is_long else (k <= (100.0 - kx))
        out = out | (np.isfinite(k) & leg)
    return out


def _c5_dc_extreme(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray:
    dx = _f(cfg, "EXIT_SCORER_DC_EXTREME", 0.80)
    out = np.zeros(n, dtype=bool)
    for key in ("dc_position_1h", "dc_position_4h"):
        d = _col(npz, key, n)
        if d is None:
            continue
        leg = (d >= dx) if is_long else (d <= (1.0 - dx))
        out = out | (np.isfinite(d) & leg)
    return out


def _hits(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray:
    h = _c1_ltf_cross(npz, n, is_long).astype(int)
    h = h + _against(npz, "4h", n, is_long).astype(int)
    h = h + _against(npz, "D", n, is_long).astype(int)
    h = h + _c4_k_extreme(npz, n, is_long, cfg).astype(int)
    h = h + _c5_dc_extreme(npz, n, is_long, cfg).astype(int)
    return h


# === EXIT_SCORER_MIN_CONDITIONS — full-fire tier (hits >= min) ===
def exitscorer_full_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        return _hits(npz, n, is_long, cfg) >= _i(cfg, "EXIT_SCORER_MIN_CONDITIONS", 5)
    except Exception:
        return None


# === EXIT_SCORER_K_EXTREME — c4 leg ===
def exitscorer_k_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        return _c4_k_extreme(npz, n, is_long, cfg)
    except Exception:
        return None


# === EXIT_SCORER_DC_EXTREME — c5 leg ===
def exitscorer_dc_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        return _c5_dc_extreme(npz, n, is_long, cfg)
    except Exception:
        return None


# === EXIT_SCORER_PARTIAL_SCORE — partial tier (hits >= max(min-1,3)) ===
def exitscorer_partial_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        _p = _f(cfg, "EXIT_SCORER_PARTIAL_SCORE", 40.0)
        req = max(_i(cfg, "EXIT_SCORER_MIN_CONDITIONS", 5) - 1, 3) if _p >= 0 else 99
        return _hits(npz, n, is_long, cfg) >= req
    except Exception:
        return None


# === EXIT_SCORER_FULL_SCORE — full tier (hits >= min) ===
def exitscorer_fullscore_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    try:
        _v = _f(cfg, "EXIT_SCORER_FULL_SCORE", 100.0)
        return _hits(npz, n, is_long, cfg) >= _i(cfg, "EXIT_SCORER_MIN_CONDITIONS", 5) if _v >= 0 else np.zeros(n, dtype=bool)
    except Exception:
        return None


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any) -> np.ndarray | None:
    if switch == "EXIT_SCORER_MIN_CONDITIONS":
        return exitscorer_full_mask(npz, n, is_long, cfg)
    if switch == "EXIT_SCORER_K_EXTREME":
        return exitscorer_k_mask(npz, n, is_long, cfg)
    if switch == "EXIT_SCORER_DC_EXTREME":
        return exitscorer_dc_mask(npz, n, is_long, cfg)
    if switch == "EXIT_SCORER_PARTIAL_SCORE":
        return exitscorer_partial_mask(npz, n, is_long, cfg)
    if switch == "EXIT_SCORER_FULL_SCORE":
        return exitscorer_fullscore_mask(npz, n, is_long, cfg)
    raise KeyError(f"unknown exitscorer switch: {switch!r}")
