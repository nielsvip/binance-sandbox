"""vec_paths/open_intent_size_gates.py — ABSOLUTE_OPEN_LOCK + PREFLIGHT_INTENT_LOCK + HARD_SIZE_GATE.

LIVE SCALAR SITES MIRRORED FROM:
  - ez_manage.py:13790-13863 (ABSOLUTE_OPEN_LOCK)
      Unconditional rate limit at top of execute_now. TTL 300s (_ABSOLUTE_OPEN_LOCK_TTL).
      Per-position_key. Redis-persisted across restarts. NO bypass via reason/is_hedge.
      Fires on every entry-class action (OPEN/AUGMENT/REENTRY/REVERSE/HEDGE_OPEN/...).
      USER CONTRACT (CLAUDE.md, position_key conventions): REENTRY on flat
      (positionAmt==0) is allowed to refill empty tradeable_keys — backtest convention.
      HEDGE bypass is allowed at the engine level to keep hedge protection alive.

  - ez_manage.py:13864-13885 (PREFLIGHT_INTENT_LOCK)
      Closes the fill-propagation race window. Scans _recent_opens + _AUGMENT_LOCK
      maps; if either was stamped within 60s, BLOCK. Stamps both maps BEFORE any
      await. REENTRY is exempt (see line 13874). is_hedge does NOT exempt.
      In backtest there is no concurrency → default-pass (returns OK) UNLESS the
      caller explicitly supplies an intent_locks_map seeded with prior bar stamps.

  - ez_manage.py:13982-14002 (HARD_SIZE_GATE)
      If position_value >= START_POSITION_SIZE AND gain < MIN_GAIN, BLOCK every
      open-side action (CLOSE/REDUCE paths bypass). One last guard before fill so
      a fully-foothold-sized position can ONLY be added when profitable.

Public API:
    evaluate_open_intent_size_gates_core(...) -> (blocked: bool, reason: str, gate_fired: str)
    evaluate_open_intent_size_gates_vec(...)  -> dict of np.ndarray (blocked, gate_fired_code)

Gate-fired codes (int8 for vec output):
    0 = NONE         (allowed)
    1 = ABSOLUTE_OPEN_LOCK
    2 = PREFLIGHT_INTENT_LOCK
    3 = HARD_SIZE_GATE
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

import numpy as np

# ─── Default knob values mirror live (CLAUDE.md + ez_manage.py) ─────────────────
DEFAULT_ABSOLUTE_OPEN_LOCK_SECONDS = 300.0   # ez_manage.py:269 _ABSOLUTE_OPEN_LOCK_TTL
DEFAULT_PREFLIGHT_INTENT_LOCK_SECONDS = 60.0  # ez_manage.py:13878 _pf_race_window
DEFAULT_HARD_SIZE_GATE_ENABLED = True         # gate is always-on in live code; flag for sweep-disable
DEFAULT_START_POSITION_SIZE = 55.0            # config.START_POSITION_SIZE
DEFAULT_MIN_GAIN = 3.0                        # config.MIN_GAIN — HARD_SIZE_GAIN_FLOOR semantic

# Live entry-action set from ez_manage.py:13796.
_ENTRY_ACTIONS = frozenset({
    'OPEN', 'AUGMENT', 'REENTRY', 'REVERSE', 'REVERSE_AUGMENT',
    'QUICK_OPEN', 'QUICK_AUGMENT', 'QUICK_HEDGE_OPEN', 'QUICK_HEDGE_AUGMENT',
    'HEDGE_OPEN',
})

# Live reduce/close action set from ez_manage.py:13984 (HARD_SIZE_GATE _sg_is_reduce branch).
_REDUCE_ACTIONS = frozenset({
    'CLOSE', 'REDUCE', 'SELL', 'QUICK_CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE',
    'STOP_MAJOR_LOSS_REDUCE', 'STOP_FUNCTIONS_KILL', 'HEDGE_CLOSE',
})

# gate_fired tokens
GATE_NONE = "NONE"
GATE_ABSOLUTE = "ABSOLUTE_OPEN_LOCK"
GATE_PREFLIGHT = "PREFLIGHT_INTENT_LOCK"
GATE_SIZE = "HARD_SIZE_GATE"

CODE_NONE = 0
CODE_ABSOLUTE = 1
CODE_PREFLIGHT = 2
CODE_SIZE = 3

_GATE_TO_CODE = {GATE_NONE: CODE_NONE, GATE_ABSOLUTE: CODE_ABSOLUTE,
                 GATE_PREFLIGHT: CODE_PREFLIGHT, GATE_SIZE: CODE_SIZE}
_CODE_TO_GATE = {v: k for k, v in _GATE_TO_CODE.items()}


def _cfg_get(cfg: Any, name: str, default: Any) -> Any:
    if cfg is None:
        return default
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _is_open_action(action: str) -> bool:
    """Mirror of ez_manage.py:13796-13798 — entry-class action detection."""
    a = (action or '').upper()
    base = (
        a in _ENTRY_ACTIONS
        or ('OPEN' in a and 'CLOSE' not in a)
        or 'HEDGE' in a
        or 'ENTRY' in a
        or 'AUGMENT' in a
    )
    return base and 'CLOSE' not in a and 'REDUCE' not in a and 'KILL' not in a


def _is_reduce_action(action: str, reason: str = '') -> bool:
    """Mirror of ez_manage.py:13984."""
    a = (action or '').upper()
    r = (reason or '').upper()
    return a in _REDUCE_ACTIONS or 'CLOSE' in r or 'REDUCE' in r


def evaluate_open_intent_size_gates_core(
    action: str,
    position_key: str,
    now_ts: float,
    last_open_attempt_ts: float,
    intent_locks_map: Optional[Mapping[str, float]],
    proposed_qty: float,
    gain: float,
    config: Any,
    *,
    position_amt: float = 0.0,
    mark_price: float = 0.0,
    is_hedge: bool = False,
    reason: str = '',
) -> Tuple[bool, str, str]:
    """Scalar evaluation of the three gates in live order.

    Args:
        action:                Prospective action (e.g. 'OPEN'/'AUGMENT'/'REENTRY').
        position_key:          'BTC_LONG' style key.
        now_ts:                Current bar unix timestamp (seconds).
        last_open_attempt_ts:  Last successful open/augment attempt ts on this key
                               (== _ABSOLUTE_OPEN_LOCK[pk] - TTL). 0.0 if never.
        intent_locks_map:      {pk: stamp_ts} for the PREFLIGHT lock — typically the
                               concatenation of _recent_opens and _AUGMENT_LOCK in
                               live code. In backtest: None or {} → default-pass.
        proposed_qty:          Order qty being requested (reserved for future caps;
                               not consulted by the three gates today).
        gain:                  Current position gain in percent (effective gain,
                               PPL-aware on live; raw is acceptable in backtest).
        config:                Config module / dict / None.
        position_amt:          Current |positionAmt|. Used for HARD_SIZE_GATE size calc.
        mark_price:            Current mark price. Used for HARD_SIZE_GATE size calc.
        is_hedge:              Pass-through for engine-level bypass; this module
                               does NOT auto-bypass on is_hedge — caller decides
                               whether to skip calling us for hedge-open paths.
        reason:                Reason string. REENTRY/RECLAIM/GUARANTEED-style
                               reasons exempt the PREFLIGHT lock per live code.

    Returns:
        (blocked, block_reason_string, gate_fired_token).
        gate_fired in {NONE, ABSOLUTE_OPEN_LOCK, PREFLIGHT_INTENT_LOCK, HARD_SIZE_GATE}.
    """
    a_up = (action or '').upper()
    if not _is_open_action(a_up):
        return False, '', GATE_NONE  # non-entry actions are out of scope

    abs_secs = float(_cfg_get(config, 'ABSOLUTE_OPEN_LOCK_SECONDS', DEFAULT_ABSOLUTE_OPEN_LOCK_SECONDS))
    pf_secs = float(_cfg_get(config, 'PREFLIGHT_INTENT_LOCK_SECONDS', DEFAULT_PREFLIGHT_INTENT_LOCK_SECONDS))
    hsg_on = bool(_cfg_get(config, 'HARD_SIZE_GATE_ENABLED', DEFAULT_HARD_SIZE_GATE_ENABLED))
    hsg_size = float(_cfg_get(config, 'START_POSITION_SIZE', DEFAULT_START_POSITION_SIZE))
    hsg_floor = float(_cfg_get(config, 'HARD_SIZE_GAIN_FLOOR',
                               _cfg_get(config, 'MIN_GAIN', DEFAULT_MIN_GAIN)))

    # ── Gate 1: ABSOLUTE_OPEN_LOCK ─────────────────────────────────────────────
    # USER CONTRACT: REENTRY on a FLAT position (positionAmt==0) refills tradeable
    # keys and bypasses the absolute lock. HEDGE also bypasses (engine concern).
    is_reentry = 'REENTRY' in a_up
    is_flat_reentry = is_reentry and float(position_amt) == 0.0
    abs_bypass = is_flat_reentry or bool(is_hedge)
    if not abs_bypass and last_open_attempt_ts > 0:
        elapsed = float(now_ts) - float(last_open_attempt_ts)
        if 0.0 <= elapsed < abs_secs:
            remaining = abs_secs - elapsed
            return True, f"BLOCKED_ABSOLUTE_OPEN_LOCK_{remaining:.0f}s", GATE_ABSOLUTE

    # ── Gate 2: PREFLIGHT_INTENT_LOCK ─────────────────────────────────────────
    # Race-window guard. Live exemptions: REENTRY / GUARANTEED / RECLAIM_LEVEL /
    # QUICK_RECOVERY reasons (ez_manage.py:13874).
    r_up = (reason or '').upper()
    pf_exempt = (
        is_reentry
        or 'GUARANTEED' in r_up
        or 'RECLAIM_LEVEL' in r_up
        or 'QUICK_RECOVERY' in r_up
    )
    if not pf_exempt and intent_locks_map and position_key:
        stamp = float(intent_locks_map.get(position_key, 0.0) or 0.0)
        if stamp > 0.0:
            age = float(now_ts) - stamp
            if 0.0 <= age < pf_secs:
                return True, f"BLOCKED_PREFLIGHT_INTENT_LOCK_{age:.1f}s", GATE_PREFLIGHT

    # ── Gate 3: HARD_SIZE_GATE ────────────────────────────────────────────────
    if hsg_on and not _is_reduce_action(a_up, reason):
        pos_val = abs(float(position_amt)) * float(mark_price) if mark_price > 0 else 0.0
        if pos_val >= hsg_size and float(gain) < hsg_floor:
            return True, (
                f"BLOCKED_HARD_SIZE_GATE_{float(gain):.2f}_floor{hsg_floor:.2f}pct"
            ), GATE_SIZE

    return False, '', GATE_NONE


def evaluate_open_intent_size_gates_vec(
    action: np.ndarray,
    position_key: np.ndarray,
    now_ts: np.ndarray,
    last_open_attempt_ts: np.ndarray,
    intent_lock_stamp: np.ndarray,
    proposed_qty: np.ndarray,
    gain: np.ndarray,
    config: Any,
    *,
    position_amt: Optional[np.ndarray] = None,
    mark_price: Optional[np.ndarray] = None,
    is_hedge: Optional[np.ndarray] = None,
    reason: Optional[np.ndarray] = None,
) -> dict:
    """Vectorized evaluation across N candidate attempts.

    All array args are shape (N,). For arrays we don't get from the caller, we
    use sensible defaults (zeros / empty strings / False). Per-bar semantics
    are bit-identical to _core: we mirror live precedence ABSOLUTE → PREFLIGHT →
    HARD_SIZE_GATE — first hit wins.

    Returns dict with:
        blocked:     (N,) bool
        gate_fired:  (N,) np.int8 (use _CODE_TO_GATE to symbolize)
        reason_code: (N,) np.int8 alias of gate_fired (for downstream symmetry)
        remaining_s: (N,) float32 — seconds of lock remaining when blocked (0 else)
    """
    n = int(np.asarray(action).shape[0])
    out_blocked = np.zeros(n, dtype=bool)
    out_gate = np.zeros(n, dtype=np.int8)
    out_remaining = np.zeros(n, dtype=np.float32)
    if n == 0:
        return {"blocked": out_blocked, "gate_fired": out_gate,
                "reason_code": out_gate, "remaining_s": out_remaining}

    abs_secs = float(_cfg_get(config, 'ABSOLUTE_OPEN_LOCK_SECONDS', DEFAULT_ABSOLUTE_OPEN_LOCK_SECONDS))
    pf_secs = float(_cfg_get(config, 'PREFLIGHT_INTENT_LOCK_SECONDS', DEFAULT_PREFLIGHT_INTENT_LOCK_SECONDS))
    hsg_on = bool(_cfg_get(config, 'HARD_SIZE_GATE_ENABLED', DEFAULT_HARD_SIZE_GATE_ENABLED))
    hsg_size = float(_cfg_get(config, 'START_POSITION_SIZE', DEFAULT_START_POSITION_SIZE))
    hsg_floor = float(_cfg_get(config, 'HARD_SIZE_GAIN_FLOOR',
                               _cfg_get(config, 'MIN_GAIN', DEFAULT_MIN_GAIN)))

    action_arr = np.asarray(action, dtype=object)
    now_arr = np.asarray(now_ts, dtype=np.float64)
    last_arr = np.asarray(last_open_attempt_ts, dtype=np.float64)
    stamp_arr = np.asarray(intent_lock_stamp, dtype=np.float64)
    gain_arr = np.asarray(gain, dtype=np.float64)
    if position_amt is None:
        pa_arr = np.zeros(n, dtype=np.float64)
    else:
        pa_arr = np.abs(np.asarray(position_amt, dtype=np.float64))
    if mark_price is None:
        mp_arr = np.zeros(n, dtype=np.float64)
    else:
        mp_arr = np.asarray(mark_price, dtype=np.float64)
    if is_hedge is None:
        hedge_arr = np.zeros(n, dtype=bool)
    else:
        hedge_arr = np.asarray(is_hedge, dtype=bool)
    if reason is None:
        reason_arr = np.array([''] * n, dtype=object)
    else:
        reason_arr = np.asarray(reason, dtype=object)

    # Vectorized is_open_action — match scalar exactly
    a_up_arr = np.array([(a or '').upper() for a in action_arr], dtype=object)
    is_entry_set = np.array([a in _ENTRY_ACTIONS for a in a_up_arr], dtype=bool)
    has_open = np.array(['OPEN' in a and 'CLOSE' not in a for a in a_up_arr], dtype=bool)
    has_hedge_str = np.array(['HEDGE' in a for a in a_up_arr], dtype=bool)
    has_entry_str = np.array(['ENTRY' in a for a in a_up_arr], dtype=bool)
    has_aug_str = np.array(['AUGMENT' in a for a in a_up_arr], dtype=bool)
    base_open = is_entry_set | has_open | has_hedge_str | has_entry_str | has_aug_str
    bad_kw = np.array(['CLOSE' in a or 'REDUCE' in a or 'KILL' in a for a in a_up_arr], dtype=bool)
    is_open = base_open & ~bad_kw
    is_reentry = np.array(['REENTRY' in a for a in a_up_arr], dtype=bool)

    r_up_arr = np.array([(r or '').upper() for r in reason_arr], dtype=object)
    pf_exempt = is_reentry | np.array([
        ('GUARANTEED' in r) or ('RECLAIM_LEVEL' in r) or ('QUICK_RECOVERY' in r)
        for r in r_up_arr
    ], dtype=bool)
    is_reduce_arr = np.array([
        ((a in _REDUCE_ACTIONS) or ('CLOSE' in r) or ('REDUCE' in r))
        for a, r in zip(a_up_arr, r_up_arr)
    ], dtype=bool)

    # ── Gate 1: ABSOLUTE_OPEN_LOCK ────────────────────────────────────────────
    flat_reentry = is_reentry & (pa_arr == 0.0)
    abs_bypass = flat_reentry | hedge_arr
    elapsed_abs = now_arr - last_arr
    abs_fire = is_open & ~abs_bypass & (last_arr > 0) & (elapsed_abs >= 0) & (elapsed_abs < abs_secs)

    # ── Gate 2: PREFLIGHT_INTENT_LOCK ─────────────────────────────────────────
    elapsed_pf = now_arr - stamp_arr
    pf_fire = is_open & ~pf_exempt & (stamp_arr > 0) & (elapsed_pf >= 0) & (elapsed_pf < pf_secs)

    # ── Gate 3: HARD_SIZE_GATE ────────────────────────────────────────────────
    pos_val = pa_arr * mp_arr
    size_fire = (
        hsg_on
        & is_open
        & ~is_reduce_arr
        & (pos_val >= hsg_size)
        & (gain_arr < hsg_floor)
    )

    # Live precedence: ABSOLUTE → PREFLIGHT → SIZE. First match wins.
    gate_code = np.where(abs_fire, CODE_ABSOLUTE,
                np.where(pf_fire & ~abs_fire, CODE_PREFLIGHT,
                np.where(size_fire & ~abs_fire & ~pf_fire, CODE_SIZE, CODE_NONE))).astype(np.int8)
    blocked = gate_code != CODE_NONE

    remaining = np.zeros(n, dtype=np.float32)
    remaining = np.where(gate_code == CODE_ABSOLUTE,
                         (abs_secs - elapsed_abs).astype(np.float32), remaining)
    remaining = np.where(gate_code == CODE_PREFLIGHT,
                         elapsed_pf.astype(np.float32), remaining)
    remaining = np.where(gate_code == CODE_SIZE, 0.0, remaining)
    return {
        "blocked": blocked,
        "gate_fired": gate_code,
        "reason_code": gate_code,
        "remaining_s": remaining,
    }


__all__ = [
    "evaluate_open_intent_size_gates_core",
    "evaluate_open_intent_size_gates_vec",
    "GATE_NONE", "GATE_ABSOLUTE", "GATE_PREFLIGHT", "GATE_SIZE",
    "CODE_NONE", "CODE_ABSOLUTE", "CODE_PREFLIGHT", "CODE_SIZE",
    "DEFAULT_ABSOLUTE_OPEN_LOCK_SECONDS",
    "DEFAULT_PREFLIGHT_INTENT_LOCK_SECONDS",
    "DEFAULT_HARD_SIZE_GATE_ENABLED",
    "DEFAULT_START_POSITION_SIZE",
    "DEFAULT_MIN_GAIN",
]
