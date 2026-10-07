"""QUARANTINE_BLOCK + VEC_STRATEGY_GATES wireup validation — scalar + vec.

This module mirrors two live execute_now() gates so that vectorized backtest
engines can produce live-parity entry/close decisions WITHOUT reimplementing
the live logic.

────────────────────────────────────────────────────────────────────────────
PART 1 — QUARANTINE_BLOCK  (mirrors ez_manage.py:14169-14183)
────────────────────────────────────────────────────────────────────────────
LIVE BEHAVIOR (verbatim from ez_manage.py):
    _reason_key = (reason or '').strip()
    with open(Path(BASE_PATH)/'data'/'function_quarantine.json') as f:
        _quarantine = json.load(f)               # list[str] of function/reason names
    for _qname in _quarantine:
        if _qname in _reason_key:                # substring match on REASON
            return f"BLOCKED_QUARANTINED({_qname})"
    # FileNotFoundError or any error → silently pass (fail-open)

USER CONTRACT (per task spec — codifies what live SHOULD do but doesn't have
inline in this block; the upstream gates handle these cases but for vec/backtest
parity we honor them here to avoid double-rejection):
    - REENTRY when positionAmt == 0   → BYPASS quarantine (this is a recovery open)
    - HEDGE action (is_hedge=True)    → BYPASS quarantine (hedge is protective)
    - QUARANTINE_ENFORCE_ENABLED=False→ short-circuit DISABLED

The bypasses are config-gated (default ON to mirror user contract). Set
QUARANTINE_BYPASS_REENTRY_ZERO_POS=False or QUARANTINE_BYPASS_HEDGE=False to
make the vec module match live's stricter behavior (live has no inline bypass;
upstream paths normally don't even reach this block for hedge/reentry-zero
because is_hedge bypass tradeable gate, etc).

────────────────────────────────────────────────────────────────────────────
PART 2 — VEC_STRATEGY_GATES parity smoke
────────────────────────────────────────────────────────────────────────────
The existing `vec_strategy_gates.py` (top-level repo) is read by live at
ez_manage.py:13485 (`from vec_strategy_gates import check_vec_gate,
shadow_log_evaluation`). The live block at 13467-13526 wraps that import with:
    - entry-action gating (OPEN/AUGMENT/ENTRY, not CLOSE/REDUCE/HEDGE/full_close)
    - account/symbol resolution from position_key
    - shadow-log to data/vec_gates_shadow_log.jsonl
    - enforcement only when VEC_GATES_LOG_ONLY=False (default True)

This module's `evaluate_vec_strategy_gates_parity_core/_vec` re-runs the SAME
top-level vec_strategy_gates.check_vec_gate under the same action-classification
and bypass rules, so a vec engine that calls this gets the SAME (fires, reason)
the live path would have used. NO new gate logic — pure wrapper for parity.

REASON CODES (match live BLOCKED_* strings where applicable):
    "OK"                              — gate passed / action not subject
    "DISABLED"                        — config flag off
    "QUARANTINED({name})"             — reason matched quarantine list
    "BYPASS_REENTRY_ZERO_POS"         — reentry on flat position bypassed
    "BYPASS_HEDGE"                    — hedge action bypassed
    "VEC_GATE_BLOCK:{reason}"         — VEC_STRATEGY_GATES enforced and refused
    "VEC_GATE_ALLOW:{reason}"         — VEC_STRATEGY_GATES enforced and allowed
    "VEC_GATE_SHADOW_ONLY"            — LOG_ONLY mode; no enforcement
    "VEC_GATE_NOT_ENTRY"              — action wasn't an entry; skipped
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


# ─── Reason-code constants ─────────────────────────────────────────────────────
REASON_OK = "OK"
REASON_DISABLED = "DISABLED"
REASON_BYPASS_REENTRY_ZERO_POS = "BYPASS_REENTRY_ZERO_POS"
REASON_BYPASS_HEDGE = "BYPASS_HEDGE"

# Numeric codes for the _vec output (np.int8)
CODE_OK = 0
CODE_DISABLED = 1
CODE_QUARANTINED = 2
CODE_BYPASS_REENTRY = 3
CODE_BYPASS_HEDGE = 4

CODE_VEC_OK = 0
CODE_VEC_DISABLED = 1
CODE_VEC_NOT_ENTRY = 2
CODE_VEC_SHADOW_ONLY = 3
CODE_VEC_ALLOW = 4
CODE_VEC_BLOCK = 5
CODE_VEC_BYPASS_HEDGE = 6

REASON_BY_QCODE: Dict[int, str] = {
    CODE_OK: REASON_OK,
    CODE_DISABLED: REASON_DISABLED,
    CODE_QUARANTINED: "QUARANTINED",
    CODE_BYPASS_REENTRY: REASON_BYPASS_REENTRY_ZERO_POS,
    CODE_BYPASS_HEDGE: REASON_BYPASS_HEDGE,
}


# ─── Default config knobs ──────────────────────────────────────────────────────
DEFAULT_QUARANTINE_ENABLED = True
DEFAULT_QUARANTINE_BYPASS_REENTRY_ZERO_POS = True
DEFAULT_QUARANTINE_BYPASS_HEDGE = True
DEFAULT_VEC_GATES_LOG_ONLY = True

ENTRY_ACTIONS = frozenset({
    "OPEN", "AUGMENT", "REENTRY", "REVERSE", "REVERSE_AUGMENT",
    "QUICK_OPEN", "QUICK_AUGMENT", "QUICK_HEDGE_OPEN", "QUICK_HEDGE_AUGMENT",
})

CLOSE_TOKENS = ("CLOSE", "REDUCE", "KILL")


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _cfg_get(config: Any, attr: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, attr, default)


def _is_entry_action(action: Optional[str]) -> bool:
    if not action:
        return False
    up = action.upper()
    if any(tok in up for tok in CLOSE_TOKENS):
        return False
    return ("OPEN" in up) or ("AUGMENT" in up) or ("ENTRY" in up)


def load_quarantine_list(base_path: Optional[str] = None) -> List[str]:
    """Load function_quarantine.json (list[str]). Returns [] if missing/error.
    Mirrors live's silent fail-open."""
    try:
        path = Path(base_path) / "data" / "function_quarantine.json" if base_path else \
               Path("data/function_quarantine.json")
        with open(str(path)) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [str(x) for x in data if x]
        return []
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        return []
    except Exception:
        return []


