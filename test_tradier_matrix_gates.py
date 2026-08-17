from types import SimpleNamespace

from tradier_matrix_gates import (
    bb_pullback_gate_blocks,
    delta_dc_floor_confirms,
    trend_reversal_at_recovery_top,
)


def test_bb_pullback_gate_is_directional_and_fails_open():
    cfg = SimpleNamespace(
        BB_PULLBACK_GATE_ENABLED=True,
        BB_PULLBACK_GATE_TF="15m",
        BB_PULLBACK_GATE_LONG_MAX=0.30,
        BB_PULLBACK_GATE_SHORT_MIN=0.70,
    )
    assert bb_pullback_gate_blocks({"bb_pct_b_15m": 0.31}, cfg, True)
    assert not bb_pullback_gate_blocks({"bb_pct_b_15m": 0.30}, cfg, True)
    assert bb_pullback_gate_blocks({"bb_pct_b_15m": 0.69}, cfg, False)
    assert not bb_pullback_gate_blocks({}, cfg, True)


def test_dc_floor_only_confirms_an_existing_delta_exit():
    assert delta_dc_floor_confirms({"dc_low_15m": 99.0}, 98.9, True)
    assert not delta_dc_floor_confirms({"dc_low_15m": 99.0}, 99.1, True)
    assert delta_dc_floor_confirms({"dc_high_15m": 101.0}, 101.1, False)
    assert not delta_dc_floor_confirms({"dc_high_15m": 101.0}, 100.9, False)
    # Data loss must not suppress a Delta decision.
    assert delta_dc_floor_confirms({}, 100.0, True)


def test_trend_reversal_requires_recovery_after_structure_break():
    long = {
        "wt1_1h": -2, "wt2_1h": 1, "wt1_4h": -3, "wt2_4h": 0,
        "dc_low_1h": 90, "dc_low_1h_ant": 95, "dc_low_5m": 91,
        "k_5m": 40, "k_5m_prev": 55,
    }
    assert trend_reversal_at_recovery_top(long, 92, True)
    assert not trend_reversal_at_recovery_top(long, 90, True)
    short = {
        "wt1_1h": 2, "wt2_1h": -1, "wt1_4h": 3, "wt2_4h": 0,
        "dc_high_1h": 110, "dc_high_1h_ant": 105, "dc_high_5m": 109,
        "k_5m": 60, "k_5m_prev": 45,
    }
    assert trend_reversal_at_recovery_top(short, 108, False)
    assert not trend_reversal_at_recovery_top(short, 110, False)
