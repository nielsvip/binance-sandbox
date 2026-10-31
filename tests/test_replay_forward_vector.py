import time

import numpy as np

from tools.replay_forward_vector import (
    _ReplayClock,
    _actions_at,
    _expected_closed_bar,
    row_prefix,
)


def test_prefix_slices_every_time_aligned_array_without_future_rows():
    arrays = {
        "timestamps": np.arange(10, 18),
        "close": np.arange(100, 108),
        "2d": np.arange(16).reshape(8, 2),
        "scalar": np.asarray(5),
        "metadata": "fixed",
    }
    prefix = row_prefix(arrays, 3, 8)
    assert prefix["timestamps"].tolist() == [10, 11, 12, 13]
    assert prefix["close"].tolist() == [100, 101, 102, 103]
    assert prefix["2d"].tolist() == [[0, 1], [2, 3], [4, 5], [6, 7]]
    assert int(prefix["scalar"]) == 5
    assert prefix["metadata"] == "fixed"


def test_bar_open_timestamp_maps_to_next_close_decision_clock():
    bar_open = 1_800_000_000
    decision_clock = bar_open + 900
    assert _expected_closed_bar(decision_clock) == bar_open
    replay_clock = _ReplayClock(decision_clock, time)
    assert replay_clock.time() == decision_clock
    assert replay_clock.gmtime() == time.gmtime()


def test_actions_are_emitted_only_for_current_npz_bar():
    events = [
        {"type": "OPEN", "ts": 1_800_000_000, "qty": 2},
        {"type": "AUGMENT", "ts": 1_800_000_000, "qty": 1},
        {"type": "REDUCE", "ts": 1_799_999_100, "qty": 0.5},
        {"type": "CLOSE", "ts": 1_800_000_900, "qty": 3},
        {"type": "NOTE", "ts": 1_800_000_000},
        {"type": "CLOSE", "ts": 1_800_000_000, "reason": "FINAL_MTM"},
    ]
    current = _actions_at(events, 1_800_000_000)
    assert [row["type"] for row in current] == ["OPEN", "AUGMENT"]


def test_replay_calls_state_for_on_strictly_causal_prefixes(tmp_path, monkeypatch):
    import hashlib
    import json
    from datetime import datetime, timezone

    import tools.v15_forward_state as forward_state
    from tools.replay_forward_vector import replay

    ts = np.arange(900, 4500, 900, dtype=np.int64)
    npz_path = tmp_path / "sample.npz"
    np.savez(npz_path, timestamps=ts, close=np.arange(len(ts), dtype=float) + 100.0)
    active = {"sym_side": "XUSDC_LONG", "overrides": {"A": True}, "updated_at": "frozen"}
    active["overrides_sha256"] = hashlib.sha256(
        json.dumps(active["overrides"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    active_path = tmp_path / "active.json"
    active_path.write_text(json.dumps(active))
    code_root = tmp_path / "code"
    code_root.mkdir()
    output_path = tmp_path / "out.jsonl"
    seen = []

    def fake_state_for(symside, window_days):
        import tools.opt.evaluate_v12 as current_evaluate_v12
        current_npz = current_evaluate_v12._store("XUSDC", "crypto", "")["timestamps"][-1]
        decision_ts = int(forward_state.time.time())
        seen.append((symside, window_days, int(current_npz), decision_ts))
        return {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "npz_tail": int(current_npz),
            "ok": True,
            "ledger": [{"type": "OPEN", "ts": int(current_npz), "qty": 1.0}],
        }

    monkeypatch.setattr(forward_state, "state_for", fake_state_for)
    original_npz = getattr(forward_state, "_replay_test_npz", None)
    # State provider reads the same patched store used by state_for's prepare path.
    import tools.opt.evaluate_v12 as evaluate_v12
    old_store = evaluate_v12._store
    evaluate_v12._store = lambda *_args: forward_state._replay_test_npz
    try:
        forward_state._replay_test_npz = {"timestamps": ts, "close": np.arange(len(ts), dtype=float) + 100.0}
        result = replay(npz_path, active_path, "XUSDC_LONG", 900, 2700,
                        output_path, code_root, limit=None)
    finally:
        evaluate_v12._store = old_store
        if original_npz is None:
            delattr(forward_state, "_replay_test_npz")
        else:
            forward_state._replay_test_npz = original_npz

    assert result["states_evaluated"] == 2
    assert seen == [("XUSDC_LONG", 30, 1800, 2700), ("XUSDC_LONG", 30, 2700, 3600)]
    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    action_rows = [row for row in rows if row["record"] == "action"]
    assert [row["npz_open_ts"] for row in action_rows] == [1800, 2700]
