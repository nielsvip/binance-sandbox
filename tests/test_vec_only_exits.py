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
    "BOARD_CONVERGE_CLOSE vec=WT_EXIT",
    "CLOSE VEC_EXIT_OWED",
    "CLOSE VEC_EXIT_OWED_BROKER",
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
    # Mirrors ez_manage._voe_reason_is_vec (single definition of vec-dictated).
    rx = str(reason or "").upper()
    return (
        "VEC_DRIVEN_" in rx
        or "RECONCILE_" in rx
        or "|VEC_EXACT" in rx
        or "BOARD_CONVERGE_" in rx
        or "EXIT_OWED" in rx
    )


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


def test_vec_reason_helper_single_definition():
    assert "def _voe_reason_is_vec(reason)" in SRC
    for tok in [
        "VEC_DRIVEN_",
        "RECONCILE_",
        "|VEC_EXACT",
        "BOARD_CONVERGE_",
        "EXIT_OWED",
    ]:
        assert tok in SRC.split("def _voe_reason_is_vec(reason)")[1].split("def ")[0]


def test_heart_intact_no_queue_chokepoint():
    # process_position / queue_trade_action / execute_trade_action stay whole:
    # no blanket kill inside the queue transport (reverted USER 2026-10-21).
    assert "_voe_native_exits_disabled() and not _voe_reason_is_vec" not in SRC
    assert "refused at queue" not in SRC


def test_new_exit_legs_stood_down():
    legs = [
        'if _ag_action == "force_close" and _ag_pos_amt > 0 and not _voe_native_exits_disabled()',
        "[NEWBORN_LOSS_KILL_STOOD_DOWN]",
        '"_uh_force_close") and not _voe_native_exits_disabled()',
        "if _ms_gain >= _ms_threshold and _ms_gain < _ms_prev and not _voe_native_exits_disabled()",
        "if _ppl_fired and _ppl_stop_level > 0 and _ppl_pos_amt > pos_min_qty and not _voe_native_exits_disabled()",
        "and not _voe_native_exits_disabled()  # USER 2026-10-21 vec-only exits\n        ):",
        'result = "SKIPPED_NON_VECTOR_EXIT_DISABLED"',
        "if reduce_qty_kill1 > 0 and reduce_qty_kill1 < position.positionAmt and not _voe_native_exits_disabled()",
        "if tp_reason and not _voe_native_exits_disabled()",
        "[OVERRIDE_REDUCE_STOOD_DOWN]",
        "([] if _voe_native_exits_disabled() else _ss_stalled)",
        "([] if _voe_native_exits_disabled() else _reduce_candidates)",
        "([] if _voe_native_exits_disabled() else _close_candidates)",
        "([] if _voe_native_exits_disabled() else _ow_candidates)",
        "and not _voe_native_exits_disabled()  # USER 2026-10-21 vec-only exits: emergency exit stood down",
        "[DC_BREACH_STOOD_DOWN]",
        "[BREAK_EVEN_GUARD_STOOD_DOWN]",
        "if _af_frac > 0 and _af_last > 0 and not _voe_native_exits_disabled()",
        "MOMENTUM_RIDER_EXIT_PROFIT_",
        "MOMENTUM_RIDER_HEDGE_CLOSE_profit",
        "MOMENTUM_RIDER_HEDGE_KILL_loss",
    ]
    for leg in legs:
        assert leg in SRC, leg
    assert SRC.count("vec-only exits: rider close stood down") == 3
    assert SRC.count("_voe_native_exits_disabled()") >= 50
