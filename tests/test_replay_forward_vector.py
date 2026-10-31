import time

import numpy as np

from tools.replay_forward_vector import (
    _ReplayClock,
    _actions_at,
    _expected_closed_bar,
    _timestamps_sha256,
    _validate_stock_rth_timestamps,
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



def test_stock_rth_policy_allows_session_gaps_without_filling_them():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    stamps = [
        int(datetime(2026, 10, 29, 9, 30, tzinfo=tz).timestamp()),
        int(datetime(2026, 10, 29, 9, 45, tzinfo=tz).timestamp()),
        int(datetime(2026, 10, 30, 9, 30, tzinfo=tz).timestamp()),
        int(datetime(2026, 10, 30, 9, 45, tzinfo=tz).timestamp()),
    ]
    _validate_stock_rth_timestamps(stamps)
    assert stamps[2] - stamps[1] > 900
    assert _timestamps_sha256(stamps) == _timestamps_sha256(list(stamps))


def test_stock_rth_policy_rejects_missing_intraday_bars():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import pytest

    tz = ZoneInfo("America/New_York")
    stamps = [
        int(datetime(2026, 10, 29, 9, 30, tzinfo=tz).timestamp()),
        int(datetime(2026, 10, 29, 10, 0, tzinfo=tz).timestamp()),
    ]
    with pytest.raises(ValueError, match="missing/irregular intraday bar"):
        _validate_stock_rth_timestamps(stamps)

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
    import tools.opt.v12_pilot as pilot_v12
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
    (code_root / "tools").mkdir(parents=True)
    import shutil
    from pathlib import Path
    shutil.copyfile(
        Path(__file__).parents[1] / "tools" / "replay_network_guard.py",
        code_root / "tools" / "replay_network_guard.py",
    )
    import socket
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, getattr(socket.socket, name))
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, getattr(socket, name))
    output_path = tmp_path / "out.jsonl"
    seen = []

    def fake_state_for(symside, window_days):
        import tools.opt.evaluate_v12 as current_evaluate_v12
        import v12_quick_engine as current_engine

        canonical = forward_state.ROOT / "backtest_v8" / "indicators" / "XUSDC.npz"
        fresh, reason = pilot_v12.npz_freshness(symside)
        assert fresh, reason
        with np.load(canonical, allow_pickle=True) as current_npz:
            prefix_ts = int(current_npz["timestamps"][-1])
            prefix_close = current_npz["close"].copy()
        decision_ts = int(forward_state.time.time())
        seen.append((symside, window_days, prefix_ts, decision_ts, len(prefix_close)))
        return {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "npz_tail": prefix_ts,
            "ok": True,
            "open": True,
            "engine_qty": float(prefix_ts),
            "engine_open_qty": float(prefix_ts),
            "engine_open_px": float(prefix_close[-1]),
            "engine_open_bar_ts": prefix_ts,
            "entry_bar": prefix_ts,
            "entry_px": float(prefix_close[-1]),
            "acts_bar_ts": prefix_ts,
            "acts_last_bar": [{"type": "OPEN", "ts": prefix_ts}],
            "src": "test",
            "settings_fallback": False,
            "ledger": [{"type": "OPEN", "ts": prefix_ts, "qty": 1.0}],
        }

    monkeypatch.setattr(forward_state, "state_for", fake_state_for)
    result = replay(npz_path, active_path, "XUSDC_LONG", 900, 2700,
                    output_path, code_root, limit=None, score_start_ts=1800)

    assert result["states_evaluated"] == 2
    assert seen == [
        ("XUSDC_LONG", 30, 900, 1800, 1),
        ("XUSDC_LONG", 30, 1800, 2700, 2),
        ("XUSDC_LONG", 30, 2700, 3600, 3),
    ]
    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    assert next(row for row in rows if row["record"] == "initial_lifecycle")["cutoff_npz_open_ts"] == 900
    action_rows = [row for row in rows if row["record"] == "action"]
    assert [row["npz_open_ts"] for row in action_rows] == [1800, 2700]
    state_rows = [row for row in rows if row["record"] == "state"]
    assert [row["phase"] for row in state_rows] == ["warmup", "scored"]
    assert [row["market_price"] for row in state_rows] == [101.0, 102.0]
    assert state_rows[0]["planner_state"]["engine_qty"] == 1800.0
    assert state_rows[0]["planner_state"]["acts_last_bar"] == [{"type": "OPEN", "ts": 1800}]
    summary = next(row for row in rows if row["record"] == "replay_summary")
    assert summary["coverage_ok"] is True
    assert summary["phase_counts"] == {"warmup": 1, "scored": 1}
    assert summary["network_guard"]["main_audit"]
    assert summary["network_guard"]["main_blocked_attempts"] >= 0
    header = next(row for row in rows if row["record"] == "replay_manifest")
    assert header["network_guard_main"]["installed"] is True


def test_actual_vector_planner_emits_convergence_open_and_honors_done_key(tmp_path):
    import json

    import tools.vector_direct_trader as vector_direct_trader

    ss = "XUSDC_LONG"
    state = {
        "ok": True,
        "npz_tail": 1800,
        "open": True,
        "engine_qty": 3.0,
        "engine_open_qty": 2.0,
        "engine_open_bar_ts": 900,
        "acts_bar_ts": 1800,
        "acts_last_bar": [],
    }
    states = {ss: state}
    positions = {}
    done = set()
    allowed = lambda acct, side: acct == "ang" and side == ss
    previous_start = vector_direct_trader.START_POSITION_SIZE
    previous_max = vector_direct_trader.MAX_OPEN_NOTIONAL
    previous_ceiling = vector_direct_trader.ENTRY_NOTIONAL_CEILING_USD
    vector_direct_trader.START_POSITION_SIZE = 15.0
    vector_direct_trader.MAX_OPEN_NOTIONAL = 80.0
    vector_direct_trader.ENTRY_NOTIONAL_CEILING_USD = 80.0
    try:
        orders, notes = vector_direct_trader.plan_account(
            "ang", "crypto", [ss], states, positions, 1800, done, allowed
        )
        assert not notes
        assert len(orders) == 1
        assert orders[0]["act"] == "OPEN"
        assert orders[0]["notional"] == 22.5
        assert orders[0]["key"] == "ang|XUSDC_LONG|1800|OPEN"
        assert "OPEN" not in {row["type"] for row in state["acts_last_bar"]}

        exchange_doc = {
            "_fetched": time.time(),
            "symbols": {"XUSDC": {
                "step": 1.0,
                "min_qty": 1.0,
                "max_qty": 100000.0,
                "min_notional": 5.0,
                "status": "TRADING",
            }},
        }
        (tmp_path / "exchange_info_crypto.json").write_text(json.dumps(exchange_doc))
        sizer = object.__new__(vector_direct_trader.Binance)
        sizer.out = tmp_path
        sizer._xi = None
        qty, reason = sizer.size(orders[0], 0.0, 0.01)
        assert qty == 2250.0
        assert reason == ""

        done.add(orders[0]["key"])
        repeated, _ = vector_direct_trader.plan_account(
            "ang", "crypto", [ss], states, positions, 1800, done, allowed
        )
        assert repeated == []
    finally:
        vector_direct_trader.START_POSITION_SIZE = previous_start
        vector_direct_trader.MAX_OPEN_NOTIONAL = previous_max
        vector_direct_trader.ENTRY_NOTIONAL_CEILING_USD = previous_ceiling
