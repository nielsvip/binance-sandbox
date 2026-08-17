from tradier_reentry_wt_contract import mandatory_reentry_wt_gate


def _indicators():
    return {
        "wt1_5m": 2.0,
        "wt2_5m": 1.0,
        "wt1_5m_prev": 0.0,
        "wt2_5m_prev": 1.0,
        "wt_velocity_5m": 2.0,
        "wt_velocity_5m_prev": 2.0,
        "wt1_15m": 0.0,
        "wt2_15m": 1.0,
        "wt1_15m_prev": 0.0,
        "wt2_15m_prev": 1.0,
        "wt_velocity_15m": -1.0,
        "wt_velocity_15m_prev": -2.0,
    }


def test_or_gate_accepts_one_fresh_directional_tf_without_slowdown():
    ok, detail = mandatory_reentry_wt_gate(
        _indicators(), True, enabled=True, min_tfs=1, velocity_ratio=0.9
    )
    assert ok, detail


def test_gate_rejects_decelerating_favorable_tf():
    indicators = _indicators()
    indicators["wt_velocity_5m"] = 1.0
    ok, detail = mandatory_reentry_wt_gate(
        indicators, True, enabled=True, min_tfs=1, velocity_ratio=0.9
    )
    assert not ok, detail


def test_two_tf_mode_requires_both_confirmations():
    ok, detail = mandatory_reentry_wt_gate(
        _indicators(), True, enabled=True, min_tfs=2, velocity_ratio=0.9
    )
    assert not ok, detail
