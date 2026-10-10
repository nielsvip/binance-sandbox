"""parity_exact_gate — TOTAL PARITY enforcement (USER 2026-10-10).

In PARITY_VEC_EXACT_MODE only vector-decided orders trade — the vec-chart
trades, nothing more, nothing less. Enforced at execute_now, the single
order chokepoint.

ALLOW:
- vec reasons: ' |VEC_EXACT' tag (in-script twin) or VEC_DRIVEN_* prefix.
- exempt exits: hard-safety (liquidation/margin/balance-floor), manual
  closes, broker-sync reconciliation. Exits only — safety never opens.
REFUSE: everything else (native entries AND native exits).

Also: local same-key exposure-increase claim (dedup). The wire guard keys
on broker-confirmed fills which lag seconds; two signals ms apart both
pass it (proven: inf AMATUSDT_SHORT filled twice 67ms apart). The claim
is synchronous in-process: first claim wins, second within TTL refused.
"""
from __future__ import annotations
import time

VX_TAG = " |VEC_EXACT"
VEC_DRIVEN_PREFIX = "VEC_DRIVEN_"
EMERGENCY_TOKENS = ("LIQUIDATION", "LIQ_", "MARGIN_KILL", "MARGIN_EMERGENCY", "EMERGENCY_MARGIN", "EMERGENCY_OVERSIZE", "FORCE_REDUCE", "BALANCE_FLOOR", "MANUAL")
SYNC_TOKENS = ("BROKER", "SYNC")
EXIT_TOKENS = ("CLOSE", "REDUCE")
ENTRY_TOKENS = ("OPEN", "AUGMENT", "REENTRY", "REVERSE", "ENTRY", "BUY")
CLAIM_TTL_S = 120.0
_claims: dict = {}


def is_vec_reason(reason) -> bool:
    r = str(reason or "")
    return VX_TAG in r or r.upper().startswith(VEC_DRIVEN_PREFIX)


def is_emergency_reason(reason) -> bool:
    r = str(reason or "").upper()
    return any(t in r for t in EMERGENCY_TOKENS)


def is_sync_reason(reason) -> bool:
    r = str(reason or "").upper()
    return any(t in r for t in SYNC_TOKENS)


def is_exit_action(action) -> bool:
    return any(t in str(action or "").upper() for t in EXIT_TOKENS)


def is_entry_action(action) -> bool:
    return any(t in str(action or "").upper() for t in ENTRY_TOKENS)


def allows(reason, action, is_full_close: bool = False) -> tuple:
    """(allowed, code). Pure — unit-tested, no imports from ez_manage."""
    r = str(reason or "")
    if is_vec_reason(r):
        return True, "VEC"
    if bool(is_full_close) or is_exit_action(action):
        if is_emergency_reason(r):
            return True, "EMERGENCY_EXIT"
        if is_sync_reason(r):
            return True, "SYNC_EXIT"
        return False, "NATIVE_EXIT"
    return False, "NATIVE_ENTRY"


def try_claim(key, now: float = None) -> tuple:
    """Same-key exposure-increase claim. (True, 0) = claimed; (False, age) = dup within TTL."""
    if not key:
        return True, 0.0
    t = time.time() if now is None else float(now)
    prev = _claims.get(key, 0.0)
    if t - prev < CLAIM_TTL_S:
        return False, t - prev
    _claims[key] = t
    return True, 0.0


def _reset_for_tests():
    _claims.clear()
