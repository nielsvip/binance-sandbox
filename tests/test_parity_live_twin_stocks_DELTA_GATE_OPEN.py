"""PARITY LANE C 2026-10-06 — DELTA_GATE_OPEN: _side_vf now assigned before the gate (was UnboundLocalError -> gate silently dead).
Default True (config/cat/all store rows) -> no veto; a per-sym False would veto every fresh OPEN exactly like vec (disables opens)."""
from tests._laneC_parity_helpers import tm_source, tradier_default


def test_assigned_before_use():
    src = tm_source()
    a = src.index('_side_vf = "LONG" if is_long else "SHORT"  # PARITY LANE C 2026-10-06')
    b = src.index('_cfg("DELTA_GATE_OPEN", True, account_key, symbol, _side_vf)')
    assert a < b


def test_default_inert():
    assert tradier_default("DELTA_GATE_OPEN") in (True, "True")
