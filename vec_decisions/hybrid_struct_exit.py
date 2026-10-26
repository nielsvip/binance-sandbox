"""Causal NumPy twin for the Tradier hybrid lower-high/lower-low exit."""

from collections.abc import Mapping
from typing import Any

import numpy as np


def struct_tf(cfg: Any, is_long: bool) -> str:
    tf = str(getattr(cfg, "EXIT_STRUCT_TF", "None") or "None").strip()
    if tf.lower() in ("none", "off", ""):
        side_tf = "LONG_STRUCT_EXIT_TF" if is_long else "SHORT_STRUCT_EXIT_TF"
        tf = str(getattr(cfg, side_tf, "None") or "None").strip()
    return "" if tf.lower() in ("none", "off", "") else tf


def hybrid_struct_exit_mask(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray:
    """Fire only on a selected-TF open change; compare the prior completed bar's high/low pair."""
    mask = np.zeros(n, dtype=bool)
    if str(getattr(cfg, "MODE", "crypto") or "crypto") != "tradier":
        return mask
    if not bool(getattr(cfg, "HYBRID_STRUCT_EXIT_ENABLED", True)):
        return mask
    tf = struct_tf(cfg, is_long)
    if not tf:
        return mask
    op = np.asarray(npz.get(f"open_{tf}", np.zeros(n)), dtype=float).reshape(-1)[:n]
    hi = np.asarray(npz.get(f"high_{tf}_prev", np.zeros(n)), dtype=float).reshape(-1)[:n]
    lo = np.asarray(npz.get(f"low_{tf}_prev", np.zeros(n)), dtype=float).reshape(-1)[:n]
    if min(op.size, hi.size, lo.size) < n:
        return mask
    boundary = np.zeros(n, dtype=bool)
    boundary[1:] = (op[1:] > 0) & (op[:-1] > 0) & (np.abs(op[1:] - op[:-1]) > 1e-8)
    prev_valid = np.zeros(n, dtype=bool)
    prev_valid[1:] = (hi[:-1] > 0) & (lo[:-1] > 0)
    if is_long:
        structure_break = (hi < np.roll(hi, 1)) & (lo < np.roll(lo, 1))
    else:
        structure_break = (hi > np.roll(hi, 1)) & (lo > np.roll(lo, 1))
    mask = boundary & prev_valid & (hi > 0) & (lo > 0) & structure_break
    mask[0] = False
    return mask


def hybrid_struct_exit_step(current_open: float, current_high_prev: float, current_low_prev: float, state: dict[str, float] | None, is_long: bool) -> tuple[bool, dict[str, float] | None]:
    """Causal per-bar update matching live's first-sample and open-change state behavior."""
    try:
        op, hi, lo = float(current_open), float(current_high_prev), float(current_low_prev)
    except (TypeError, ValueError):
        return False, state
    if not (np.isfinite(op) and np.isfinite(hi) and np.isfinite(lo)) or op <= 0 or hi <= 0 or lo <= 0:
        return False, state
    next_state = {"open": op, "high_prev": hi, "low_prev": lo}
    if state is None:
        return False, next_state
    try:
        last_open = float(state["open"])
        last_hi = float(state["high_prev"])
        last_lo = float(state["low_prev"])
    except (KeyError, TypeError, ValueError):
        return False, next_state
    boundary = np.abs(np.asarray([op - last_open], dtype=float)) > 1e-8
    if not bool(boundary[0]):
        return False, state
    if is_long:
        fire = bool((np.asarray([hi]) < np.asarray([last_hi]))[0] and (np.asarray([lo]) < np.asarray([last_lo]))[0])
    else:
        fire = bool((np.asarray([hi]) > np.asarray([last_hi]))[0] and (np.asarray([lo]) > np.asarray([last_lo]))[0])
    return fire, next_state
