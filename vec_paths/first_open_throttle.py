"""
vec_paths/first_open_throttle.py — Throttle the very first OPEN of a sim.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS
═══════════════════════════════════════════════════════════════════════════════
Vec engine's overtrade gate at v8_vec_sweep.py:2067-2083 reads:

    if VEC_OVERTRADE_FIX_ENABLED
        and _vec_check_dup_guard is not None
        and state.qty <= 0.0001
        and state.last_augment_ts > 0       # ← exemption
    ):
        block = check_dup_guard_block(...)
        if block: continue

The `state.last_augment_ts > 0` exemption means the cooldown DOES NOT engage
until AFTER the first fill. So when v8_vec_sweep starts a sweep on a fresh
sym at simulation start (state.last_augment_ts = 0.0), the OPEN path is
unguarded. If multiple entry triggers (wt_open_ok, reentry fire, GR fire,
delta_entry, b15, connors, breakout_retest, bb_break, brs, btc, rz_break,
rz_cascade, qb) align on early bars, vec can emit 10-50 OPENs in the first
~100 bars — each followed by some flavor of CLOSE inside a few bars.

Live has no equivalent surge because:
  (a) Live `_recent_reduces` Redis state persists across process restarts.
  (b) Live tradeable_keys are hand-picked, not "everything in symbols.json".
  (c) Live OPEN requires execute_now() which checks per-account cooldowns.

This module adds an explicit "first-open throttle" — a bar-since-sim-start
gate. It's intentionally simple and config-driven so it's easy to disable.

═══════════════════════════════════════════════════════════════════════════════
ENABLEMENT
═══════════════════════════════════════════════════════════════════════════════
Activated via `config.VEC_FIRST_OPEN_THROTTLE_BARS` (integer; 0 = OFF, default
0). When > 0, the first OPEN of the sim cannot fire until bar index >= this
value.

═══════════════════════════════════════════════════════════════════════════════
PUBLIC API
═══════════════════════════════════════════════════════════════════════════════
    is_first_open_throttled(bar_idx, state, cfg) -> bool

True  → caller should skip this OPEN attempt.
False → OPEN may proceed (subject to other gates).

═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
from typing import Any


def is_first_open_throttled(bar_idx: int, state: Any, cfg: Any) -> bool:
    """Block the first OPEN of a sim until bar_idx >= VEC_FIRST_OPEN_THROTTLE_BARS.

    Once a position has been opened (state.last_augment_ts > 0), this gate
    is permanently disengaged for the rest of the sim. After a CLOSE, the
    gate stays disengaged because state.last_augment_ts is not reset.
    """
    try:
        bars = int(getattr(cfg, "VEC_FIRST_OPEN_THROTTLE_BARS", 0))
    except (TypeError, ValueError):
        bars = 0
    if bars <= 0:
        return False
    last_aug = float(getattr(state, "last_augment_ts", 0.0) or 0.0)
    if last_aug > 0:
        return False
    return int(bar_idx) < bars
