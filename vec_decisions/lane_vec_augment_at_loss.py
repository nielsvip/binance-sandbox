"""lane_vec_augment_at_loss.py — numpy twin for AUGMENT_AT_LOSS_ENABLED (LANE L2).

User-approved design (2026-10-05): test augment-at-loss ONLY under the
higher-low / lower-high restriction — NEVER to catch a falling knife. Add to
a losing position ONLY when a higher low (LONG) / lower high (SHORT) is
confirmed on CLOSED bars.

Live citations:
  crypto live: ez_manage.py evaluate_augmentation LANE-L2 block (AUGMENT_AT_LOSS
    gate before the gain gate; reads _psym_get(symbol, side,
    "AUGMENT_AT_LOSS_ENABLED", False); cap = position.augmented_count <
    MAX_AUGMENTS_PER_POSITION; ONE START_POSITION_SIZE step per HL stair-step).
  stocks live: tradier_manage.py evaluate_augment LANE-L2 block (reads
    _cfg_auto("AUGMENT_AT_LOSS_ENABLED", False); cap = state aug_count <
    MAX_AUGMENTS_PER_POSITION + MAX_SYMBOL_VALUE_TRADIER headroom; ONE
    START_POSITION_SIZE step, 1-share min).

Confirmation definition (IDENTICAL live and vec, closed bars only):
  LONG higher-low:  low_15m_prev[i] > low_15m_prev[i-1] (strictly rising
    consecutive closed-15m-bar lows) AND dc_low_15m_prev[i] >=
    dc_low_15m_prev[i-1] (Donchian floor not falling — structural confirm).
  SHORT lower-high: high_15m_prev[i] < high_15m_prev[i-1] (strictly falling
    consecutive closed-15m-bar highs) AND dc_high_15m_prev[i] <=
    dc_high_15m_prev[i-1] (Donchian ceiling not rising).
  Live tracks the "i-1" value in trade_manager state (previous evaluate call's
  closed-bar value); vec shifts the column by one bar. Either way ONLY *_prev
  (confirmed/prev-bar) columns are read — no current/forming-bar column, no
  intra-bar peeking. Bar 0 (no prior) is always False.
  Anti-falling-knife: predicate ALSO requires gain <= 0 strictly (position at
  a loss or exactly breakeven; never adds green via this path).

Design (mirrors lane_vec_scalp_v3.py):
  - OFF (default False both configs) -> None (BIBLE 39: no effect).
  - gain_arr is position state, not NPZ — caller must pass it; None without.
  - Absent low_15m_prev/high_15m_prev key -> None (never fabricated).
  - Absent dc_*_15m_prev key -> structural leg neutral (pass); primary
    low/high leg still required. Both venues emit the dc prev keys live and
    the frozen NPZ carries them, so the leg enforces in practice.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

SUPPORTED = ("AUGMENT_AT_LOSS_ENABLED",)


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


def _as_float_arr(x: Any, n: int) -> np.ndarray | None:
    try:
        a = np.asarray(x, dtype=float).reshape(-1)
    except Exception:
        return None
    if a.size < n:
        return None
    return a[:n]


def _rising_strict(a: np.ndarray) -> np.ndarray:
    """a[i] > a[i-1] for i >= 1; bar 0 False. Non-finite-safe."""
    out = np.zeros(a.shape[0], dtype=bool)
    if a.shape[0] < 2:
        return out
    fin = np.isfinite(a)
    out[1:] = fin[1:] & fin[:-1] & (a[1:] > 0) & (a[:-1] > 0) & (a[1:] > a[:-1])
    return out


def _falling_strict(a: np.ndarray) -> np.ndarray:
    """a[i] < a[i-1] for i >= 1; bar 0 False. Non-finite-safe."""
    out = np.zeros(a.shape[0], dtype=bool)
    if a.shape[0] < 2:
        return out
    fin = np.isfinite(a)
    out[1:] = fin[1:] & fin[:-1] & (a[1:] > 0) & (a[:-1] > 0) & (a[1:] < a[:-1])
    return out


def _non_falling(a: np.ndarray) -> np.ndarray:
    """a[i] >= a[i-1] for i >= 1; bar 0 neutral-True (primary leg kills bar 0)."""
    out = np.ones(a.shape[0], dtype=bool)
    if a.shape[0] < 2:
        return out
    fin = np.isfinite(a)
    out[1:] = fin[1:] & fin[:-1] & (a[1:] >= a[:-1])
    return out


def _non_rising(a: np.ndarray) -> np.ndarray:
    """a[i] <= a[i-1] for i >= 1; bar 0 neutral-True (primary leg kills bar 0)."""
    out = np.ones(a.shape[0], dtype=bool)
    if a.shape[0] < 2:
        return out
    fin = np.isfinite(a)
    out[1:] = fin[1:] & fin[:-1] & (a[1:] <= a[:-1])
    return out


# === AUGMENT_AT_LOSS_ENABLED — higher-low / lower-high restricted loss-add ===
# LONG:  gain <= 0 AND low_15m_prev rising AND dc_low_15m_prev non-falling.
# SHORT: gain <= 0 AND high_15m_prev falling AND dc_high_15m_prev non-rising.
def augment_at_loss_mask(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, gain_arr: Any = None
) -> np.ndarray | None:
    if not _b(cfg, "AUGMENT_AT_LOSS_ENABLED", False):
        return None
    if gain_arr is None:
        return None
    g = _as_float_arr(gain_arr, n)
    if g is None:
        return None
    at_loss = np.isfinite(g) & (g <= 0.0)
    if is_long:
        lo = _col(npz, "low_15m_prev", n)
        if lo is None:
            return None
        struct = _rising_strict(lo)
        dc = _col(npz, "dc_low_15m_prev", n)
        if dc is not None:
            struct = struct & _non_falling(dc)
    else:
        hi = _col(npz, "high_15m_prev", n)
        if hi is None:
            return None
        struct = _falling_strict(hi)
        dc = _col(npz, "dc_high_15m_prev", n)
        if dc is not None:
            struct = struct & _non_rising(dc)
    return at_loss & struct


# === dispatcher (literal keys; BIBLE 19: no dynamic getattr dispatch) ===
def get(
    switch: str, npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, **kw: Any
) -> np.ndarray | None:
    if switch == "AUGMENT_AT_LOSS_ENABLED":
        return augment_at_loss_mask(npz, n, is_long, cfg, gain_arr=kw.get("gain_arr"))
    raise KeyError(f"unknown augment-at-loss switch: {switch!r}")
