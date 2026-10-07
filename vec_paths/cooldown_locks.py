"""HARD_REDUCE_LOCK + HARD_AUGMENT_LOCK + AUGMENTATION_COOLDOWN_MAP — scalar + vectorized.

LIVE LOGIC MIRRORED FROM:
  - ez_manage.py:14018-14037 (HARD_REDUCE_LOCK — 15s cooldown on reduce-class actions)
  - ez_manage.py:14062-14081 (HARD_AUGMENT_LOCK — 900s cooldown on augment-class actions
                              unless gain >= MIN_GAIN or WT_3M_FORCE_OPEN)
  - ez_manage.py:14089-14107 (augmentation_cooldown_map / recent_augmentations gates)

PURPOSE:
  Give backtest_v8_engine identical-result parity with live trade cooldowns. In live
  trading, two reduces inside 15s = the second is BLOCKED_HARD_REDUCE_LOCK_<n>s, and
  an augment within 900s of the last augment is BLOCKED_HARD_AUGMENT_LOCK_<n>s
  (unless gain >= MIN_GAIN). Without parity here the engine over-trades and inflates
  Sharpe vs live.

USER CONTRACT (CLAUDE.md / memory):
  - REENTRY on positionAmt==0 BYPASSES these gates (per-symbol force-open mandate).
  - HEDGE entries with is_hedge=True BYPASS these gates (hedge engine has its own
    HEDGE_COMPLETED_LOCKOUT path).
  - Urgent reasons (EMERGENCY/KILL/HEDGE_FAILED/BOYCOTT/RIDICULOUS_HOLD/...) BYPASS.
  - WT_3M_FORCE_OPEN in reason BYPASSES augment lock when WT_3M_FORCE_OPEN_BYPASS_GATES.

OUTPUT CONTRACT:
  evaluate_cooldown_locks_core(...) -> (blocked: bool, reason: str, gate_fired: str)
  evaluate_cooldown_locks_vec(...)  -> (blocked: np.bool_[N], reason_codes: np.int8[N])

REASON CODES (string + numeric):
    GATE_OK                      / CODE_OK = 0
    BLOCKED_HARD_REDUCE_LOCK     / CODE_REDUCE_LOCK = 1
    BLOCKED_HARD_AUGMENT_LOCK    / CODE_AUGMENT_LOCK = 2
    BLOCKED_AUGMENTATION_COOLDOWN_MAP / CODE_AUG_CD_MAP = 3
    BLOCKED_RECENT_AUGMENTATIONS / CODE_RECENT_AUG = 4
    BLOCKED_LAST_AUGMENT_SAVE    / CODE_LAST_AUG_SAVE = 5
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np


# ─── Reason-code constants (mirror live BLOCKED_* strings) ────────────────────
GATE_OK = "GATE_OK"
GATE_REDUCE_LOCK = "HARD_REDUCE_LOCK"
GATE_AUGMENT_LOCK = "HARD_AUGMENT_LOCK"
GATE_AUG_CD_MAP = "AUGMENTATION_COOLDOWN_MAP"
GATE_RECENT_AUG = "RECENT_AUGMENTATIONS"
GATE_LAST_AUG_SAVE = "LAST_AUGMENT_SAVE"

# Numeric codes for vectorized output
CODE_OK = np.int8(0)
CODE_REDUCE_LOCK = np.int8(1)
CODE_AUGMENT_LOCK = np.int8(2)
CODE_AUG_CD_MAP = np.int8(3)
CODE_RECENT_AUG = np.int8(4)
CODE_LAST_AUG_SAVE = np.int8(5)

GATE_BY_CODE: Dict[int, str] = {
    int(CODE_OK): GATE_OK,
    int(CODE_REDUCE_LOCK): GATE_REDUCE_LOCK,
    int(CODE_AUGMENT_LOCK): GATE_AUGMENT_LOCK,
    int(CODE_AUG_CD_MAP): GATE_AUG_CD_MAP,
    int(CODE_RECENT_AUG): GATE_RECENT_AUG,
    int(CODE_LAST_AUG_SAVE): GATE_LAST_AUG_SAVE,
}

# ─── Default thresholds (match live constants at module top of ez_manage) ─────
DEFAULT_HARD_REDUCE_LOCK_SECONDS = 15.0       # ez_manage._DUPLICATE_REDUCE_COOLDOWN
DEFAULT_HARD_AUGMENT_LOCK_SECONDS = 900.0     # ez_manage._AUGMENT_LOCK_MIN_SECONDS
DEFAULT_AUGMENTATION_COOLDOWN_SECONDS = 480.0 # config.AUGMENTATION_COOLDOWN_SECONDS
DEFAULT_MIN_GAIN = 1.2                        # config.MIN_GAIN (used as gain bypass)
DEFAULT_WT_3M_FORCE_OPEN_BYPASS = True        # config.WT_3M_FORCE_OPEN_BYPASS_GATES

# ─── Action classification (mirror ez_manage:14017, 14040, 14048) ─────────────
REDUCE_ACTIONS = frozenset({
    "CLOSE", "REDUCE", "SELL", "QUICK_CLOSE", "FULL_CLOSE", "PROFIT_TAKE",
    "STOP_MAJOR_LOSS_REDUCE", "STOP_FUNCTIONS_KILL", "HEDGE_CLOSE",
})
AUGMENT_ACTIONS = frozenset({
    "OPEN", "AUGMENT", "REENTRY", "REVERSE", "REVERSE_AUGMENT",
    "QUICK_OPEN", "QUICK_AUGMENT", "QUICK_HEDGE_OPEN", "QUICK_HEDGE_AUGMENT",
    "HEDGE_OPEN",
})

# ─── Urgent reason fragments (substring match, case-insensitive) ─────────────
# These bypass the cooldown gates entirely. Sources:
#   ez_manage.py:14024-14033 (V3 urgent close + DC_BB_D_BREAK + RIDICULOUS_*)
#   USER CONTRACT: EMERGENCY/KILL/HEDGE_FAILED/BOYCOTT are emergency-class.
URGENT_REASON_FRAGMENTS_REDUCE = (
    "SCALP_V3_OPEN_MAX_LOSS_CUT",
    "SCALP_V3_OPEN_PROTECTIVE_EXIT",
    "SCALP_V3_OPEN_BE_STOP_AUG",
    "DC_BB_D_BREAK_REVERSE",
    "RIDICULOUS_HOLD",
    "RIDICULOUS_LOSS",
    "UNDERWATER_HEDGE_OR_CLOSE",
    "WT15M_AGAINST",
    "ALL_TF_AGAINST",
    "EMERGENCY",
    "KILL",
    "HEDGE_FAILED",
    "BOYCOTT",
)
URGENT_REASON_FRAGMENTS_AUGMENT = (
    "WT_3M_FORCE_OPEN",
    "EMERGENCY",
    "KILL",
    "HEDGE_FAILED",
    "BOYCOTT",
)


# ════════════════════════════════════════════════════════════════════════════════
# Config helper
# ════════════════════════════════════════════════════════════════════════════════

def _cfg_get(cfg: Any, name: str, default: Any) -> Any:
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _action_class(action: str) -> str:
    """Classify action into 'reduce' | 'augment' | 'unknown'."""
    a = (action or "").upper()
    if a in REDUCE_ACTIONS:
        return "reduce"
    if a in AUGMENT_ACTIONS:
        return "augment"
    return "unknown"


def _reason_has_urgent(reason: str, fragments: Tuple[str, ...]) -> bool:
    if not reason:
        return False
    r = reason.upper()
    return any(frag in r for frag in fragments)


# ════════════════════════════════════════════════════════════════════════════════
# SCALAR core
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_cooldown_locks_core(
    action: str,
    now_ts: float,
    last_reduce_ts: float,
    last_augment_ts: float,
    augmentation_cooldown_ts: Optional[float] = None,
    recent_augmentation_ts: Optional[float] = None,
    last_augment_save_ts: Optional[float] = None,
    reason: str = "",
    position_amt: float = 0.0,
    is_hedge: bool = False,
    current_gain_pct: float = 0.0,
    has_existing_position: Optional[bool] = None,
    cfg: Any = None,
) -> Tuple[bool, str, str]:
    """Scalar parity for HARD_REDUCE_LOCK + HARD_AUGMENT_LOCK + AUGMENTATION_COOLDOWN_MAP.

    Args:
        action: REDUCE/CLOSE/AUGMENT/OPEN/REENTRY/HEDGE_OPEN etc.
        now_ts: Current epoch seconds.
        last_reduce_ts: Epoch seconds of the most recent reduce on this position_key (0 if none).
        last_augment_ts: Epoch seconds of the most recent augment on this position_key (0 if none).
        augmentation_cooldown_ts: Epoch sec set into augmentation_cooldown_map (None if not in map).
        recent_augmentation_ts: Epoch sec set into recent_augmentations (None if not in map).
        last_augment_save_ts: Epoch sec set into _last_augment_save_time (None if not in map).
        reason: The execute_now reason string (may carry urgent / WT_3M_FORCE_OPEN).
        position_amt: Current absolute position size (used to derive has_existing_position).
        is_hedge: True if this is a hedge entry — bypasses augment lock per user mandate.
        current_gain_pct: Current realized+unrealized gain percent (0 if no position).
        has_existing_position: Override for the size check. If None, derived from position_amt > 0.
        cfg: Optional config object/dict for HARD_REDUCE_LOCK_SECONDS / HARD_AUGMENT_LOCK_SECONDS /
             AUGMENTATION_COOLDOWN_SECONDS / MIN_GAIN / WT_3M_FORCE_OPEN_BYPASS_GATES.

    Returns:
        (blocked, reason, gate_fired)
            blocked     — True if action should be blocked.
            reason      — BLOCKED_HARD_REDUCE_LOCK_<n>s style string mirroring live.
            gate_fired  — Canonical gate identifier (GATE_REDUCE_LOCK / GATE_OK / ...).
    """
    cls = _action_class(action)
    if cls == "unknown":
        # Mirrors ez_manage:14041 UNRECOGNIZED_ACTION_BLOCK (handled elsewhere; we pass).
        return False, GATE_OK, GATE_OK

    # ─── HEDGE / REENTRY-on-empty bypass per user contract ──────────────────────
    # REENTRY/HEDGE bypass applies ONLY to augment-class actions. A REDUCE still
    # has to honour HARD_REDUCE_LOCK regardless of is_hedge.
    if cls == "augment":
        if is_hedge:
            return False, GATE_OK, GATE_OK
        if (action or "").upper() == "REENTRY" and abs(position_amt) <= 0.0:
            # True REENTRY on empty position — bypass cooldowns.
            return False, GATE_OK, GATE_OK
        if _reason_has_urgent(reason, URGENT_REASON_FRAGMENTS_AUGMENT):
            wt3m_bypass_cfg = bool(_cfg_get(cfg, "WT_3M_FORCE_OPEN_BYPASS_GATES", DEFAULT_WT_3M_FORCE_OPEN_BYPASS))
            # WT_3M_FORCE_OPEN respects WT_3M_FORCE_OPEN_BYPASS_GATES; other urgent fragments
            # (EMERGENCY/KILL/HEDGE_FAILED/BOYCOTT) always bypass.
            r = (reason or "").upper()
            if "WT_3M_FORCE_OPEN" in r and not wt3m_bypass_cfg:
                pass  # gate stays engaged
            else:
                return False, GATE_OK, GATE_OK

    # ─── HARD_REDUCE_LOCK (mirrors ez_manage:14018-14037) ──────────────────────
    if cls == "reduce":
        lock_secs = float(_cfg_get(cfg, "HARD_REDUCE_LOCK_SECONDS", DEFAULT_HARD_REDUCE_LOCK_SECONDS))
        if _reason_has_urgent(reason, URGENT_REASON_FRAGMENTS_REDUCE):
            return False, GATE_OK, GATE_OK
        since_red = now_ts - float(last_reduce_ts or 0)
        if last_reduce_ts and since_red < lock_secs:
            return (
                True,
                f"BLOCKED_HARD_REDUCE_LOCK_{since_red:.0f}s",
                GATE_REDUCE_LOCK,
            )
        return False, GATE_OK, GATE_OK

    # ─── AUGMENT-class gates ──────────────────────────────────────────────────
    # Derive has_existing_position when not supplied.
    if has_existing_position is None:
        has_existing_position = abs(position_amt) > 0.0

    min_gain = float(_cfg_get(cfg, "MIN_GAIN", DEFAULT_MIN_GAIN))
    gain_ok = (current_gain_pct >= min_gain) if has_existing_position else False
    aug_lock_secs = float(_cfg_get(cfg, "HARD_AUGMENT_LOCK_SECONDS", DEFAULT_HARD_AUGMENT_LOCK_SECONDS))
    aug_cd_secs = float(_cfg_get(cfg, "AUGMENTATION_COOLDOWN_SECONDS", DEFAULT_AUGMENTATION_COOLDOWN_SECONDS))

    # HARD_AUGMENT_LOCK: applies on TRUE OPEN as well (per ez_manage:14067-14068).
    since_aug = now_ts - float(last_augment_ts or 0)
    if last_augment_ts and since_aug < aug_lock_secs and not gain_ok:
        return (
            True,
            f"BLOCKED_HARD_AUGMENT_LOCK_{since_aug:.0f}s",
            GATE_AUGMENT_LOCK,
        )

    # augmentation_cooldown_map — only fires when position exists AND gain not OK.
    if (
        has_existing_position
        and not gain_ok
        and augmentation_cooldown_ts is not None
    ):
        cd_age = now_ts - float(augmentation_cooldown_ts)
        if cd_age < aug_cd_secs:
            return (
                True,
                f"BLOCKED_AUGMENTATION_COOLDOWN_MAP_{cd_age:.0f}s",
                GATE_AUG_CD_MAP,
            )

    # recent_augmentations — same gating as augmentation_cooldown_map but uses
    # the 900s _AUGMENT_LOCK_MIN_SECONDS threshold (ez_manage:14104-14107).
    if (
        has_existing_position
        and not gain_ok
        and recent_augmentation_ts is not None
    ):
        ra_age = now_ts - float(recent_augmentation_ts)
        if ra_age < aug_lock_secs:
            return (
                True,
                f"BLOCKED_RECENT_AUGMENTATIONS_{ra_age:.0f}s",
                GATE_RECENT_AUG,
            )

    # _last_augment_save_time (ez_manage:14110-14113) — same 900s threshold.
    if (
        has_existing_position
        and not gain_ok
        and last_augment_save_ts is not None
    ):
        las_age = now_ts - float(last_augment_save_ts)
        if las_age < aug_lock_secs:
            return (
                True,
                f"BLOCKED_LAST_AUGMENT_SAVE_{las_age:.0f}s",
                GATE_LAST_AUG_SAVE,
            )

    return False, GATE_OK, GATE_OK


# ════════════════════════════════════════════════════════════════════════════════
# VECTORIZED — N candidate decisions at once
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_cooldown_locks_vec(
    actions: np.ndarray,                      # object/str, shape (N,)
    now_ts: np.ndarray,                       # float64, shape (N,)
    last_reduce_ts: np.ndarray,               # float64, shape (N,)  (0 if none)
    last_augment_ts: np.ndarray,              # float64, shape (N,)  (0 if none)
    augmentation_cooldown_ts: np.ndarray,     # float64, shape (N,)  (NaN if absent)
    recent_augmentation_ts: np.ndarray,       # float64, shape (N,)  (NaN if absent)
    last_augment_save_ts: np.ndarray,         # float64, shape (N,)  (NaN if absent)
    reasons: np.ndarray,                      # object/str, shape (N,)
    position_amts: np.ndarray,                # float64, shape (N,)
    is_hedge: np.ndarray,                     # bool, shape (N,)
    current_gain_pcts: np.ndarray,            # float64, shape (N,)
    cfg: Any = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized parity for evaluate_cooldown_locks_core.

    Returns:
        (blocked, reason_codes)
            blocked:      np.bool_  shape (N,)
            reason_codes: np.int8   shape (N,)  values CODE_OK/CODE_REDUCE_LOCK/...
    """
    n = len(actions)
    if n == 0:
        return np.zeros(0, dtype=bool), np.zeros(0, dtype=np.int8)

    reduce_lock_secs = float(_cfg_get(cfg, "HARD_REDUCE_LOCK_SECONDS", DEFAULT_HARD_REDUCE_LOCK_SECONDS))
    aug_lock_secs = float(_cfg_get(cfg, "HARD_AUGMENT_LOCK_SECONDS", DEFAULT_HARD_AUGMENT_LOCK_SECONDS))
    aug_cd_secs = float(_cfg_get(cfg, "AUGMENTATION_COOLDOWN_SECONDS", DEFAULT_AUGMENTATION_COOLDOWN_SECONDS))
    min_gain = float(_cfg_get(cfg, "MIN_GAIN", DEFAULT_MIN_GAIN))
    wt3m_bypass_cfg = bool(_cfg_get(cfg, "WT_3M_FORCE_OPEN_BYPASS_GATES", DEFAULT_WT_3M_FORCE_OPEN_BYPASS))

    # Action classification masks
    upper = np.array([(a or "").upper() for a in actions], dtype=object)
    is_reduce = np.array([u in REDUCE_ACTIONS for u in upper], dtype=bool)
    is_augment = np.array([u in AUGMENT_ACTIONS for u in upper], dtype=bool)

    # Urgent-reason masks
    reasons_up = np.array([(r or "").upper() for r in reasons], dtype=object)
    urgent_reduce = np.array(
        [any(f in r for f in URGENT_REASON_FRAGMENTS_REDUCE) for r in reasons_up],
        dtype=bool,
    )

    def _aug_bypass(r: str) -> bool:
        if not r:
            return False
        emerg = ("EMERGENCY" in r) or ("KILL" in r) or ("HEDGE_FAILED" in r) or ("BOYCOTT" in r)
        wt3m = ("WT_3M_FORCE_OPEN" in r) and wt3m_bypass_cfg
        return emerg or wt3m

    urgent_augment = np.array([_aug_bypass(r) for r in reasons_up], dtype=bool)

    # Reentry-on-empty bypass
    reentry_empty = (upper == "REENTRY") & (np.abs(position_amts) <= 0.0)

    aug_bypass_mask = is_augment & (is_hedge | urgent_augment | reentry_empty)

    # Output buffers
    blocked = np.zeros(n, dtype=bool)
    codes = np.zeros(n, dtype=np.int8)

    # ─── HARD_REDUCE_LOCK ─────────────────────────────────────────────────────
    since_red = now_ts - last_reduce_ts
    reduce_blocked = (
        is_reduce
        & (~urgent_reduce)
        & (last_reduce_ts > 0)
        & (since_red < reduce_lock_secs)
    )
    blocked |= reduce_blocked
    codes = np.where(reduce_blocked, CODE_REDUCE_LOCK, codes)

    # ─── AUGMENT-class gates (skip when bypass) ───────────────────────────────
    aug_active = is_augment & ~aug_bypass_mask
    has_existing = np.abs(position_amts) > 0.0
    gain_ok = has_existing & (current_gain_pcts >= min_gain)

    # HARD_AUGMENT_LOCK
    since_aug = now_ts - last_augment_ts
    aug_lock_block = (
        aug_active
        & (last_augment_ts > 0)
        & (since_aug < aug_lock_secs)
        & (~gain_ok)
        & (~blocked)
    )
    blocked |= aug_lock_block
    codes = np.where(aug_lock_block, CODE_AUGMENT_LOCK, codes)

    # augmentation_cooldown_map
    cd_age = now_ts - augmentation_cooldown_ts
    cd_map_block = (
        aug_active
        & has_existing
        & (~gain_ok)
        & np.isfinite(augmentation_cooldown_ts)
        & (cd_age < aug_cd_secs)
        & (~blocked)
    )
    blocked |= cd_map_block
    codes = np.where(cd_map_block, CODE_AUG_CD_MAP, codes)

    # recent_augmentations
    ra_age = now_ts - recent_augmentation_ts
    ra_block = (
        aug_active
        & has_existing
        & (~gain_ok)
        & np.isfinite(recent_augmentation_ts)
        & (ra_age < aug_lock_secs)
        & (~blocked)
    )
    blocked |= ra_block
    codes = np.where(ra_block, CODE_RECENT_AUG, codes)

    # _last_augment_save_time
    las_age = now_ts - last_augment_save_ts
    las_block = (
        aug_active
        & has_existing
        & (~gain_ok)
        & np.isfinite(last_augment_save_ts)
        & (las_age < aug_lock_secs)
        & (~blocked)
    )
    blocked |= las_block
    codes = np.where(las_block, CODE_LAST_AUG_SAVE, codes)

    return blocked, codes


