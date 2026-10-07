"""Shared BB band-touch + stoch-cross-3m exit predicates — ONE logic for the live
managers (ez_manage.process_position, tradier_manage.process_position) mirroring
v12_quick_engine.compute_exit_signals (EXIT_STRUCTURAL).

BB_EXIT_AT_LOSS_TF / BB_PROFIT_TAKE_TF (vec ~10260-10273): single-TF exit
('3m'/'5m' map to '15m'; any other value incl. lists = inert):
  LOSS LONG pct_b<0.05 & lower>0 / SHORT pct_b>0.95 & upper>0 (opposite band)
  TAKE LONG pct_b>0.95 & upper>0 / SHORT pct_b<0.05 & lower>0 (favorable band)
STOCH_CROSS_3M_EXIT_ENABLED (vec ~10022-10029):
  LONG (k_prev>=d) & (k<d) & (k>60) / SHORT (k_prev<=d) & (k>d) & (k<40).
Live reads k/d_3m (+ k/d_3m_prev); missing prev falls back to current (= no
cross = no fire, safe direction). Tradier snapshots lacking 3m fall back to 5m
keys (vec _base_safe equivalent). Default OFF = inert.

Pure functions: no state, no I/O. Live defaults inert -> zero live behaviour
change until an operator promotes a value.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Optional, Tuple

_BB_TFS = ("15m", "1h", "4h", "D")


def _f(v: Any, d: float) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def resolve_bb_band_tf(raw: Any) -> Optional[str]:
    """Single-TF parse (vec 10263-10264): '3m'/'5m'->'15m', else must be exact TF."""
    s = str(raw or "OFF").strip()
    if s in ("3m", "5m"):
        return "15m"
    return s if s in _BB_TFS else None


def bb_band_exit_fires(kind: str, tf: str, is_long: bool, ind: Mapping[str, Any]) -> bool:
    """kind 'LOSS' (opposite band) or 'TAKE' (favorable band)."""
    pct = _f((ind or {}).get(f"bb_pct_b_{tf}", 0.5), 0.5)
    lo = _f((ind or {}).get(f"bb_lower_{tf}", 0.0), 0.0)
    up = _f((ind or {}).get(f"bb_upper_{tf}", 0.0), 0.0)
    if kind == "LOSS":
        return ((pct < 0.05) and (lo > 0)) if is_long else ((pct > 0.95) and (up > 0))
    return ((pct > 0.95) and (up > 0)) if is_long else ((pct < 0.05) and (lo > 0))


def bb_band_exits(get: Callable[[str, Any], Any], is_long: bool, ind: Mapping[str, Any]) -> Tuple[bool, str]:
    """Evaluate LOSS then TAKE (vec loop order); returns (fire, reason). Keys are literals (unrolled) so the wiring scan sees both reads."""
    tf_loss = resolve_bb_band_tf(get("BB_EXIT_AT_LOSS_TF", "OFF"))
    if tf_loss and bb_band_exit_fires("LOSS", tf_loss, is_long, ind or {}):
        return True, f"BB_LOSS_{tf_loss}"
    tf_take = resolve_bb_band_tf(get("BB_PROFIT_TAKE_TF", "OFF"))
    if tf_take and bb_band_exit_fires("TAKE", tf_take, is_long, ind or {}):
        return True, f"BB_TAKE_{tf_take}"
    return False, ""


def _kd(ind: Mapping[str, Any], base: str) -> Tuple[float, float, float, float]:
    ind = ind or {}
    k = _f(ind.get(f"stoch_k_{base}", ind.get(f"k_{base}", 50.0)), 50.0)
    d = _f(ind.get(f"stoch_d_{base}", ind.get(f"d_{base}", 50.0)), 50.0)
    kp = _f(ind.get(f"stoch_k_{base}_prev", ind.get(f"k_{base}_prev", k)), k)
    return k, d, kp, d


def stoch_cross_3m_fires(is_long: bool, ind: Mapping[str, Any]) -> bool:
    """Vec 10026-10029 cross with overbought/oversold qualifier."""
    ind = ind or {}
    base = "3m" if (f"stoch_k_3m" in ind or f"k_3m" in ind) else "5m"
    k, d, kp, _ = _kd(ind, base)
    if is_long:
        return (kp >= d) and (k < d) and (k > 60)
    return (kp <= d) and (k > d) and (k < 40)


def stoch_cross_3m_exit(get: Callable[[str, Any], Any], is_long: bool, ind: Mapping[str, Any]) -> Tuple[bool, str]:
    if not bool(get("STOCH_CROSS_3M_EXIT_ENABLED", False)):
        return False, ""
    if stoch_cross_3m_fires(is_long, ind or {}):
        return True, "STOCH_CROSS_3M_EXIT"
    return False, ""
