"""NEWBORN_PROTECT — 15-min grace period blocking closes on freshly opened positions.

LIVE LOGIC MIRRORED FROM: ez_manage.py:14210-14264 (NEWBORN_PROTECT block).

WHY THIS EXISTS:
    Just-opened positions are noise-vulnerable. A WT/MTF reversal in the first
    few minutes after entry is almost always a wick, not a real flip. We block
    CLOSE/REDUCE for the first NEWBORN_PROTECT_GRACE_SECONDS (default 900s = 15min)
    UNLESS:
      - price has broken the dc_3m channel (LONG: price ≤ dc_low_3m;
        SHORT: price ≥ dc_high_3m) — structural break = real signal
      - the close reason is in the urgent / emergency bypass set (R1 emergency,
        HEDGE_FAILED, LIQUIDATION, etc.) — caller has higher authority.

LIVE PRECEDENCE (ez_manage.py:14213-14262):
    is_reduce AND NOT is_hedge → check age vs grace
    age >= grace → ALLOW
    age < grace:
        dc_3m broken → ALLOW (logs NEWBORN_DC_BREAK)
        emergency reason → ALLOW (logs NEWBORN_EMERGENCY_BYPASS)
        else → BLOCK with "BLOCKED_NEWBORN_PROTECT_{age}s"

EMERGENCY BYPASS REASONS (USER MANDATE — match live + add explicit per-spec):
    From live ez_manage.py:14249-14254:
        DC_BB_D_BREAK_REVERSE, RIDICULOUS_HOLD, RIDICULOUS_LOSS,
        UNDERWATER_HEDGE_OR_CLOSE, WT15M_AGAINST, ALL_TF_AGAINST
    Plus per-spec contract (USER 2026-05-12):
        R1_DC_LOW4_3M_EMERGENCY, HEDGE_FAILED, LIQUIDATION

REASON CODES returned to the caller:
    "OK"                                 — not blocked
    "DISABLED"                           — feature off via config
    "NOT_REDUCE"                         — non-close action (never blocks)
    "IS_HEDGE_BYPASS"                    — hedge close bypass (live semantics)
    "AGE_EXCEEDED_GRACE"                 — old enough, no block
    "DC_BREACH_BYPASS"                   — DC3m structure broken, allow
    "EMERGENCY_REASON_BYPASS"            — urgent reason in bypass set
    "BLOCKED_NEWBORN_PROTECT_{age}s"     — fired; carry age in string
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Tuple

import numpy as np


# ─── Reason-code constants ─────────────────────────────────────────────────────
REASON_OK = "OK"
REASON_DISABLED = "DISABLED"
REASON_NOT_REDUCE = "NOT_REDUCE"
REASON_IS_HEDGE_BYPASS = "IS_HEDGE_BYPASS"
REASON_AGE_OK = "AGE_EXCEEDED_GRACE"
REASON_DC_BREACH = "DC_BREACH_BYPASS"
REASON_EMERGENCY = "EMERGENCY_REASON_BYPASS"
REASON_BLOCKED_PREFIX = "BLOCKED_NEWBORN_PROTECT_"

# Numeric codes for the _vec output (np.int8 array)
CODE_OK = 0
CODE_DISABLED = 1
CODE_NOT_REDUCE = 2
CODE_IS_HEDGE_BYPASS = 3
CODE_AGE_OK = 4
CODE_DC_BREACH = 5
CODE_EMERGENCY = 6
CODE_BLOCKED = 7

REASON_BY_CODE = {
    CODE_OK: REASON_OK,
    CODE_DISABLED: REASON_DISABLED,
    CODE_NOT_REDUCE: REASON_NOT_REDUCE,
    CODE_IS_HEDGE_BYPASS: REASON_IS_HEDGE_BYPASS,
    CODE_AGE_OK: REASON_AGE_OK,
    CODE_DC_BREACH: REASON_DC_BREACH,
    CODE_EMERGENCY: REASON_EMERGENCY,
    CODE_BLOCKED: REASON_BLOCKED_PREFIX + "{age}s",  # template
}

# ─── Default thresholds ────────────────────────────────────────────────────────
DEFAULT_ENABLED = True
DEFAULT_GRACE_SECONDS = 900  # 15 minutes (matches live hardcoded `< 900` on L14230)

# Reduce-class actions (match live ez_manage.py:14204)
REDUCE_ACTIONS = frozenset({
    "CLOSE", "REDUCE", "QUICK_CLOSE", "FULL_CLOSE", "PROFIT_TAKE",
    "STOP_MAJOR_LOSS_REDUCE", "STOP_FUNCTIONS_KILL", "HEDGE_CLOSE",
})

# Emergency reasons that bypass newborn protect.
# Order: live ez_manage.py:14249-14254 + USER 2026-05-12 contract additions.
EMERGENCY_REASONS = (
    "R1_DC_LOW4_3M_EMERGENCY",       # USER contract
    "HEDGE_FAILED",                  # USER contract
    "LIQUIDATION",                   # USER contract
    "DC_BB_D_BREAK_REVERSE",         # live
    "RIDICULOUS_HOLD",               # live
    "RIDICULOUS_LOSS",               # live
    "UNDERWATER_HEDGE_OR_CLOSE",     # live
    "WT15M_AGAINST",                 # live
    "ALL_TF_AGAINST",                # live
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


def _resolve_knobs(cfg: Any) -> Tuple[bool, float]:
    enabled = bool(_cfg_get(cfg, "NEWBORN_PROTECT_ENABLED", DEFAULT_ENABLED))
    grace_s = float(_cfg_get(cfg, "NEWBORN_PROTECT_GRACE_SECONDS", DEFAULT_GRACE_SECONDS))
    return enabled, grace_s


def _is_reduce_action(action: str, reason: str) -> bool:
    """Mirror ez_manage.py:14204 — action in REDUCE set OR reason contains CLOSE/REDUCE."""
    act_upper = (action or "").upper()
    reason_upper = (reason or "").upper()
    return (
        act_upper in REDUCE_ACTIONS
        or "CLOSE" in reason_upper
        or "REDUCE" in reason_upper
    )


def _is_emergency_reason(reason: str) -> bool:
    if not reason:
        return False
    r = reason.upper()
    return any(tag in r for tag in EMERGENCY_REASONS)


def _to_epoch(ts: Any) -> float:
    """Accept float epoch, datetime, or ISO string. Returns 0 on failure."""
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc).timestamp()
        return ts.timestamp()
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


# ════════════════════════════════════════════════════════════════════════════════
# _core — single-call decision (scalar)
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_newborn_protect_core(
    action: str,
    position_opened_at: Any,
    now_ts: Any,
    mark_price: float,
    dc_low_3m: float,
    dc_high_3m: float,
    is_long: bool,
    *,
    reason: str = "",
    is_hedge: bool = False,
    config: Any = None,
) -> Tuple[bool, str, float, bool]:
    """Decide whether a CLOSE/REDUCE is blocked by NEWBORN_PROTECT.

    Args:
        action:             prospective action (CLOSE/REDUCE/OPEN/...).
        position_opened_at: opened_at or last_augmentation_time. epoch float,
                            datetime, or ISO string.
        now_ts:             current time (epoch float / datetime / iso).
        mark_price:         current price (`old_price` in live).
        dc_low_3m:          dc_low_3m indicator value.
        dc_high_3m:         dc_high_3m indicator value.
        is_long:            True if position side is LONG.
        reason:             reduce reason string (urgent reasons bypass).
        is_hedge:           if True, NEWBORN_PROTECT is skipped (live L14213).
        config:             module / dict / None.

    Returns:
        (blocked, reason_code, age_s, dc_breach)
    """
    enabled, grace_s = _resolve_knobs(config)
    if not enabled:
        return (False, REASON_DISABLED, 0.0, False)

    if is_hedge:
        return (False, REASON_IS_HEDGE_BYPASS, 0.0, False)

    if not _is_reduce_action(action, reason):
        return (False, REASON_NOT_REDUCE, 0.0, False)

    now_epoch = _to_epoch(now_ts)
    ref_epoch = _to_epoch(position_opened_at)
    age_s = now_epoch - ref_epoch if ref_epoch > 0 else 999_999.0

    if age_s >= grace_s:
        return (False, REASON_AGE_OK, age_s, False)

    # Within grace — check DC structure break
    dc_breach = False
    try:
        dc_low = float(dc_low_3m) if dc_low_3m is not None else 0.0
        dc_high = float(dc_high_3m) if dc_high_3m is not None else 0.0
        px = float(mark_price) if mark_price is not None else 0.0
        if is_long and dc_low > 0 and px > 0 and px <= dc_low:
            dc_breach = True
        elif not is_long and dc_high > 0 and px > 0 and px >= dc_high:
            dc_breach = True
    except (TypeError, ValueError):
        dc_breach = False

    if dc_breach:
        return (False, REASON_DC_BREACH, age_s, True)

    if _is_emergency_reason(reason):
        return (False, REASON_EMERGENCY, age_s, False)

    return (True, f"{REASON_BLOCKED_PREFIX}{age_s:.0f}s", age_s, False)


# ════════════════════════════════════════════════════════════════════════════════
# _vec — vectorized batch evaluation
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_newborn_protect_vec(
    actions: Iterable[str],
    opened_at_epoch: np.ndarray,
    now_epoch: np.ndarray,
    mark_price: np.ndarray,
    dc_low_3m: np.ndarray,
    dc_high_3m: np.ndarray,
    is_long: np.ndarray,
    *,
    reasons: Optional[Iterable[str]] = None,
    is_hedge: Optional[np.ndarray] = None,
    config: Any = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized version. Returns (blocked, reason_code, age_s, dc_breach).

    Arrays:
        opened_at_epoch, now_epoch, mark_price, dc_low_3m, dc_high_3m: float64.
        is_long: bool.
        is_hedge: bool, optional (defaults to all False).
        actions, reasons: iterables of strings (object arrays accepted).

    All arrays must be same length N. Returns 4 arrays of length N.
    """
    enabled, grace_s = _resolve_knobs(config)
    actions_arr = np.asarray([(a or "").upper() for a in actions], dtype=object)
    n = actions_arr.shape[0]

    if reasons is None:
        reasons_arr = np.array([""] * n, dtype=object)
    else:
        reasons_arr = np.asarray([(r or "").upper() for r in reasons], dtype=object)

    if is_hedge is None:
        hedge_mask = np.zeros(n, dtype=bool)
    else:
        hedge_mask = np.asarray(is_hedge, dtype=bool)

    opened_at = np.asarray(opened_at_epoch, dtype=np.float64)
    now_arr = np.asarray(now_epoch, dtype=np.float64)
    px = np.asarray(mark_price, dtype=np.float64)
    dcl = np.asarray(dc_low_3m, dtype=np.float64)
    dch = np.asarray(dc_high_3m, dtype=np.float64)
    long_mask = np.asarray(is_long, dtype=bool)

    blocked = np.zeros(n, dtype=bool)
    rc = np.full(n, CODE_OK, dtype=np.int8)
    age_s = np.where(opened_at > 0, now_arr - opened_at, 999_999.0).astype(np.float64)
    dc_breach = np.zeros(n, dtype=bool)

    if not enabled:
        rc[:] = CODE_DISABLED
        age_s[:] = 0.0
        return blocked, rc, age_s, dc_breach

    # Per-row reduce action check (vectorized through object loop — actions are strings)
    is_reduce = np.array([
        (a in REDUCE_ACTIONS) or ("CLOSE" in r) or ("REDUCE" in r)
        for a, r in zip(actions_arr, reasons_arr)
    ], dtype=bool)

    # Hedge bypass (highest precedence after enabled-check — matches live L14213)
    rc[hedge_mask] = CODE_IS_HEDGE_BYPASS
    age_s[hedge_mask] = 0.0
    # Then non-reduce
    nr_mask = (~hedge_mask) & (~is_reduce)
    rc[nr_mask] = CODE_NOT_REDUCE
    age_s[nr_mask] = 0.0

    # Eligible = reduce AND not hedge
    elig = (~hedge_mask) & is_reduce
    if not elig.any():
        return blocked, rc, age_s, dc_breach

    age_ok = elig & (age_s >= grace_s)
    rc[age_ok] = CODE_AGE_OK

    # Within grace window
    in_grace = elig & (age_s < grace_s)

    # DC breach (LONG: px<=dc_low_3m>0; SHORT: px>=dc_high_3m>0)
    long_breach = in_grace & long_mask & (dcl > 0) & (px > 0) & (px <= dcl)
    short_breach = in_grace & (~long_mask) & (dch > 0) & (px > 0) & (px >= dch)
    dc_breach = long_breach | short_breach
    rc[dc_breach] = CODE_DC_BREACH

    # Emergency-reason bypass (only rows still in grace AND not dc-breached)
    needs_emerg = in_grace & (~dc_breach)
    emerg_mask = np.array([
        any(tag in r for tag in EMERGENCY_REASONS) if needs_emerg[i] else False
        for i, r in enumerate(reasons_arr)
    ], dtype=bool)
    rc[emerg_mask & needs_emerg] = CODE_EMERGENCY

    # Blocked = in grace AND not dc-breached AND not emergency
    block_mask = needs_emerg & (~emerg_mask)
    blocked[block_mask] = True
    rc[block_mask] = CODE_BLOCKED

    return blocked, rc, age_s, dc_breach


# ════════════════════════════════════════════════════════════════════════════════
# Pretty-print reason from numeric code (round-trips _vec output)
# ════════════════════════════════════════════════════════════════════════════════

def reason_from_code(code: int, age_s: float = 0.0) -> str:
    if code == CODE_BLOCKED:
        return f"{REASON_BLOCKED_PREFIX}{age_s:.0f}s"
    return REASON_BY_CODE.get(int(code), REASON_OK)
