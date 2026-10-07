"""
vec_paths/ratio_reduce_sym_proxy.py — Per-symbol proxy for RATIO_REDUCE.

═══════════════════════════════════════════════════════════════════════════════
WHY A PROXY?
═══════════════════════════════════════════════════════════════════════════════
The canonical vec_paths/ratio_reduce.py operates on a portfolio level
(pool_long_count vs pool_short_count). v8_vec_sweep.py runs symbol-by-symbol
in a tight per-sym loop — there is no global portfolio object at the per-bar
boundary. Live, however, fires RATIO_REDUCE constantly: "Manual/System
Detection" REDUCE events are the dominant exit driver (11,383 in the live
audit, vs 293 for R1).

Live's ratio reduce semantics (ez_positions_quick.py runtime):
    skew = max(pool_long, pool_short) / (min(pool_long, pool_short) + 1)
    if skew > RATIO_MULTIPLIER:
        reduce_targets = positions on the overweight side, lowest gain first
        each target: REDUCE qty = pos_qty / RATIO_MULTIPLIER

To approximate this in a per-sym vec sweep WITHOUT cross-sym state, we use
two proxies:
  (a) A gain-driven periodic trim: when a position has been open longer than
      RATIO_TRIM_MIN_AGE_S AND gain < RATIO_TRIM_GAIN_FLOOR, trim by 1/RATIO_MULTIPLIER.
  (b) A cap: total cumulative trim count per position <= MAX_AUGMENTS_PER_POSITION
      (so we don't bleed it to zero).

This proxy is intentionally CONSERVATIVE — it under-fires vs live (because we
don't have cross-sym skew context). Its purpose is to introduce REDUCE events
where vec currently has NONE on the slow-trim cadence, drawing vec closer to
the live event distribution.

═══════════════════════════════════════════════════════════════════════════════
ENABLEMENT
═══════════════════════════════════════════════════════════════════════════════
Activated via `config.VEC_RATIO_REDUCE_PROXY_ENABLED`. Default = False
(byte-exact preservation). When False, check_ratio_reduce_proxy() returns None
on every call → vec engine unchanged.

═══════════════════════════════════════════════════════════════════════════════
PUBLIC API
═══════════════════════════════════════════════════════════════════════════════
    check_ratio_reduce_proxy(state, ts, mark, gain, cfg) -> Optional[dict]

Returns None (no action) or:
    {
        "frac": float,        # fraction of qty to reduce
        "reason": str,        # for the event reason field
    }

═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
from typing import Any, Optional


def _sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def _is_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "VEC_RATIO_REDUCE_PROXY_ENABLED", False))


def check_ratio_reduce_proxy(
    state: Any,
    ts: float,
    mark: float,
    gain: float,
    cfg: Any,
) -> Optional[dict]:
    """Decide whether to fire a RATIO_REDUCE proxy trim at this bar.

    Args:
        state: vec_engine position state (must have .qty, .opened_at,
               .last_reduce_ts, .augmented_count)
        ts: bar timestamp (unix seconds)
        mark: current mark price
        gain: current position gain percent
        cfg: VecConfig

    Returns:
        None if no trim; dict with 'frac' and 'reason' otherwise.
    """
    if not _is_enabled(cfg):
        return None

    qty = float(getattr(state, "qty", 0.0) or 0.0)
    if qty <= 0.0001:
        return None

    # Foothold check — don't trim sub-min positions
    min_pos = _sf(getattr(cfg, "MIN_POSITION_SIZE", 45.0), 45.0)
    if qty * mark < min_pos * 1.2:
        return None

    # Cool-down — at most one proxy trim per RATIO_TRIM_MIN_INTERVAL_S
    min_interval = _sf(getattr(cfg, "RATIO_TRIM_MIN_INTERVAL_S", 14400.0), 14400.0)  # 4hr default
    last_reduce = _sf(getattr(state, "last_reduce_ts", 0.0), 0.0)
    if last_reduce > 0 and (ts - last_reduce) < min_interval:
        return None

    # Age gate — wait until the position has matured
    min_age = _sf(getattr(cfg, "RATIO_TRIM_MIN_AGE_S", 7200.0), 7200.0)  # 2hr default
    opened_at = _sf(getattr(state, "opened_at", 0.0), 0.0)
    if opened_at <= 0 or (ts - opened_at) < min_age:
        return None

    # Gain gate — only trim when in a draw-down or stalled gain.
    # gain <= floor → fire the proxy. Default floor = 0.5% (live observation:
    # ratio_reduce dominantly fires on stalled-gain positions).
    gain_floor = _sf(getattr(cfg, "RATIO_TRIM_GAIN_FLOOR", 0.5), 0.5)
    if gain > gain_floor:
        return None

    # Frac = 1/RATIO_MULTIPLIER (default 25%)
    ratio_mult = _sf(getattr(cfg, "RATIO_MULTIPLIER", 4.0), 4.0)
    frac = 1.0 / ratio_mult if ratio_mult > 1.0 else 0.25
    frac = max(0.05, min(0.5, frac))

    return {
        "frac": frac,
        "reason": f"RATIO_REDUCE_PROXY_g{gain:.2f}",
    }
