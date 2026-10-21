"""Vec-only exits (USER 2026-10-21): every non-vector exit path is disabled.

Months of premature closes (WT/MTF/DC/stop/vigilance/manual scalping hours-old
positions, e.g. flz:XRPUSDC_LONG closed twice at -0.08%/+0.35% within minutes).
Only a vec-dictated close/reduce may trade. Native exits are refused POLICY
(the exit-guarantee wrapper never escalates POLICY: no watcher, no FORCE).
NATIVE_EXITS_DISABLED=0 restores legacy behavior.
"""

import inspect
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import exit_guarantee as XG  # noqa: E402

SRC = pathlib.Path("ez_manage.py").read_text()
SVC = pathlib.Path("ez_positions_service.py").read_text()

VEC_REASONS = [
    "VEC_DRIVEN_OPEN_PURE_BOARD_BOARD_HOLDS_LIVE_FLAT_BOARD_QTY_2131_LIVE_Q",
    "VEC_DRIVEN_CLOSE_WT_EXIT",
    "RECONCILE_XRPUSDC_LONG",
    "EXIT_GUARANTEE_FORCE[VEC_DRIVEN_OPEN_PURE_BOARD] WT_X",
    "twin act |VEC_EXACT live",
]
NATIVE_REASONS = [
    "WT_SIMPLE_GUARANTEE_15m_wt-27.8vs-21.4_g-0.08%",
    "DC_15M_HOLD_OVERRIDE",
    "MTF_GR_WT_EXIT_15m",
    "MANUAL close",
    "ORPHANED_HEDGE",
    "VIGILANCE_2_CONSECUTIVE_LOSING",
    "FIXED_STOP_HIT",
    "CB_HTF_EXHAUST_X",
]


def _gate_passes(reason):
    rx = str(reason or "").upper()
    return "VEC_DRIVEN_" in rx or "RECONCILE_" in rx or "|VEC_EXACT" in rx


def test_refusal_classifies_policy_never_escalated():
    assert (
        XG.classify("POLICY_NON_VECTOR_EXIT_DISABLED", "WT_SIMPLE_GUARANTEE_X")
        == "POLICY"
    )
    assert (
        XG.classify("POLICY_NON_VECTOR_EXIT_DISABLED", "VEC_DRIVEN_CLOSE_X") == "POLICY"
    )


def test_vec_reasons_pass_native_blocked():
    assert all(_gate_passes(r) for r in VEC_REASONS)
    assert not any(_gate_passes(r) for r in NATIVE_REASONS)


def test_gate_present_before_force_branch():
    i_gate = SRC.index("NON_VECTOR_EXIT_DISABLED")
    i_force = SRC.index("return await self._exit_force_market")
    assert i_gate < i_force


def test_exit_action_covers_close_reduce_not_open():
    assert XG.is_exit_action("CLOSE", True) is True
    assert XG.is_exit_action("REDUCE", False) is True
    assert XG.is_exit_action("OPEN", False) is False
    assert XG.is_exit_action("AUGMENT", False) is False
    assert XG.is_exit_action("PLACE_STOP", False) is False


def test_stop_manager_disarmed():
    assert "NATIVE_EXITS_DISABLED" in SVC
    assert "lifted" in SVC and "resting managed stops" in SVC


def test_kill_switch_documented():
    assert "NATIVE_EXITS_DISABLED=0 restores" in SRC
