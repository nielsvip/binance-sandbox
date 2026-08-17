from tools.diagnose_srs_factor import SrsParams, condition_values


def _values():
    return {
        "bb_upper_1h": 11.0,
        "bb_lower_1h": 9.0,
        "stoch_k_1h": 10.0,
        "stoch_k_1h_prev": 9.0,
        "stoch_k_15m": 10.0,
        "stoch_k_15m_prev": 9.0,
        "stoch_k_5m": 10.0,
        "stoch_k_5m_prev": 9.0,
        "wt1_1h": 1.0,
        "wt2_1h": 0.0,
        "wt1_15m": 1.0,
        "wt2_15m": 0.0,
        "wt1_5m": 1.0,
        "wt2_5m": 0.0,
    }


def test_short_entry_precondition_is_below_lower_not_above():
    correct = condition_values(
        side="SHORT",
        entry_price=8.5,
        price=9.0,
        values=_values(),
        params=SrsParams(85, 15, 100),
    )
    assert correct["entry_outside"]
    assert not correct["legacy_opposite_entry_test"]
    wrong = condition_values(
        side="SHORT",
        entry_price=9.5,
        price=9.0,
        values=_values(),
        params=SrsParams(85, 15, 100),
    )
    assert not wrong["entry_outside"]
    assert wrong["legacy_opposite_entry_test"]


def test_stock_default_short_cascade_passes_at_k10():
    predicates = condition_values(
        side="SHORT",
        entry_price=8.5,
        price=9.0,
        values=_values(),
        params=SrsParams(85, 15, 100),
    )
    required = {
        key: value
        for key, value in predicates.items()
        if key != "legacy_opposite_entry_test"
    }
    assert all(required.values())
