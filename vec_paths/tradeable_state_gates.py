"""TRADEABLE_STATE_GATES — vec analogue of the 4 list/state entry guards.

LIVE SOURCES MIRRORED (ez_manage.py 2026-05-12):
  L13886-13915  TRADEABLE_HARD_BLOCK          — position_key must be in tradeable_keys.
                Bypasses: SCALP_V3_OPEN, INTERVENTION/MANUAL reasons auto-add the key.
                HEDGE_OPEN bypass: hedges open on the opposite side; the opposite-side
                position_key may not be in tradeable_keys yet.
  L13916-13927  FLAGGED_ORIGIN_BLOCK          — data/flagged_origins.json count>=3 blocks.
                One JSON file at config.BASE_PATH/data/flagged_origins.json.
  L13928-13940  POSITION_EXISTS_BLOCK         — if pos_value_usd > MIN_POSITION_SIZE
                AND gain < MIN_GAIN → BLOCK. REENTRY positionAmt==0 bypasses.
  L13971-13981  OPEN_ON_OPEN_BLOCK            — if action contains 'OPEN' AND
                positionAmt > min_qty → reclassify OPEN→AUGMENT (NOT a block — a mutation).

USER CONTRACT (verbatim):
  - REENTRY with positionAmt==0 bypasses POSITION_EXISTS_BLOCK.
  - HEDGE bypasses TRADEABLE_HARD_BLOCK (opening opposite side that may not yet be
    tradeable on its own).

CONFIG KNOBS (default values match live behaviour at file mtime 2026-05-12):
  TRADEABLE_HARD_BLOCK_ENABLED      bool   True
  FLAGGED_ORIGIN_BLOCK_ENABLED      bool   True
  FLAGGED_ORIGIN_BLOCK_THRESHOLD    int    3       (>=3 fires)
  POSITION_EXISTS_BLOCK_ENABLED     bool   True
  OPEN_ON_OPEN_BLOCK_ENABLED        bool   True
  MIN_POSITION_SIZE                 float  45.0
  MIN_GAIN                          float  1.2     (matches live default getattr)
  SCALP_V3_AUTOADD_ENABLED          bool   True
  INTERVENTION_AUTOADD_ENABLED      bool   True

PUBLIC API:
  evaluate_tradeable_state_gates_core(
      action, symbol, position_key,
      tradeable_keys_set, positions_dict, quarantine_set,
      config,
      is_hedge=False, reason="",
      min_qty=0.0001,
  ) -> (allowed: bool, reason: str, action_reclassified: str, gate_fired: str)

  evaluate_tradeable_state_gates_vec(
      actions, symbols, position_keys,
      tradeable_keys_set, positions_arr, quarantine_set,
      config,
      is_hedge_arr=None, reasons=None,
  ) -> (allowed_arr, reason_codes, action_reclassified_arr, gate_codes)

GATE CODES (np.int8, mirror REASON_BY_CODE):
  0 OK
  1 DISABLED                       (entire module disabled — placeholder, not used)
  2 NON_TRADEABLE_HARD_BLOCK
  3 FLAGGED_ORIGIN_BLOCK
  4 POSITION_EXISTS_BLOCK
  5 OPEN_ON_OPEN_RECLASSIFIED      (not a block — flags that action was mutated)
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Set, Tuple

import numpy as np


# ─── Reason-code constants ─────────────────────────────────────────────────────
REASON_OK = "OK"
REASON_DISABLED = "DISABLED"
REASON_NON_TRADEABLE = "BLOCKED_NON_TRADEABLE"
REASON_FLAGGED_ORIGIN = "BLOCKED_FLAGGED_ORIGIN"
REASON_POSITION_EXISTS = "BLOCKED_POSITION_EXISTS"
REASON_OPEN_RECLASSIFIED = "RECLASSIFIED_OPEN_TO_AUGMENT"

GATE_OK = "OK"
GATE_NON_TRADEABLE = "NON_TRADEABLE_HARD_BLOCK"
GATE_FLAGGED = "FLAGGED_ORIGIN_BLOCK"
GATE_POSITION_EXISTS = "POSITION_EXISTS_BLOCK"
GATE_OPEN_ON_OPEN = "OPEN_ON_OPEN_BLOCK"

CODE_OK = 0
CODE_DISABLED = 1
CODE_NON_TRADEABLE = 2
CODE_FLAGGED = 3
CODE_POSITION_EXISTS = 4
CODE_OPEN_RECLASSIFIED = 5

REASON_BY_CODE: Dict[int, str] = {
    CODE_OK: REASON_OK,
    CODE_DISABLED: REASON_DISABLED,
    CODE_NON_TRADEABLE: REASON_NON_TRADEABLE,
    CODE_FLAGGED: REASON_FLAGGED_ORIGIN,
    CODE_POSITION_EXISTS: REASON_POSITION_EXISTS,
    CODE_OPEN_RECLASSIFIED: REASON_OPEN_RECLASSIFIED,
}

# Action-class sets — mirror execute_now() classifiers
OPEN_LIKE_ACTIONS = frozenset({
    "OPEN", "QUICK_OPEN", "AUGMENT", "QUICK_AUGMENT", "REENTRY",
    "HEDGE_OPEN", "QUICK_HEDGE_OPEN", "REVERSE", "REVERSE_AUGMENT",
})
REENTRY_ACTIONS = frozenset({"REENTRY"})
HEDGE_REASON_TOKENS = ("HEDGE_OPEN", "HEDGE_AUGMENT", "QUICK_HEDGE", "OBLIGATORY_HEDGE")
SCALP_V3_TOKEN = "SCALP_V3_OPEN"
INTERVENTION_TOKENS = ("INTERVENTION", "MANUAL")


# ════════════════════════════════════════════════════════════════════════════════
# Config helper — read knobs off a config module / dict / None
# ════════════════════════════════════════════════════════════════════════════════

def _cfg_get(cfg: Any, name: str, default: Any) -> Any:
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _action_is_open(action: str) -> bool:
    """Mirror of live `_is_open_action` (ez_manage.py:13797-13798).

    Returns True for any entry-class action: OPEN/AUGMENT/REENTRY/HEDGE/QUICK_*,
    minus any with CLOSE/REDUCE/KILL tokens.
    """
    if not action:
        return False
    au = action.upper()
    if "CLOSE" in au or "REDUCE" in au or "KILL" in au:
        return False
    return (
        au in OPEN_LIKE_ACTIONS
        or "OPEN" in au
        or "HEDGE" in au
        or "ENTRY" in au
        or "AUGMENT" in au
        or "REENTRY" in au
    )


def _action_is_reentry(action: str) -> bool:
    return (action or "").upper() in REENTRY_ACTIONS


def _reason_carries_hedge(reason: str) -> bool:
    if not reason:
        return False
    ru = reason.upper()
    return any(tok in ru for tok in HEDGE_REASON_TOKENS)


def _reason_carries_scalp_v3(reason: str) -> bool:
    return SCALP_V3_TOKEN in (reason or "").upper()


def _reason_carries_intervention(reason: str) -> bool:
    if not reason:
        return False
    ru = reason.upper()
    return any(tok in ru for tok in INTERVENTION_TOKENS)


# ════════════════════════════════════════════════════════════════════════════════
# Position lookup helper — positions_dict may carry many shapes
# ════════════════════════════════════════════════════════════════════════════════

def _pos_amt(positions_dict: Mapping[str, Any], position_key: str) -> float:
    """Return |positionAmt| for position_key, 0.0 when missing."""
    if not positions_dict or not position_key:
        return 0.0
    p = positions_dict.get(position_key)
    if p is None:
        return 0.0
    if isinstance(p, dict):
        v = p.get("positionAmt", p.get("position_amt", 0.0))
    else:
        v = getattr(p, "positionAmt", getattr(p, "position_amt", 0.0))
    try:
        return abs(float(v))
    except (TypeError, ValueError):
        return 0.0


def _pos_value_usd(positions_dict: Mapping[str, Any], position_key: str, fallback_price: float = 0.0) -> float:
    if not positions_dict or not position_key:
        return 0.0
    p = positions_dict.get(position_key)
    if p is None:
        return 0.0
    if isinstance(p, dict):
        amt = abs(float(p.get("positionAmt", p.get("position_amt", 0.0)) or 0.0))
        px = float(p.get("mark_price", p.get("entry_price", 0.0)) or 0.0)
    else:
        amt = abs(float(getattr(p, "positionAmt", getattr(p, "position_amt", 0.0)) or 0.0))
        px = float(getattr(p, "mark_price", getattr(p, "entry_price", 0.0)) or 0.0)
    if px <= 0:
        px = fallback_price
    return amt * px


def _pos_gain_pct(positions_dict: Mapping[str, Any], position_key: str) -> float:
    if not positions_dict or not position_key:
        return 0.0
    p = positions_dict.get(position_key)
    if p is None:
        return 0.0
    if isinstance(p, dict):
        v = p.get("gain", 0.0)
    else:
        v = getattr(p, "gain", 0.0)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ════════════════════════════════════════════════════════════════════════════════
# Scalar core — single-call decision
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_tradeable_state_gates_core(
    action: str,
    symbol: str,
    position_key: str,
    tradeable_keys_set: Set[str],
    positions_dict: Mapping[str, Any],
    quarantine_set: Mapping[str, Any],
    config: Any,
    is_hedge: bool = False,
    reason: str = "",
    min_qty: float = 0.0001,
    fallback_price: float = 0.0,
) -> Tuple[bool, str, str, str]:
    """Mirror of the 4 entry-side list/state guards in ez_manage.execute_now.

    Returns:
        (allowed, reason, action_reclassified, gate_fired)

        allowed: True → caller may proceed; False → blocked.
        reason:  block string a la 'BLOCKED_NON_TRADEABLE_BTC_LONG'.
                 'OK' when allowed and not reclassified.
                 'RECLASSIFIED_OPEN_TO_AUGMENT' when OPEN→AUGMENT.
        action_reclassified: the action to use downstream (may differ from input).
        gate_fired: 'OK' | 'NON_TRADEABLE_HARD_BLOCK' | 'FLAGGED_ORIGIN_BLOCK' |
                    'POSITION_EXISTS_BLOCK' | 'OPEN_ON_OPEN_BLOCK'
    """
    # Default outputs
    out_action = action or ""

    # Only entry-side actions trigger this stack. Closes/reduces bypass.
    if not _action_is_open(out_action):
        return True, REASON_OK, out_action, GATE_OK

    _hard_enabled = bool(_cfg_get(config, "TRADEABLE_HARD_BLOCK_ENABLED", True))
    _flag_enabled = bool(_cfg_get(config, "FLAGGED_ORIGIN_BLOCK_ENABLED", True))
    _pos_enabled = bool(_cfg_get(config, "POSITION_EXISTS_BLOCK_ENABLED", True))
    _oo_enabled = bool(_cfg_get(config, "OPEN_ON_OPEN_BLOCK_ENABLED", True))
    _flag_thr = int(_cfg_get(config, "FLAGGED_ORIGIN_BLOCK_THRESHOLD", 3))
    _scalp_v3_auto = bool(_cfg_get(config, "SCALP_V3_AUTOADD_ENABLED", True))
    _intervention_auto = bool(_cfg_get(config, "INTERVENTION_AUTOADD_ENABLED", True))
    _min_pos_size = float(_cfg_get(config, "MIN_POSITION_SIZE", 45.0))
    _min_gain = float(_cfg_get(config, "MIN_GAIN", 1.2))

    # ── 1. TRADEABLE_HARD_BLOCK ───────────────────────────────────────────────
    # Hedge action bypass: opening opposite side; key may not be in tradeable.
    # SCALP_V3 / INTERVENTION auto-add live; mirror by treating as bypass here.
    if _hard_enabled and position_key and tradeable_keys_set is not None:
        if position_key not in tradeable_keys_set:
            _bypass = (
                is_hedge
                or _reason_carries_hedge(reason)
                or (_scalp_v3_auto and _reason_carries_scalp_v3(reason))
                or (_intervention_auto and _reason_carries_intervention(reason))
            )
            if not _bypass:
                return (
                    False,
                    f"{REASON_NON_TRADEABLE}_{position_key}",
                    out_action,
                    GATE_NON_TRADEABLE,
                )

    # ── 2. FLAGGED_ORIGIN_BLOCK ───────────────────────────────────────────────
    if _flag_enabled and position_key and quarantine_set:
        _entry = quarantine_set.get(position_key) if isinstance(quarantine_set, Mapping) else None
        _flag_count = 0
        if _entry is not None:
            if isinstance(_entry, Mapping):
                try:
                    _flag_count = int(_entry.get("count", 0) or 0)
                except (TypeError, ValueError):
                    _flag_count = 0
            else:
                # Treat plain membership in a set as a single hit.
                _flag_count = 1
        elif isinstance(quarantine_set, (set, frozenset)) and position_key in quarantine_set:
            _flag_count = max(_flag_thr, 3)  # any set membership counts as a block
        if _flag_count >= _flag_thr:
            return (
                False,
                f"{REASON_FLAGGED_ORIGIN}_{_flag_count}x",
                out_action,
                GATE_FLAGGED,
            )

    # ── 3. POSITION_EXISTS_BLOCK ──────────────────────────────────────────────
    # REENTRY positionAmt==0 bypass (user contract).
    if _pos_enabled and position_key and positions_dict:
        _amt = _pos_amt(positions_dict, position_key)
        _is_reentry = _action_is_reentry(out_action)
        if not (_is_reentry and _amt == 0.0):
            _val = _pos_value_usd(positions_dict, position_key, fallback_price=fallback_price)
            _gain = _pos_gain_pct(positions_dict, position_key)
            if _val > _min_pos_size and _gain < _min_gain:
                return (
                    False,
                    f"{REASON_POSITION_EXISTS}_gain{_gain:.2f}pct",
                    out_action,
                    GATE_POSITION_EXISTS,
                )

    # ── 4. OPEN_ON_OPEN_BLOCK (mutation, not a hard block) ────────────────────
    # If action contains 'OPEN' (not REENTRY/AUGMENT/HEDGE_OPEN) and a position
    # already exists with amt > min_qty → reclassify OPEN → AUGMENT.
    if _oo_enabled and position_key and out_action:
        au = out_action.upper()
        if "OPEN" in au and "CLOSE" not in au:
            _amt = _pos_amt(positions_dict, position_key) if positions_dict else 0.0
            if _amt > min_qty:
                _new = au.replace("OPEN", "AUGMENT")
                # Preserve casing convention: live uses both upper/lower repl.
                if action and action != au:
                    _new = action.replace("OPEN", "AUGMENT").replace("open", "augment")
                return True, REASON_OPEN_RECLASSIFIED, _new, GATE_OPEN_ON_OPEN

    return True, REASON_OK, out_action, GATE_OK


# ════════════════════════════════════════════════════════════════════════════════
# Vectorized — bulk decisions over arrays of candidate events
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_tradeable_state_gates_vec(
    actions: np.ndarray,
    symbols: np.ndarray,
    position_keys: np.ndarray,
    tradeable_keys_set: Set[str],
    positions_dict: Mapping[str, Any],
    quarantine_set: Mapping[str, Any],
    config: Any,
    is_hedge_arr: Optional[np.ndarray] = None,
    reasons: Optional[np.ndarray] = None,
    min_qty: float = 0.0001,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Bulk version. All arrays length N.

    Returns:
        allowed_arr            np.bool_   shape (N,)
        reason_strings         np.object_ shape (N,)  matches scalar 'reason' field
        action_reclassified    np.object_ shape (N,)  may equal input
        gate_codes             np.int8    shape (N,)  matches CODE_BY_GATE map
    """
    n = len(actions)
    allowed = np.ones(n, dtype=bool)
    reasons_out = np.full(n, REASON_OK, dtype=object)
    action_out = np.array(actions, dtype=object).copy()
    gates_out = np.full(n, CODE_OK, dtype=np.int8)

    if n == 0:
        return allowed, reasons_out, action_out, gates_out

    if is_hedge_arr is None:
        is_hedge_arr = np.zeros(n, dtype=bool)
    if reasons is None:
        reasons = np.full(n, "", dtype=object)

    for i in range(n):
        a, r, ar, g = evaluate_tradeable_state_gates_core(
            action=str(actions[i]) if actions[i] is not None else "",
            symbol=str(symbols[i]) if symbols[i] is not None else "",
            position_key=str(position_keys[i]) if position_keys[i] is not None else "",
            tradeable_keys_set=tradeable_keys_set,
            positions_dict=positions_dict,
            quarantine_set=quarantine_set,
            config=config,
            is_hedge=bool(is_hedge_arr[i]),
            reason=str(reasons[i]) if reasons[i] is not None else "",
            min_qty=min_qty,
        )
        allowed[i] = a
        reasons_out[i] = r
        action_out[i] = ar
        # Map gate string to code
        if g == GATE_OK:
            gates_out[i] = CODE_OK
        elif g == GATE_NON_TRADEABLE:
            gates_out[i] = CODE_NON_TRADEABLE
        elif g == GATE_FLAGGED:
            gates_out[i] = CODE_FLAGGED
        elif g == GATE_POSITION_EXISTS:
            gates_out[i] = CODE_POSITION_EXISTS
        elif g == GATE_OPEN_ON_OPEN:
            gates_out[i] = CODE_OPEN_RECLASSIFIED

    return allowed, reasons_out, action_out, gates_out


# ════════════════════════════════════════════════════════════════════════════════
# Helpers — load quarantine / flagged_origins JSON (utility, opt-in)
# ════════════════════════════════════════════════════════════════════════════════

def load_flagged_origins(path: Any) -> Dict[str, Dict[str, Any]]:
    """Load data/flagged_origins.json into the dict shape expected here.

    Returns empty dict on missing/unreadable file (matches live `except: pass`).
    """
    import json
    try:
        from pathlib import Path
        p = Path(path)
        if not p.exists():
            return {}
        with open(p) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}