# ─── QUARANTINE_BLOCK — SCALAR ─────────────────────────────────────────────────

def evaluate_quarantine_core(
    position_key: Optional[str],
    reason: Optional[str],
    action: Optional[str],
    is_hedge: bool,
    position_amt: float,
    quarantine_set: Iterable[str],
    config: Any = None,
) -> Tuple[bool, str]:
    """Mirrors ez_manage.py:14169-14183 with user-contract bypasses.

    Returns (blocked, reason_str). reason_str matches live's BLOCKED_QUARANTINED({name})
    when blocked, else one of: OK, DISABLED, BYPASS_REENTRY_ZERO_POS, BYPASS_HEDGE.

    Args:
        position_key: unused for matching (kept for live-signature parity / logging)
        reason: the trade reason string; this is what live substring-matches
        action: OPEN/AUGMENT/REENTRY/CLOSE/REDUCE/HEDGE_*/...
        is_hedge: True if this call is a hedge action
        position_amt: current positionAmt (used for REENTRY+zero bypass)
        quarantine_set: iterable of function/reason names to quarantine
        config: object with QUARANTINE_ENFORCE_ENABLED, QUARANTINE_BYPASS_*
    """
    if not _cfg_get(config, "QUARANTINE_ENFORCE_ENABLED", DEFAULT_QUARANTINE_ENABLED):
        return (False, REASON_DISABLED)
    bypass_hedge = _cfg_get(config, "QUARANTINE_BYPASS_HEDGE", DEFAULT_QUARANTINE_BYPASS_HEDGE)
    bypass_reentry = _cfg_get(
        config, "QUARANTINE_BYPASS_REENTRY_ZERO_POS", DEFAULT_QUARANTINE_BYPASS_REENTRY_ZERO_POS,
    )
    if bypass_hedge and is_hedge:
        return (False, REASON_BYPASS_HEDGE)
    act_up = (action or "").upper()
    if bypass_reentry and ("REENTRY" in act_up) and (float(position_amt or 0) == 0.0):
        return (False, REASON_BYPASS_REENTRY_ZERO_POS)
    reason_key = (reason or "").strip()
    if not reason_key:
        return (False, REASON_OK)
    # Live substring match: any qname found in reason_key triggers block.
    for qname in quarantine_set:
        if qname and qname in reason_key:
            return (True, f"BLOCKED_QUARANTINED({qname})")
    return (False, REASON_OK)


