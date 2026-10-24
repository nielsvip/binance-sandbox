"""BB-recovery class: a POLICY_NON_VECTOR_EXIT_DISABLED refusal must HOLD for vec
confirm (USER 2026-10-24 VEC_ clean-run) instead of dying outright; protective
reasons must never hold even under that policy."""
from live_twins import vec_pending as vp


def test_policy_non_vector_exit_refusal_holds():
    vp._HOLDS.clear()
    assert vp.holdable("POLICY_NON_VECTOR_EXIT_DISABLED") is True
    filed = vp.file_hold("crypto", "fin:BBUSDT_SHORT", "REDUCE",
                         "BB_RECOVERY_EXIT_TRADIER_SHORT_px1.0_k72",
                         "POLICY_NON_VECTOR_EXIT_DISABLED")
    assert filed is not None
    hold, _, is_new = filed
    assert hold["direction"] == "CLOSE" and is_new is True
    assert vp.release_for("fin:BBUSDT_SHORT", "CLOSE") is not None


def test_protective_reason_never_holds_under_policy():
    vp._HOLDS.clear()
    assert vp.file_hold("crypto", "fin:XRPUSDC_SHORT", "CLOSE",
                        "FIXED_STOP_SHORT_px0.99",
                        "POLICY_NON_VECTOR_EXIT_DISABLED") is None
    assert vp.pending_count() == 0


def test_preemptive_hold_files_silently_and_releases():
    vp._HOLDS.clear()
    filed = vp.file_preemptive_hold("crypto", "fin:BBUSDT_SHORT", "REDUCE",
                                    "BB_RECOVERY_EXIT_TRADIER_SHORT_px1.0_k72")
    assert filed is not None
    hold, _, is_new = filed
    assert hold["direction"] == "CLOSE" and is_new is True
    assert vp.release_for("fin:BBUSDT_SHORT", "CLOSE") is not None
    vp._HOLDS.clear()
    assert vp.file_preemptive_hold("crypto", "fin:XRPUSDC_SHORT", "CLOSE",
                                   "FIXED_STOP_SHORT_px0.99") is None
