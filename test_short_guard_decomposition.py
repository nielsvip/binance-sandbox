from tools.vec_short_guard_decomposition import (
    CONFLICT_KEYS,
    EMERGENCY_KEYS,
    GuardProfile,
    PREREGISTERED_PROFILES,
    _select,
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


def _fold(*, capital: float, opportunity: float) -> dict:
    return {
        "capital_return_pct": capital,
        "opportunity_benchmark_pct": opportunity,
        "beats_opportunity_benchmark": capital > opportunity,
        "insolvent": False,
        "max_drawdown_account_pct": 2.0,
        "technical_exits": 3,
        "correction_capture_pct": 5.0,
    }


def test_discovery_gate_beats_fixed_2k_opportunity_in_every_fold():
    passed, _ = _select([
        _fold(capital=8.0, opportunity=0.0),
        _fold(capital=7.0, opportunity=31.0),
    ])
    assert not passed
    passed, _ = _select([
        _fold(capital=8.0, opportunity=1.0),
        _fold(capital=7.0, opportunity=0.0),
    ])
    assert passed
