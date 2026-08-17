import numpy as np

from tools import replay_shared_engine_raw_formation_exit_overlay as raw


def test_raw_overlay_only_counts_formation_exit_while_exact_position_open(monkeypatch):
    timestamps = np.array([1, 2, 3, 4], dtype=np.int64)
    close = np.array([10.0, 10.0, 11.0, 10.0])
    mask = np.array([False, False, True, False])
    monkeypatch.setattr(raw, "formation_vector_mask", lambda *a, **k: (mask, np.ones(4), np.zeros(4, dtype=int)))
    cfg = raw.v8.SweepConfig()
    events = [{"timestamp": 1, "side": "LONG", "action": "OPEN", "reason": "LR_BAND"}, {"timestamp": 4, "side": "LONG", "action": "CLOSE", "reason": "OTHER"}]
    result = raw.raw_overlay("ABC", "LONG", events, {"close": close}, timestamps, cfg, 0.0)
    assert result["formation_exit_action_count"] == 1
    assert result["trades"] == 1
    assert result["canary_pass"] is True


def test_timestamp_reader_skips_missing_ts_before_valid_timestamp():
    assert raw.event_ts({"timestamp": 123.0}) == 123
