"""Pending-confirm holds (USER 2026-10-24): a native order refused for lack of vec confirmation waits for the
vec to catch up instead of dying — flagged for switch creation, unflagged when the identical vec signal lands."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_twins import vec_pending as VP


def _reset():
    VP._HOLDS.clear()


def test_holdable_covers_suppression_and_opinion_vetoes():
    for refusal in ("BLOCKED_VEC_DRIVEN_NATIVE_SUPPRESSED", "BLOCKED_NATIVE_ENTRY_VEC_DRIVEN", "BLOCKED_GATES_A_LONG", "BLOCKED_BOARD_ENTRY_NO_LATEST_BOARD", "BLOCKED_HTF_TREND_VETO_action=OPEN", "AUGMENTATION_COOLDOWN", "BLOCKED_NEWBORN_PROTECT_12s"):
        assert VP.holdable(refusal), refusal
    for refusal in ("SUCCESS", "BLOCKED_NON_TRADEABLE_x", "MARKET_CLOSED", "BLOCKED_BROKER_SYNC_STALE", "BLOCKED_QUARANTINED(y)", "BLOCKED_EMERGENCY_BRAKE_MAX_ENTRIES", "BLOCKED_SYM_HALT", "BLOCKED_CIRCUIT_BREAKER", ""):
        assert not VP.holdable(refusal), refusal


def test_protective_never_holds():
    for reason in ("FIXED_STOP_HIT", "LIQUIDATION_PROTECT", "EMERGENCY_MARGIN_KILL", "TRAILING_STOP_x"):
        assert VP.protective(reason), reason
        assert VP.file_hold("crypto", "men:A_LONG", "CLOSE", reason, "BLOCKED_GATES_A_LONG") is None
    assert not VP.protective("WT_DC_ENTRY")
    _reset()


def test_direction_mapping():
    assert VP.direction_of("OPEN") == "OPEN" and VP.direction_of("AUGMENT") == "OPEN" and VP.direction_of("REENTRY") == "OPEN"
    assert VP.direction_of("CLOSE") == "CLOSE" and VP.direction_of("REDUCE") == "CLOSE"
    assert VP.direction_of("HOLD") is None and VP.direction_of("") is None


def test_file_refresh_release_expiry():
    _reset()
    hold, expired, is_new = VP.file_hold("crypto", "men:A_LONG", "OPEN", "WT_DC_ENTRY", "BLOCKED_GATES_A_LONG", now=1000.0)
    assert is_new and expired == [] and VP.pending_count() == 1
    hold2, _, is_new2 = VP.file_hold("crypto", "men:A_LONG", "OPEN", "WT_DC_ENTRY", "BLOCKED_GATES_A_LONG", now=1100.0)
    assert not is_new2 and hold2["filed_ts"] == 1000.0 and hold2["refreshed_ts"] == 1100.0 and VP.pending_count() == 1
    assert VP.release_for("men:A_LONG", "CLOSE", now=1150.0) is None and VP.pending_count() == 1
    got = VP.release_for("men:A_LONG", "OPEN", now=1150.0)
    assert got is not None and got["reason"] == "WT_DC_ENTRY" and VP.pending_count() == 0
    VP.file_hold("crypto", "men:B_SHORT", "CLOSE", "TECHNICAL_DC", "BLOCKED_HTF_TREND_VETO_action=CLOSE", now=1000.0)
    assert VP.release_for("men:B_SHORT", "CLOSE", now=1000.0 + VP.window_s() + 1) is None
    assert VP.pending_count() == 0
    _reset()


def test_window_floor():
    assert VP.window_s() >= 60.0


def test_deferred_classifies_as_policy_never_escalated():
    from exit_guarantee import classify
    assert classify("DEFERRED_VEC_CONFIRM_WAIT", "WT_DC_ENTRY") == "POLICY"
