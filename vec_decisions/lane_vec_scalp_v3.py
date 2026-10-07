"""lane_vec_scalp_v3.py — numpy twins for the SCALP_V3 LIVE_ONLY family (10 switches).

Live citations (ez_positions_quick.py — SHARED live path for BOTH venues):
  SCALP_V3_PROTECTIVE_EXIT_ENABLED ... :18576 (master gate, _scalp_v3_protective_exits)
  SCALP_V3_OB_WALL_TOO_CLOSE_PCT ..... :18622/:18635/:18646 (OB wall + gain>0 exit)
  SCALP_V3_K_OB_EXIT_ENABLED ......... :18650 (K-extreme+wall sub-gate)
  SCALP_V3_K_OB_EXIT_K3M_HI .......... :18651/:18658 (LONG k_3m >= HI)
  SCALP_V3_K_OB_EXIT_K3M_LO .......... :18652/:18663 (SHORT k_3m <= LO)
  SCALP_V3_K_OB_EXIT_K15M_HI ......... :18653/:18658 (LONG k_15m >= HI)
  SCALP_V3_K_OB_EXIT_K15M_LO ......... :18654/:18663 (SHORT k_15m <= LO)
  SCALP_V3_K_OB_EXIT_WALL_PCT ........ :18655/:18659/:18664 (wall proximity leg)
  SCALP_V3_AUG_BE_STOP_ENABLED ....... :18770 (BE-stop gate, _scalp_v3_attempt_be_stops)
  SCALP_V3_AUG_BE_STOP_PCT ........... :18772/:18789 (gain <= pct on augmented winner)

Design (mirrors twin_vec_special.py):
  - ENABLED switches -> armed mask (all-True) or None when off (BIBLE 39).
  - Threshold switches -> pure sub-condition mask, gate-free (parent ANDs with
    the armed mask). None ONLY when the required NPZ key / position state is
    absent — never fabricated.
  - Side-inapplicable thresholds (e.g. K3M_HI on SHORT) -> None, because live
    never reads them on that side; flipping them must not move that side.
Defaults mirror live config.py / config_tradier.py exactly (both True/0.5/20/80/0.1).

NPZ reality (frozen backtest NPZ, 802 keys): k_15m PRESENT; k_3m, k_1m,
wt1_3m/wt2_3m, high/low_1m/3m, ob_*_wall_pct ABSENT (redis-only live data).
Predicates needing absent keys return None on real NPZ and a real mask when
the caller supplies the key (probe proves both).
"""
from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np

SUPPORTED = (
    "SCALP_V3_PROTECTIVE_EXIT_ENABLED",
    "SCALP_V3_OB_WALL_TOO_CLOSE_PCT",
    "SCALP_V3_K_OB_EXIT_WALL_PCT",
    "SCALP_V3_K_OB_EXIT_K3M_LO",
    "SCALP_V3_K_OB_EXIT_K3M_HI",
    "SCALP_V3_K_OB_EXIT_K15M_LO",
    "SCALP_V3_K_OB_EXIT_K15M_HI",
    "SCALP_V3_K_OB_EXIT_ENABLED",
    "SCALP_V3_AUG_BE_STOP_PCT",
    "SCALP_V3_AUG_BE_STOP_ENABLED",
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
        a = np.asarray(x, dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return np.isfinite(a[:n])


def _as_float_arr(x: Any, n: int) -> np.ndarray | None:
    try:
        a = np.asarray(x, dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return a[:n]


# === 1. SCALP_V3_PROTECTIVE_EXIT_ENABLED (live :18576) — master armed gate ===
def scalp_v3_protective_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "SCALP_V3_PROTECTIVE_EXIT_ENABLED", True):
        return None
    return np.ones(n, dtype=bool)


# === 2. SCALP_V3_OB_WALL_TOO_CLOSE_PCT (live :18622/:18635/:18646) ===
# LONG: ob_ask_wall_pct < pct AND gain > 0 ; SHORT: ob_bid_wall_pct < pct AND gain > 0.
def scalp_v3_ob_wall_close_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, gain_arr: Any = None) -> np.ndarray | None:
    pct = _f(cfg, "SCALP_V3_OB_WALL_TOO_CLOSE_PCT", 0.5)
    wall = _col(npz, "ob_ask_wall_pct" if is_long else "ob_bid_wall_pct", n)
    if wall is None:
        return None
    mask = np.isfinite(wall) & (wall < pct)
    if gain_arr is not None:
        g = _as_float_arr(gain_arr, n)
        if g is None:
            return None
        mask = mask & (g > 0)
    return mask


# === 3. SCALP_V3_K_OB_EXIT_WALL_PCT (live :18655/:18659/:18664) — wall leg ===
def scalp_v3_k_ob_wall_close_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    pct = _f(cfg, "SCALP_V3_K_OB_EXIT_WALL_PCT", 0.5)
    wall = _col(npz, "ob_ask_wall_pct" if is_long else "ob_bid_wall_pct", n)
    if wall is None:
        return None
    return np.isfinite(wall) & (wall < pct)


# === 4/5. K3M thresholds (live :18651-18652/:18658/:18663) ===
def scalp_v3_k_ob_k3m_hi_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not is_long:
        return None
    k = _col(npz, "k_3m", n)
    if k is None:
        return None
    return np.isfinite(k) & (k >= _f(cfg, "SCALP_V3_K_OB_EXIT_K3M_HI", 80.0))


def scalp_v3_k_ob_k3m_lo_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if is_long:
        return None
    k = _col(npz, "k_3m", n)
    if k is None:
        return None
    return np.isfinite(k) & (k <= _f(cfg, "SCALP_V3_K_OB_EXIT_K3M_LO", 20.0))


# === 6/7. K15M thresholds (live :18653-18654/:18658/:18663) ===
def scalp_v3_k_ob_k15m_hi_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not is_long:
        return None
    k = _col(npz, "k_15m", n)
    if k is None:
        return None
    return np.isfinite(k) & (k >= _f(cfg, "SCALP_V3_K_OB_EXIT_K15M_HI", 80.0))


def scalp_v3_k_ob_k15m_lo_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if is_long:
        return None
    k = _col(npz, "k_15m", n)
    if k is None:
        return None
    return np.isfinite(k) & (k <= _f(cfg, "SCALP_V3_K_OB_EXIT_K15M_LO", 20.0))


# === 8. SCALP_V3_K_OB_EXIT_ENABLED (live :18650) — sub-gate armed ===
def scalp_v3_k_ob_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "SCALP_V3_K_OB_EXIT_ENABLED", True):
        return None
    return np.ones(n, dtype=bool)


