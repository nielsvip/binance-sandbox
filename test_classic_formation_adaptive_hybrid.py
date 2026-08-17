import numpy as np

from tools import run_classic_formation_adaptive_hybrid as hybrid


def test_route_pairs_are_observed_not_invented():
    schedule = [
        {"type": "OPEN", "reason_group": "GOLDEN_RULE"},
        {"type": "CLOSE", "reason_group": "HYBRID_STRUCT_EXIT"},
        {"type": "OPEN", "reason_group": "GOLDEN_RULE"},
        {"type": "CLOSE", "reason_group": "HYBRID_STRUCT_EXIT"},
    ]
    assert hybrid.productive_route_pairs(schedule) == [{
        "ordinary_entry_group": "GOLDEN_RULE", "ordinary_exit_group": "HYBRID_STRUCT_EXIT",
        "ordinary_entry_schedule_count": 2, "ordinary_exit_schedule_count": 2,
    }]


def test_entry_confirmation_replay_has_no_undeclared_actions(monkeypatch):
    ts = np.arange(24, dtype=np.int64) * 300
    close = np.full(24, 10.0)
    close[1::2] = 11.0
    schedule = []
    for i in range(0, 24, 2):
        schedule.extend([
            {"ts": int(ts[i]), "type": "OPEN", "reason_group": "GOLDEN_RULE"},
            {"ts": int(ts[i + 1]), "type": "CLOSE", "reason_group": "HYBRID_STRUCT_EXIT"},
        ])
    masks = np.zeros(24, dtype=bool); masks[::2] = True
    monkeypatch.setattr(hybrid, "ordinary_schedule", lambda *args: (schedule, []))
    monkeypatch.setattr(hybrid, "_formation_masks", lambda *args: ((masks, np.ones(24), np.zeros(24, dtype=int)), (np.zeros(24, dtype=bool), np.zeros(24), np.zeros(24, dtype=int))))
    evidence = hybrid.hybrid_replay(
        "ABC", "LONG", "ENTRY_CONFIRMATION", "wedge_entry_1h", "GOLDEN_RULE", "HYBRID_STRUCT_EXIT",
        hybrid.role_config(hybrid.v8.SweepConfig(), "wedge_entry_1h"), ({"close": close}, ts), 0.0,
    )
    assert evidence["trades"] == 12
    assert evidence["selected_formation_action_count"] == 12
    assert evidence["ordinary_entry_action_count"] == 12
    assert evidence["undeclared_event_count"] == 0
    assert hybrid.strict_failures(evidence) == []


def test_strict_gate_requires_multiplier_and_declared_activity():
    evidence = {
        "return_pct": 4.0, "trades": 11, "bh_gross_multiplier": 1.03,
        "strategy_to_bh_multiplier": 1.0097, "selected_formation_action_count": 1,
        "ordinary_entry_action_count": 1, "ordinary_exit_schedule_count": 1,
        "undeclared_event_count": 0,
    }
    assert hybrid.strict_failures(evidence) == []
    assert "DID_NOT_BEAT_SIDE_AWARE_BH_MULTIPLIER" in hybrid.strict_failures({**evidence, "strategy_to_bh_multiplier": 1.0})
    assert "DECLARED_ORDINARY_ENTRY_ACTIVITY_ZERO" in hybrid.strict_failures({**evidence, "ordinary_entry_action_count": 0})