# ─── QUARANTINE_BLOCK — VECTORIZED ─────────────────────────────────────────────

def evaluate_quarantine_vec(
    reasons: Sequence[str],
    actions: Sequence[str],
    is_hedge_arr: np.ndarray,
    position_amts: np.ndarray,
    quarantine_set: Iterable[str],
    config: Any = None,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Vectorized QUARANTINE_BLOCK evaluation over N candidate trades.

    Returns:
        blocked_arr  (np.ndarray bool, shape [N])
        code_arr     (np.ndarray int8, shape [N]); maps via REASON_BY_QCODE
        matched_name (list[str], len N); name of quarantined entry that matched,
                     "" when not blocked
    """
    n = len(reasons)
    blocked = np.zeros(n, dtype=bool)
    codes = np.full(n, CODE_OK, dtype=np.int8)
    matched: List[str] = [""] * n
    if n == 0:
        return blocked, codes, matched
    if not _cfg_get(config, "QUARANTINE_ENFORCE_ENABLED", DEFAULT_QUARANTINE_ENABLED):
        codes[:] = CODE_DISABLED
        return blocked, codes, matched
    bypass_hedge = bool(_cfg_get(config, "QUARANTINE_BYPASS_HEDGE", DEFAULT_QUARANTINE_BYPASS_HEDGE))
    bypass_reentry = bool(_cfg_get(
        config, "QUARANTINE_BYPASS_REENTRY_ZERO_POS", DEFAULT_QUARANTINE_BYPASS_REENTRY_ZERO_POS,
    ))
    qlist = [q for q in quarantine_set if q]
    is_hedge_arr = np.asarray(is_hedge_arr, dtype=bool)
    pos_amt_arr = np.asarray(position_amts, dtype=float)
    for i in range(n):
        if bypass_hedge and bool(is_hedge_arr[i]):
            codes[i] = CODE_BYPASS_HEDGE
            continue
        act_up = (actions[i] or "").upper()
        if bypass_reentry and ("REENTRY" in act_up) and pos_amt_arr[i] == 0.0:
            codes[i] = CODE_BYPASS_REENTRY
            continue
        rkey = (reasons[i] or "").strip()
        if not rkey:
            codes[i] = CODE_OK
            continue
        hit = ""
        for q in qlist:
            if q in rkey:
                hit = q
                break
        if hit:
            blocked[i] = True
            codes[i] = CODE_QUARANTINED
            matched[i] = hit
    return blocked, codes, matched


# ─── VEC_STRATEGY_GATES PARITY — SCALAR ────────────────────────────────────────

def evaluate_vec_strategy_gates_parity_core(
    account: Optional[str],
    symbol: Optional[str],
    side: str,
    action: Optional[str],
    is_hedge: bool,
    is_full_close: bool,
    ind_data: Optional[dict],
    klines_15m: Optional[List[Dict[str, Any]]] = None,
    config: Any = None,
) -> Tuple[bool, str, Dict[str, bool]]:
    """Re-runs the live VEC_STRATEGY_GATES decision under the SAME pre-filter
    that ez_manage.py:13476-13519 applies.

    Returns (blocks_entry, status_code, shadow_dict):
        blocks_entry: True iff live would have returned BLOCKED_VEC_GATE_...
        status_code:  one of OK | DISABLED | NOT_ENTRY | SHADOW_ONLY | ALLOW | BLOCK | BYPASS_HEDGE
        shadow_dict:  same dict shape as vec_strategy_gates.shadow_log_evaluation()

    NEVER raises — bad indicator data / module unavailable → (False, "OK", {}).
    """
    shadow: Dict[str, bool] = {}
    # Hedge / full_close / non-entry actions are not subject to VEC_STRATEGY_GATES
    if is_hedge:
        return (False, "BYPASS_HEDGE", shadow)
    if is_full_close:
        return (False, "NOT_ENTRY", shadow)
    if not _is_entry_action(action):
        return (False, "NOT_ENTRY", shadow)
    if not account or not symbol:
        return (False, "OK", shadow)
    side_up = "LONG" if (side or "").upper() == "LONG" else "SHORT"
    ind = ind_data or {}
    try:
        # Module sits at top of repo
        from vec_strategy_gates import check_vec_gate, shadow_log_evaluation  # type: ignore
    except Exception:
        return (False, "OK", shadow)
    try:
        shadow = shadow_log_evaluation(account, symbol, side_up, ind, klines_15m, config_obj=config) or {}
    except Exception:
        shadow = {}
    log_only = bool(_cfg_get(config, "VEC_GATES_LOG_ONLY", DEFAULT_VEC_GATES_LOG_ONLY))
    if log_only:
        return (False, "SHADOW_ONLY", shadow)
    try:
        fires, gate_reason = check_vec_gate(account, symbol, side_up, ind, klines_15m, config_obj=config)
    except Exception:
        return (False, "OK", shadow)
    if not fires:
        return (True, f"BLOCK:{gate_reason}", shadow)
    return (False, f"ALLOW:{gate_reason}", shadow)


def evaluate_vec_strategy_gates_parity_vec(
    accounts: Sequence[Optional[str]],
    symbols: Sequence[Optional[str]],
    sides: Sequence[str],
    actions: Sequence[Optional[str]],
    is_hedge_arr: np.ndarray,
    is_full_close_arr: np.ndarray,
    ind_dicts: Sequence[Optional[dict]],
    klines_15m_list: Optional[Sequence[Optional[List[Dict[str, Any]]]]] = None,
    config: Any = None,
) -> Tuple[np.ndarray, List[str], List[Dict[str, bool]]]:
    """Batched parity over N candidate entries. Returns
        blocks_arr  (np.ndarray bool [N])
        statuses    (list[str] [N])
        shadows     (list[dict] [N])
    """
    n = len(accounts)
    blocks = np.zeros(n, dtype=bool)
    statuses: List[str] = [""] * n
    shadows: List[Dict[str, bool]] = [{} for _ in range(n)]
    if n == 0:
        return blocks, statuses, shadows
    is_hedge_arr = np.asarray(is_hedge_arr, dtype=bool)
    is_full_close_arr = np.asarray(is_full_close_arr, dtype=bool)
    klines_list = klines_15m_list if klines_15m_list is not None else [None] * n
    for i in range(n):
        b, s, sh = evaluate_vec_strategy_gates_parity_core(
            accounts[i], symbols[i], sides[i], actions[i],
            bool(is_hedge_arr[i]), bool(is_full_close_arr[i]),
            ind_dicts[i] if ind_dicts is not None else None,
            klines_list[i] if klines_list is not None else None,
            config=config,
        )
        blocks[i] = b
        statuses[i] = s
        shadows[i] = sh
    return blocks, statuses, shadows