# === composite: full live K-OB exit (lines 18650-18666, inside master gate) ===
def scalp_v3_k_ob_exit_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "SCALP_V3_PROTECTIVE_EXIT_ENABLED", True):
        return None
    if not _b(cfg, "SCALP_V3_K_OB_EXIT_ENABLED", True):
        return None
    wall_m = scalp_v3_k_ob_wall_close_mask(npz, n, is_long, cfg)
    if wall_m is None:
        return None
    if is_long:
        hi3 = scalp_v3_k_ob_k3m_hi_mask(npz, n, is_long, cfg)
        hi15 = scalp_v3_k_ob_k15m_hi_mask(npz, n, is_long, cfg)
    else:
        hi3 = scalp_v3_k_ob_k3m_lo_mask(npz, n, is_long, cfg)
        hi15 = scalp_v3_k_ob_k15m_lo_mask(npz, n, is_long, cfg)
    legs = [m for m in (hi3, hi15) if m is not None]
    if not legs:
        return None
    extreme = legs[0]
    for m in legs[1:]:
        extreme = extreme | m
    return extreme & wall_m


# === 9. SCALP_V3_AUG_BE_STOP_PCT (live :18772/:18787-18790) ===
# Fires on augmented V3 winners: augmented AND max_gain >= 1.0 AND gain <= pct.
# gain/max_gain/augmented are position state, not NPZ — caller must pass them.
def scalp_v3_aug_be_stop_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, gain_arr: Any = None, max_gain_arr: Any = None, augmented_arr: Any = None) -> np.ndarray | None:
    if gain_arr is None or max_gain_arr is None or augmented_arr is None:
        return None
    g = _as_float_arr(gain_arr, n)
    mg = _as_float_arr(max_gain_arr, n)
    if g is None or mg is None:
        return None
    try:
        aug = np.asarray(augmented_arr, dtype=bool).reshape(-1)
    except Exception:
        return None
    if aug.size < n:
        return None
    aug = aug[:n]
    pct = _f(cfg, "SCALP_V3_AUG_BE_STOP_PCT", 0.1)
    return aug & np.isfinite(mg) & (mg >= 1.0) & np.isfinite(g) & (g <= pct)


# === 10. SCALP_V3_AUG_BE_STOP_ENABLED (live :18770) — BE-stop armed ===
def scalp_v3_aug_be_stop_armed(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray | None:
    if not _b(cfg, "SCALP_V3_AUG_BE_STOP_ENABLED", True):
        return None
    return np.ones(n, dtype=bool)


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any) -> np.ndarray | None:
    if switch == "SCALP_V3_PROTECTIVE_EXIT_ENABLED":
        return scalp_v3_protective_armed(npz, n, is_long, cfg)
    if switch == "SCALP_V3_OB_WALL_TOO_CLOSE_PCT":
        return scalp_v3_ob_wall_close_mask(npz, n, is_long, cfg, gain_arr=kw.get("gain_arr"))
    if switch == "SCALP_V3_K_OB_EXIT_WALL_PCT":
        return scalp_v3_k_ob_wall_close_mask(npz, n, is_long, cfg)
    if switch == "SCALP_V3_K_OB_EXIT_K3M_LO":
        return scalp_v3_k_ob_k3m_lo_mask(npz, n, is_long, cfg)
    if switch == "SCALP_V3_K_OB_EXIT_K3M_HI":
        return scalp_v3_k_ob_k3m_hi_mask(npz, n, is_long, cfg)
    if switch == "SCALP_V3_K_OB_EXIT_K15M_LO":
        return scalp_v3_k_ob_k15m_lo_mask(npz, n, is_long, cfg)
    if switch == "SCALP_V3_K_OB_EXIT_K15M_HI":
        return scalp_v3_k_ob_k15m_hi_mask(npz, n, is_long, cfg)
    if switch == "SCALP_V3_K_OB_EXIT_ENABLED":
        return scalp_v3_k_ob_armed(npz, n, is_long, cfg)
    if switch == "SCALP_V3_AUG_BE_STOP_PCT":
        return scalp_v3_aug_be_stop_mask(npz, n, is_long, cfg, gain_arr=kw.get("gain_arr"), max_gain_arr=kw.get("max_gain_arr"), augmented_arr=kw.get("augmented_arr"))
    if switch == "SCALP_V3_AUG_BE_STOP_ENABLED":
        return scalp_v3_aug_be_stop_armed(npz, n, is_long, cfg)
    if switch == "SCALP_V3_K_OB_EXIT":
        return scalp_v3_k_ob_exit_mask(npz, n, is_long, cfg)
    raise KeyError(f"unknown scalp-v3 switch: {switch!r}")
