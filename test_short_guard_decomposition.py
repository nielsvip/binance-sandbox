from tools.vec_short_guard_decomposition import (
    CONFLICT_KEYS,
    EMERGENCY_KEYS,
    GuardProfile,
    PREREGISTERED_PROFILES,
    guard_decision,
)


def _emergency_ok():
    return {key: True for key in EMERGENCY_KEYS}


def _no_conflicts():
    return {key: False for key in CONFLICT_KEYS}


def test_full_conflict_bypass_never_bypasses_any_emergency():
    full = next(p for p in PREREGISTERED_PROFILES if p.label.startswith("G99_"))
    for emergency in EMERGENCY_KEYS:
        state = _emergency_ok()
        state[emergency] = False
        ok, reasons = guard_decision(
            state, {key: True for key in CONFLICT_KEYS}, full,
            qualified_top=True, causal_rollover=True,
        )
        assert ok is False
        assert f"EMERGENCY:{emergency}" in reasons


def test_path_scoped_bypass_requires_qualified_top_and_causal_rollover():
    full = next(p for p in PREREGISTERED_PROFILES if p.label.startswith("G99_"))
    ok, reasons = guard_decision(
        _emergency_ok(), _no_conflicts(), full,
        qualified_top=False, causal_rollover=True,
    )
    assert not ok and reasons == ("NOT_QUALIFIED_TOP",)
    ok, reasons = guard_decision(
        _emergency_ok(), _no_conflicts(), full,
        qualified_top=True, causal_rollover=False,
    )
    assert not ok and reasons == ("NO_CAUSAL_ROLLOVER",)


def test_single_ablation_only_bypasses_declared_component():
    profile = GuardProfile("RSI15_ONLY", ("RSI_15M",))
    conflicts = _no_conflicts()
    conflicts["RSI_15M"] = True
    conflicts["BULL_D_WT"] = True
    ok, reasons = guard_decision(
        _emergency_ok(), conflicts, profile,
        qualified_top=True, causal_rollover=True,
    )
    assert not ok
    assert reasons == ("BULL_D_WT",)


def test_full_path_scoped_profile_can_bypass_only_conflict_set():
    full = next(p for p in PREREGISTERED_PROFILES if p.label.startswith("G99_"))
    ok, reasons = guard_decision(
        _emergency_ok(), {key: True for key in CONFLICT_KEYS}, full,
        qualified_top=True, causal_rollover=True,
    )
    assert ok and reasons == ()
