from tools import evaluate_intc_close_lift_grid_proposal as proposal


def test_nearest_bottom_b_exit_is_grid_exact_and_never_uses_metrics():
    intended = {
        "family": proposal.FAMILY,
        "params": {
            "arm_tf": "4h", "confirm_tf": "15m", "rebound_atr": 0.0,
            "prebreak_lookback": 3, "max_wait_1h": 24,
            "confirmation_mode": "AND", "confirmation_bars": 3,
            "emergency_modes": [], "emergency_adverse_atr": 0.0,
            "emergency_adverse_stdev": 0.0, "emergency_continued_bars": 0,
            "arm_break_mode": "PREV_BAR", "arm_break_threshold": 0.0,
            "max_wait_hours": 6,
        },
    }
    selected, candidates = proposal.nearest_grid_exit(intended)
    assert selected["rebound_atr"] == 0.125
    assert candidates[0]["rebound_distance"] == 0.125
    assert all("fold" not in row for row in candidates)