# ════════════════════════════════════════════════════════════════════════════════
# Helper: CooldownState — mutable per-position-key state for engine loop
# ════════════════════════════════════════════════════════════════════════════════

class CooldownState:
    """Mutable state container for backtest_v8_engine to thread per-position cooldowns.

    Engine usage:
        state = CooldownState()
        ...
        # before placing an order
        blocked, reason, gate = evaluate_cooldown_locks_core(
            action, now_ts,
            last_reduce_ts=state.last_reduce.get(pk, 0),
            last_augment_ts=state.last_augment.get(pk, 0),
            augmentation_cooldown_ts=state.aug_cd_map.get(pk),
            ...
        )
        if blocked:
            return reason
        # after a successful reduce/augment
        state.record(pk, action, now_ts)
    """

    def __init__(self) -> None:
        self.last_reduce: Dict[str, float] = {}
        self.last_augment: Dict[str, float] = {}
        self.aug_cd_map: Dict[str, float] = {}
        self.recent_aug: Dict[str, float] = {}
        self.last_aug_save: Dict[str, float] = {}

    def record(self, position_key: str, action: str, now_ts: float) -> None:
        cls = _action_class(action)
        if cls == "reduce":
            self.last_reduce[position_key] = now_ts
        elif cls == "augment":
            self.last_augment[position_key] = now_ts
            self.recent_aug[position_key] = now_ts
            self.last_aug_save[position_key] = now_ts

    def record_cooldown_map(self, position_key: str, now_ts: float) -> None:
        """Mirror the live `augmentation_cooldown_map[pk] = {'time': now}` write site
        (used when a position gets a 'cooldown stamp' from sweep / regime checks)."""
        self.aug_cd_map[position_key] = now_ts
