"""Publish coherence (2026-10-03): TIM-guard veto, repair->REDO rule, content gate, pro-rata 365D floor.

Regression: 1000FLOKIUSDT_SHORT_bhm15p84_gain1p58 filled 2794 rows from a TIM-3.58
baseline, promoted ~nothing (C=0, E flat), then published a repaired +1.58 set whose
steps were nowhere in the rows — and charts whose headers contradicted filenames.
These predicates are the fix; they must keep failing closed.
"""
import os
import sys

sys.path.insert(0, ".")
import v15_pilot as P


def test_tim_guard_vetoes_low_tim():
    assert P.tim_guard_veto(3.4) != ""
    assert P.tim_guard_veto(19.9) != ""


def test_tim_guard_allows_healthy_band():
    assert P.tim_guard_veto(20.0) == ""
    assert P.tim_guard_veto(25.3) == ""
    assert P.tim_guard_veto(80.0) == ""


def test_tim_guard_vetoes_high_tim():
    assert P.tim_guard_veto(80.1) != ""


def test_tim_guard_unknown_abstains():
    assert P.tim_guard_veto(None) == ""


def test_tim_guard_allows_recovery_toward_band():
    assert P.tim_guard_veto(18.0, 16.1) == ""
    assert P.tim_guard_veto(75.0, 85.0) == ""


def test_tim_guard_blocks_decay_away_from_band():
    assert P.tim_guard_veto(14.0, 16.1) != ""
    assert P.tim_guard_veto(88.0, 85.0) != ""


def test_tim_guard_kill_switch():
    os.environ["V15_TIM_GUARD"] = "0"
    try:
        assert P.tim_guard_veto(3.4) == ""
    finally:
        os.environ.pop("V15_TIM_GUARD", None)


def test_repair_with_applied_steps_needs_redo():
    assert P.repair_needs_redo({"steps": [{"applied": "ENTRY:x=True"}]}) is True
    assert P.repair_needs_redo({"steps": [{"applied": "a"}] * 9}) is True


def test_repair_without_applied_steps_may_publish():
    assert P.repair_needs_redo({"steps": []}) is False
    assert P.repair_needs_redo({"steps": [{"result": "no improving candidate"}]}) is False
    assert P.repair_needs_redo({}) is False


def test_content_gate_blocks_empty_sheet():
    assert P.content_ok(0, 2794) != ""
    assert P.content_ok(12, 2794) != ""
    assert P.content_ok(299, 2794) != ""


def test_content_gate_allows_real_fill():
    assert P.content_ok(2026, 2794) == ""
    assert P.content_ok(1340, 2794) == ""
    assert P.content_ok(3000, 3100) == ""


def test_content_gate_blocks_quarter_missing_board():
    assert P.content_ok(600, 2794) != ""


def test_365d_prorata_floor_short_history():
    au = {"valid": True, "gain_pct": 26.76, "trades": 73, "tim_pct": 26.15}
    ok, _ = P._qualifies_365d(au, 80.3)
    assert ok is True


def test_365d_full_floor_without_span():
    au = {"valid": True, "gain_pct": 26.76, "trades": 73, "tim_pct": 26.15}
    ok, _ = P._qualifies_365d(au)
    assert ok is False


def test_365d_negative_always_fails():
    neg = {"valid": True, "gain_pct": -0.64, "trades": 2187}
    ok, _ = P._qualifies_365d(neg, 403.0)
    assert ok is False
