"""Stocks vector-authority backstop (USER 2026-10-24): vec-dictated orders record-and-continue through
every opinion veto to the late VEC_LAST backstop; natives keep every refusal. Pins the shared predicate and the
per-gate bypass wiring so a later edit cannot silently re-harden a gate against the vector (or soften a
sacred/safety gate for natives)."""
import inspect
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tradier_manage as TR


def test_tr_parity_vec_order_covers_force_open_reasons():
    for reason in ("BOARD_CONVERGE_OPEN vec=B12", "BOARD_CONVERGE_CLOSE vec=X", "PURE_BOARD_OPEN x", "VEC_DRIVEN_OPEN_WT_DC", "CLOSE TECHNICAL_DC_15m |VEC_EXACT"):
        assert TR._tr_parity_vec_order(reason), reason
    for reason in ("WT_DC_ENTRY", "EXIT_VELOCITY_WT_1h", "RATIO_REDUCE", "FIXED_STOP_HIT", ""):
        assert not TR._tr_parity_vec_order(reason), reason


_CONVERTED = ("NEWBORN_PROTECT", "DC_HARDSTOP_COOLDOWN", "MTF_FILTER", "RECENT_REDUCTION_GUARD", "OPEN_RATE_BREAKER", "DISASTER_GUARD", "PER_SYM_LIVE_GATE", "HTF_TREND_VETO", "HTF_TREND_VETO_ON_REDUCE", "EXIT_LH_LL", "AUGMENTATION_COOLDOWN", "REDUCTION_COOLDOWN", "LOSS_PROTECTION", "NOLOSS", "SYM_HALT", "VIGILANCE", "STALE_SET_HALT")


def test_converted_vetoes_record_and_continue_for_vec():
    src = inspect.getsource(TR.TradierTradeManager._execute_now_inner)
    assert "def _tr_bypass(_gate_name):" in src
    for gate in _CONVERTED:
        assert f'_tr_bypass("{gate}' in src or f"_tr_bypass(f\"{gate}" in src or f'_vec_last_bypassed.append("{gate}")' in src, gate


def test_sacred_and_safety_gates_have_no_vec_bypass():
    src = inspect.getsource(TR.TradierTradeManager._execute_now_inner)
    for gate in ("NON_TRADEABLE", "OPPOSITE_SIDE", "MARKET_CLOSED", "BROKER_SYNC", "QUARANTINED", "EMERGENCY_BRAKE", "ACCOUNT_ENTRY_BANNED"):
        assert f'_tr_bypass("{gate}' not in src and f"_tr_bypass(f\"{gate}" not in src, gate


def test_backstop_records_carry_fix_trail():
    src = inspect.getsource(TR.TradierTradeManager._execute_now_inner)
    assert '"kind": "VEC_LAST_OVERRIDE"' in src and '"kind": "VEC_LAST_REFUSE"' in src
    assert '"vec_path"' in src and "wire live to emit this vec path" in src
    assert "_queue_retrace" in src


def test_vec_unreachable_paths_fail_open_for_vec_only():
    """USER 2026-10-24 unreachable-real-fix (stocks mirror of the crypto proceed path): unreadable broker
    holdings and unverifiable preflight state proceed for vec-dictated orders; natives keep every refusal
    (mismatch gate, distress fail-fast, evidence-code preflight)."""
    po = inspect.getsource(TR.TradierTradeManager.place_order)
    assert "[TR_TRUTH_UNVERIFIED_PROCEED]" in po and '"POSITIONS_BROKER_UNREACHABLE"' in po
    inner = inspect.getsource(TR.TradierTradeManager._execute_now_inner)
    assert "[TR_VEC_PREFLIGHT_FAILOPEN]" in inner and "_tr_parity_vec_order(reason)" in inner
