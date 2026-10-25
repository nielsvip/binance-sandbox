"""vec_parity_gates — pure predicates behind the 10 VEC-parity switches (USER 2026-10-24).

Every VEC_LAST_* intervention names a path to fix. These helpers decide the
gated verdicts from plain values so both live (ez_manage) and tests share one
implementation. All defaults reproduce current behavior (inert) except
VEC_DISAGREE_FLAT_OPEN_BLOCK, which implements the explicit zero-tolerance
order (no OPEN while the forward vec target is flat).
"""

CIRCUIT_BREAKER_TOKEN = "CIRCUIT_BREAKER"
QUICK_REDUCE_TOKENS = ("QUICK_REDUCE", "QUICK_BB_RECOVERY")
PROTECTED_TOKENS = (
    "STOP",
    "TRAILING",
    "HEDGE",
    "EMERGENCY",
    "LIQUIDATION",
    "MARGIN",
    "MANUAL",
    "FORCE",
    "GUARANTEE",
    "OBLIGATORY",
)
OPEN_ACTIONS = ("OPEN", "AUGMENT")
BAR_S = 900.0
TTL_S_MIN = 60.0
TTL_S_DEFAULT = 300.0
TTL_S_MAX = 3600.0


def split_gates(detail):
    return [g for g in str(detail or "").split("+") if g]


def override_verdict(tag, detail, refuse, override_on, breaker_bypass_on):
    if tag != "VEC_LAST_OVERRIDE":
        return tag, detail, refuse
    if not override_on:
        if refuse:
            return "VEC_LAST_REFUSE", refuse, refuse
        return "VEC_LAST_CLEAN", "VEC_OVERRIDE_DISABLED", refuse
    if not breaker_bypass_on and CIRCUIT_BREAKER_TOKEN in split_gates(detail):
        if refuse:
            return "VEC_LAST_REFUSE", refuse, refuse
        return "VEC_LAST_CLEAN", "VEC_OVERRIDE_BREAKER_VETO", refuse
    return tag, detail, refuse


def is_quick_reduce_reason(reason):
    r = str(reason or "").upper()
    return any(t in r for t in QUICK_REDUCE_TOKENS)


def non_vector_exit_refused(reason, master_on, quick_gate_on):
    if not master_on:
        return False
    if is_quick_reduce_reason(reason) and not quick_gate_on:
        return False
    return True


def is_protected_reason(reason):
    r = str(reason or "").upper()
    return any(t in r for t in PROTECTED_TOKENS)


def flat_open_blocked(action, reason, target_flat, block_on):
    if not block_on or not target_flat:
        return False
    if str(action or "").upper() not in OPEN_ACTIONS:
        return False
    if is_protected_reason(reason):
        return False
    return True


def disagree_refuse(disagree, kill_on):
    return bool(disagree and kill_on)


def pending_defer_on(value):
    return bool(value)


def pending_ttl_s(raw):
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        return TTL_S_DEFAULT
    if secs != secs:
        return TTL_S_DEFAULT
    return min(TTL_S_MAX, max(TTL_S_MIN, secs))


def hold_expiry_flatten_on(value):
    return bool(value)


def confirm_release_ok(dictated, vec_order, require_board):
    if dictated:
        return True
    if vec_order and not require_board:
        return True
    return False


def dictate_only_exempt(action, reason):
    # USER 2026-10-24 STEP1 narrowed: ONLY ratio trims/augments (no open/close
    # state change), board-confirmed vec speech, and bookkeeping survive.
    # Stops, emergencies, hedges (dead months ago), oversize trims: disabled
    # first, wired correctly later. Kill: VEC_DICTATE_ONLY_ENABLED=False.
    try:
        from live_twins import vector_enforce as _ve
    except Exception:
        return True
    try:
        if _ve._is_board_confirmed(reason):
            return True
        up = str(reason or "").upper()
        if any(b in up for b in _ve.BOOKKEEPING):
            return True
        act = str(action or "").upper()
        if "CLOSE" in act or "OPEN" in act or "ENTRY" in act:
            return False
        if _ve.family_of(reason) == "RATIO" and ("REDUCE" in act or "AUGMENT" in act):
            return True
    except Exception:
        return True
    return False


def exec_board_ok(action, open_on, close_on):
    act = str(action or "").upper()
    if "CLOSE" in act or "REDUCE" in act:
        return bool(close_on)
    return bool(open_on)
