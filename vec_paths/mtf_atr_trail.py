"""
vec_paths/mtf_atr_trail.py — canonical MTF_ATR_TRAIL ratchet (live-parity).

This is the SHARED single-source-of-truth for the live MTF_ATR_TRAIL compound
exit so that every backtest engine (Tier-1 v8_quick_engine, Tier-2
backtest_v8_engine, v8_vec_sweep, per_sym engines) fires it IDENTICALLY to live.

It is a byte-faithful replication of the inline live logic at
    tradier_manage.py:2318-2333  (and the ez_manage.py mirror)
NOT the sweep-only experiment in vec_paths/atr_trail.py (ATR_TRAIL_SWEEP_*).

LIVE STATUS (2026-05-29): MTF_ATR_TRAIL_ENABLED=True on BOTH platforms,
MTF_ATR_TRAIL_MULT=2.0, gated behind master MTF_EXIT_USE_COMPOUND=True.
Crypto TF=MTF_ATR_TRAIL_TF (15m); stocks TF=MTF_ATR_TRAIL_TF_TRADIER (5m).

RATCHET (from live):
    LONG : cand = max(entry - mult*atr, current - mult*atr)
           trail = max(prev_trail, cand)  (prev>0)  else cand   -- ratchets UP
           fire when current_price < trail
    SHORT: cand = min(entry + mult*atr, current + mult*atr)
           trail = min(prev_trail, cand)  (prev>0)  else cand   -- ratchets DOWN
           fire when current_price > trail
    reason = f"MTF_ATR_TRAIL_{tf}_x{mult}_lvl{trail:.4f}"
"""
from __future__ import annotations

from typing import Any, Tuple


def mtf_atr_trail_tf(cfg: Any, mode: str) -> str:
    """Return the ATR timeframe used by the trail for this mode (matches live)."""
    if mode == "tradier":
        return str(getattr(cfg, "MTF_ATR_TRAIL_TF_TRADIER", getattr(cfg, "MTF_ATR_TRAIL_TF", "5m")))
    return str(getattr(cfg, "MTF_ATR_TRAIL_TF", "15m"))


def mtf_atr_trail_enabled(cfg: Any) -> bool:
    """True only when the master compound switch AND the trail switch are on (matches live gate)."""
    return bool(getattr(cfg, "MTF_EXIT_USE_COMPOUND", False)) and bool(getattr(cfg, "MTF_ATR_TRAIL_ENABLED", False))


def update_and_check(state: dict, entry_price: float, current_price: float, atr: float, is_long: bool, mult: float, tf: str) -> Tuple[bool, str, float]:
    """Update the ratcheting trail in `state` and return (fired, reason, trail_level).

    `state` is a per-position dict carrying key 'trail' (0.0 = not yet set);
    mirror of live trade_manager.mtf_compound_exit_state[position_key]['trail'].
    Returns fired=False / reason="" when inputs are invalid or the stop did not fire.
    """
    if atr <= 0 or entry_price <= 0 or current_price <= 0:
        return False, "", float(state.get("trail", 0.0))
    prev = float(state.get("trail", 0.0))
    if is_long:
        cand = max(entry_price - mult * atr, current_price - mult * atr)
        trail = max(prev, cand) if prev > 0 else cand
        state["trail"] = trail
        if trail > 0 and current_price < trail:
            return True, f"MTF_ATR_TRAIL_{tf}_x{mult}_lvl{trail:.4f}", trail
    else:
        cand = min(entry_price + mult * atr, current_price + mult * atr)
        trail = min(prev, cand) if prev > 0 else cand
        state["trail"] = trail
        if trail > 0 and current_price > trail:
            return True, f"MTF_ATR_TRAIL_{tf}_x{mult}_lvl{trail:.4f}", trail
    return False, "", float(state["trail"])
