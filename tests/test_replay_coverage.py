import asyncio
import pytest
from tools.replay_clock import ReplayEventLedger, expect_replay_bars, set_completed_bar_clock
from tools.replay_ownership import replay_ownership_scope

@pytest.mark.parametrize("visited, verdict", [([900, 1800, 2700], "COMPLETE"), ([900, 1800], "INCOMPLETE"), ([900, 1800, 3600], "INCOMPLETE"), ([900, 1800, 1800], "INCOMPLETE"), ([], "INCOMPLETE")])
def test_coverage_detects_caps_missing_shifted_and_repeated_bars(visited, verdict):
    @replay_ownership_scope
    async def simulate():
        expect_replay_bars([900, 1800, 2700])
        bar, decision = [0], [0]
        events = ReplayEventLedger(bar, decision)
        for timestamp in visited:
            set_completed_bar_clock(bar, decision, timestamp, 900)
        return events
    report = asyncio.run(simulate()).replay_coverage
    assert report["verdict"] == verdict
    assert report["expected_bars"] == 3
    assert report["visited_bars"] == len(visited)
    assert (report["expected_timestamps_sha256"] == report["visited_timestamps_sha256"]) is (verdict == "COMPLETE")
