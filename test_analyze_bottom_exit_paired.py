from tools.analyze_bottom_exit_paired import _base_key, _paired_selection


def test_base_pair_identity_ignores_only_emergency_fields():
    common = {
        "arm_tf": "4h",
        "confirm_tf": "15m",
        "confirmation_bars": 1,
        "confirmation_mode": "AND",
        "max_wait_1h": 48,
        "prebreak_lookback": 6,
        "rebound_atr": 0.5,
    }
    base = {"params": {**common, "emergency_modes": []}}
    brake = {
        "params": {
            **common,
            "emergency_modes": ["ADVERSE_ATR"],
            "emergency_adverse_atr": 3.0,
            "emergency_label": "ADVERSE_ATR_3",
        }
    }
    assert _base_key(base) == _base_key(brake)
    brake["params"]["confirmation_mode"] = "OR"
    assert _base_key(base) != _base_key(brake)


def test_paired_selection_uses_discovery_and_marks_exposure_eligibility():
    row = {
        "symbol": "MU",
        "side": "LONG",
        "emergency_label": "ADVERSE_ATR_3",
        "discovery": {
            "rare": True,
            "brake_fills": 2,
            "future_htf": 0,
            "beyond_reclaim": 0,
            "base_tim": 74.0,
            "emergency_tim": 73.0,
            "delta_return_pp": 5.0,
            "brake_share": 0.1,
        },
        "validation": {},
    }
    selected = _paired_selection([row])
    assert len(selected) == 1
    assert selected[0]["discovery_exposure_eligible"] is True
