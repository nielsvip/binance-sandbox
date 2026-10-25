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


def test_vec_holds_position_only_on_fresh_matching_state(tmp_path, monkeypatch):
    import json, time
    d = tmp_path / "state"
    d.mkdir()
    (d / "ARBUSDC_LONG.json").write_text(json.dumps(
        {"target_state": {"side": "long"}, "written_at": time.time()}))
    (d / "BATUSDT_LONG.json").write_text(json.dumps(
        {"target_state": {"side": "flat"}, "written_at": time.time()}))
    (d / "OLDUSDC_SHORT.json").write_text(json.dumps(
        {"target_state": {"side": "short"}, "written_at": time.time() - 3600}))
    monkeypatch.setenv("VEC_LIVE_STATE_DIR", str(d))
    assert xg._vec_holds_position("men:ARBUSDC_LONG") is True
    assert xg._vec_holds_position("men:BATUSDT_LONG") is False
    assert xg._vec_holds_position("men:OLDUSDC_SHORT") is False
    assert xg._vec_holds_position("men:MISSING_LONG") is False
    assert xg._vec_holds_position("men:WEIRD") is False
    monkeypatch.delenv("VEC_LIVE_STATE_DIR")
    assert xg._vec_holds_position("men:ARBUSDC_LONG") is False
