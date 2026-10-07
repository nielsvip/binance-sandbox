"""vec_decisions/bb_pullback_gate.py — SHARED scalar+vectorized BB_PULLBACK_GATE predicate.

Single source of truth for the live BB pullback gate. The live scalar path
(tradier_manage / ez_manage via tradier_matrix_gates.bb_pullback_gate_blocks)
AND the vectorized backtest path (v12_quick_engine compute_entry_signals)
BOTH derive their block decision from the same pure predicate below, so the two
paths CANNOT drift.

Faithful to tradier_matrix_gates.py:bb_pullback_gate_blocks (lines ~25-55) which
is the live scalar imported by tradier_manage.py:9388-9422:

    if not BB_PULLBACK_GATE_ENABLED: return False
    tf = BB_PULLBACK_GATE_TF or "15m"
    pct_b = indicators[f"bb_pct_b_{tf}"]
    if pct_b is None: return False
    boundary = BB_PULLBACK_GATE_LONG_MAX if is_long else BB_PULLBACK_GATE_SHORT_MIN
    return pct_b > boundary if is_long else pct_b < boundary

TEMPLATE 280 wiring: BB_PULLBACK_GATE_TF, BB_PULLBACK_GATE_ENABLED,
BB_PULLBACK_GATE_LONG_MAX, BB_PULLBACK_GATE_SHORT_MIN (QuickConfig 3893-3896).

The vector mask returns True where the gate BLOCKS entry (i.e. would veto).
Caller applies: _base_entry = _base_entry & ~block_mask  (or extra_ok & ~block).
Missing bb_pct_b_* fails open (no block).
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np


def _number(source: Mapping[str, Any] | None, key: str) -> float | None:
    try:
        v = (source or {}).get(key)
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def bb_pullback_gate_blocks(
    indicators: Mapping[str, Any] | None,
    cfg: Any,
    is_long: bool,
) -> bool:
    """Live scalar: whether gate blocks entry. Mirrors tradier_matrix_gates."""
    if not bool(getattr(cfg, "BB_PULLBACK_GATE_ENABLED", False)):
        return False
    # BB_PULLBACK_GATE_FILTER_TF selects the TF of THIS gate (OFF = BB_PULLBACK_GATE_TF); it is not a second,
    # stricter 0.20/0.80 gate stacked on top (that double gate zeroed 61% of stock sym_sides at 1h)
    _ftf = str(getattr(cfg, "BB_PULLBACK_GATE_FILTER_TF", "OFF") or "OFF").strip()
    tf = _ftf if _ftf.upper() != "OFF" else str(getattr(cfg, "BB_PULLBACK_GATE_TF", "15m") or "15m")
    pct_b = _number(indicators, f"bb_pct_b_{tf}")
    if pct_b is None:
        return False
    try:
        boundary = float(
            getattr(cfg, "BB_PULLBACK_GATE_LONG_MAX", 0.30)
            if is_long
            else getattr(cfg, "BB_PULLBACK_GATE_SHORT_MIN", 0.70)
        )
    except (TypeError, ValueError):
        return False
    return pct_b > boundary if is_long else pct_b < boundary


def bb_pullback_gate_vec(
    npz: Mapping[str, Any],
    n: int,
    cfg: Any,
    is_long: bool,
) -> np.ndarray:
    """Vectorized BB_PULLBACK_GATE block mask. SAME thresholds+predicate as scalar.

    Returns bool ndarray shape (n,): True where entry should be BLOCKED.
    Reads npz[f"bb_pct_b_{tf}"]; absent key -> all False (fail open).
    """
    if not bool(getattr(cfg, "BB_PULLBACK_GATE_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    # BB_PULLBACK_GATE_FILTER_TF selects the TF of THIS gate (OFF = BB_PULLBACK_GATE_TF); it is not a second,
    # stricter 0.20/0.80 gate stacked on top (that double gate zeroed 61% of stock sym_sides at 1h)
    _ftf = str(getattr(cfg, "BB_PULLBACK_GATE_FILTER_TF", "OFF") or "OFF").strip()
    tf = _ftf if _ftf.upper() != "OFF" else str(getattr(cfg, "BB_PULLBACK_GATE_TF", "15m") or "15m")
    key = f"bb_pct_b_{tf}"
    if key not in npz:
        return np.zeros(n, dtype=bool)
    arr = np.asarray(npz[key], dtype=float)
    if arr.size < n:
        # pad/truncate to n
        tmp = np.full(n, np.nan, dtype=float)
        tmp[: min(n, arr.size)] = arr[: min(n, arr.size)]
        arr = tmp
    else:
        arr = arr[:n]
    # NaN fails open -> not blocked
    valid = np.isfinite(arr)
    try:
        boundary = float(
            getattr(cfg, "BB_PULLBACK_GATE_LONG_MAX", 0.30)
            if is_long
            else getattr(cfg, "BB_PULLBACK_GATE_SHORT_MIN", 0.70)
        )
    except (TypeError, ValueError):
        return np.zeros(n, dtype=bool)
    if is_long:
        blocked = valid & (arr > boundary)
    else:
        blocked = valid & (arr < boundary)
    return blocked
