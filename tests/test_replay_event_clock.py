import pytest
from tools.replay_clock import ReplayEventLedger, set_completed_bar_clock

def test_fill_enrichment_preserves_original_event_and_signal_bar():
    bar, decision = [0], [0]
    set_completed_bar_clock(bar, decision, 1800, 900)
    ledger = ReplayEventLedger(bar, decision)
    event = {"timestamp": decision[0], "status": "PENDING"}
    ledger.append(event)
    event["status"] = "FILLED"
    set_completed_bar_clock(bar, decision, 2700, 900)
    ledger.extend([{"action": "REDUCE"}])
    assert ledger[0] is event
    assert event == {"timestamp": 2700, "status": "FILLED", "bar_ts": 1800, "decision_ts": 2700}
    assert ledger[1]["bar_ts"] == 2700
    assert ledger[1]["decision_ts"] == 3600

@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf")])
def test_invalid_completion_delay_is_rejected(duration):
    with pytest.raises(ValueError):
        set_completed_bar_clock([0], [0], 1800, duration)
