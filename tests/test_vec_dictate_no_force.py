"""USER 2026-10-25 VEC_DICTATE_ONLY: dictate refusals are parity HOLDs, never FORCE-escalated.

444 crypto + 148 stock SUCCESS_FORCE_CLOSE in 3d flattened positions the board kept open
(open->flat->reopen churn, 50 churn keys). Genuine failures (VERIFY) and strategy-gate
refusals of dictated closes must still escalate.
"""
import exit_guarantee as xg

DICTATE_HOLDS = (
    "BLOCKED_NON_VEC_DICTATE",
    "BLOCKED_HERMETIC_NOT_VEC",
    "BLOCKED_VECTOR_EXEC_OFF_BAR_WINDOW",
    "BLOCKED_VECTOR_EXEC_STALE_BAR",
    "VEC_EXEC_BOARD_WITHHELD",
    "POLICY_NON_VECTOR_EXIT_DISABLED",
)


def test_dictate_refusals_classify_policy_never_escalated():
    for res in DICTATE_HOLDS:
        assert xg.classify(res, "QUICK_PEAK_GIVEBACK_GAIN_EROSION_STOP", True) == "POLICY"
        assert xg.classify(res, "FIXED_STOP_SHORT_px0.007", True) == "POLICY"


def test_genuine_failures_still_escalate():
    # REFUSED and VERIFY both reach _escalate (only NOOP/POLICY return early).
    assert xg.classify("TIMEOUT_BROKER_BUSY", "FIXED_STOP", True) == "REFUSED"
    assert xg.classify("TIMEOUT_ALREADY_FLAT", "FIXED_STOP", True) == "VERIFY"
    assert xg.classify("", "FIXED_STOP", True) == "REFUSED"


def test_strategy_gate_refusal_of_dictated_close_still_refused():
    assert xg.classify("BLOCKED_NOLOSS_MIN_GAIN", "VEC_DRIVEN_OPEN_PURE_BOARD", True) == "REFUSED"
