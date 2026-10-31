import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools.replay_vector_consumer import _MockBroker, _ReplayTime, _prefix_digest, _validate_state_stream
from tools.v15_forward_state import _vector_acts


def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _iso(value):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _fixture(tmp_path):
    timestamps = np.asarray([900, 1800, 2700, 3600], dtype=np.int64)
    close = np.asarray([1.0, 1.1, 1.2, 1.3], dtype=np.float64)
    active = {"sym_side": "XUSDC_LONG", "overrides": {"A": True}, "overrides_sha256": _canonical({"A": True})}
    active_path = tmp_path / "active.json"
    active_path.write_text(json.dumps(active))
    npz_path = tmp_path / "input.npz"
    npz_path.write_bytes(b"frozen-npz")
    args = SimpleNamespace(
        state_stream=str(tmp_path / "states.jsonl"), npz=str(npz_path), active_set=str(active_path),
        start_ts=900, end_ts=2700, score_start_ts=1800,
        expected_timestamps_sha256=hashlib.sha256(b"[1800,2700]").hexdigest(),
        expected_warmup_bars=1, expected_scored_bars=1, code_manifest_sha="codehash",
    )
    rows = []
    for position, index in enumerate((1, 2), 1):
        bar = int(timestamps[index])
        phase = "warmup" if bar <= args.score_start_ts else "scored"
        rows.append({
            "record": "state", "row": position, "npz_open_ts": bar, "prefix_last_ts": bar,
            "decision_clock_ts": bar + 900, "phase": phase,
            "prefix_sha256_timestamps_close": _prefix_digest(timestamps, close, index),
            "ok": True, "planner_state": {"ok": True}, "settings_sha256": active["overrides_sha256"],
            "market_price": close[index],
        })
    manifest = {
        "record": "replay_manifest", "sym_side": active["sym_side"],
        "input_npz_sha256": hashlib.sha256(npz_path.read_bytes()).hexdigest(),
        "active_set_file_sha256": hashlib.sha256(active_path.read_bytes()).hexdigest(),
        "active_set_overrides_sha256": active["overrides_sha256"],
        "selected_timestamps_sha256": args.expected_timestamps_sha256,
        "bar_count": 2, "timestamp_policy": "crypto_15m_regular",
        "warmup_bar_count_expected": 1, "scored_bar_count_expected": 1,
        "interval_start_exclusive": _iso(args.start_ts),
        "interval_end_inclusive": _iso(args.end_ts), "score_start_exclusive": _iso(args.score_start_ts),
        "npz_open_ts_to_decision_clock_seconds": 900, "code_manifest_sha256": "codehash",
    }
    summary = {"record": "replay_summary", "sym_side": active["sym_side"], "coverage_ok": True,
               "states_evaluated": 2, "phase_counts": {"warmup": 1, "scored": 1}, "invalid_state_counts": {}}
    stream = [manifest, {"record": "initial_lifecycle", "ok": True}, *rows, summary]
    Path(args.state_stream).write_text("\n".join(json.dumps(row) for row in stream) + "\n")
    return args, {"timestamps": timestamps, "close": close}, active


def test_consumer_preflight_accepts_exact_complete_ordered_stream(tmp_path):
    args, arrays, active = _fixture(tmp_path)
    manifest, states, summary, stream_hash = _validate_state_stream(args, arrays, active)
    assert len(states) == summary["states_evaluated"] == 2
    assert manifest["bar_count"] == 2
    assert stream_hash == hashlib.sha256(Path(args.state_stream).read_bytes()).hexdigest()


def test_consumer_preflight_rejects_reordered_or_duplicate_bars(tmp_path):
    args, arrays, active = _fixture(tmp_path)
    lines = [json.loads(line) for line in Path(args.state_stream).read_text().splitlines()]
    lines[2], lines[3] = lines[3], lines[2]
    Path(args.state_stream).write_text("\n".join(json.dumps(row) for row in lines) + "\n")
    with pytest.raises(ValueError, match="order/clock/phase"):
        _validate_state_stream(args, arrays, active)


def test_replay_time_uses_completed_bar_epoch_and_real_monotonic(tmp_path):
    clock = [2700.0]
    replay_time = _ReplayTime(clock, __import__("time"))
    assert replay_time.time() == 2700.0
    assert replay_time.monotonic() > 0
    clock[0] = 3600.0
    assert replay_time.time() == 3600.0


def test_mock_standing_stop_fills_before_closed_bar_planner(tmp_path):
    fills = []
    broker = _MockBroker(SimpleNamespace(accounts=["ang"]), {"symbols": {}}, {}, fills)
    broker.position["XUSDC_LONG"] = {"qty": 5.0, "entry": 100.0, "break_even": 100.0, "price": 99.0}
    broker.algos[1] = {"algoId": 1, "symbol": "XUSDC", "positionSide": "LONG", "triggerPrice": "98.0",
                       "closePosition": "true", "algoStatus": "NEW", "orderType": "STOP_MARKET", "workingType": "MARK_PRICE", "account": "ang"}
    broker.bar_ts = 2700
    assert broker.process_standing_stops(open_price=99.0, high=100.0, low=97.5) == [fills[0]]
    assert broker.position == {}
    assert fills[0]["position_before"] == 5.0 and fills[0]["position_after"] == 0.0
    assert fills[0]["price"] == 98.0
    assert fills[0]["fill_model"] == "contract_ohlc_trigger_at_stop_or_gap_open"


def test_mock_standing_stop_uses_gap_open_and_short_high_trigger():
    fills = []
    broker = _MockBroker(SimpleNamespace(accounts=["ang"]), {"symbols": {}}, {}, fills)
    broker.position["XUSDC_LONG"] = {"qty": 5.0}
    broker.algos[1] = {"symbol": "XUSDC", "positionSide": "LONG", "triggerPrice": "98.0", "closePosition": "true", "orderType": "STOP_MARKET", "workingType": "MARK_PRICE", "account": "ang"}
    broker.position["YUSDC_SHORT"] = {"qty": 7.0}
    broker.algos[2] = {"symbol": "YUSDC", "positionSide": "SHORT", "triggerPrice": "102.0", "closePosition": "true", "orderType": "STOP_MARKET", "workingType": "MARK_PRICE", "account": "ang"}
    broker.bar_ts = 3600
    rows = broker.process_standing_stops(open_price=97.0, high=103.0, low=96.0)
    assert len(rows) == 2
    assert {row["sym_side"] for row in rows} == {"XUSDC_LONG", "YUSDC_SHORT"}
    assert {row["price"] for row in rows} == {97.0, 102.0}
    assert broker.position == {} and broker.algos == {}


def test_forward_projection_preserves_native_stop_permission_and_pnl_fields():
    raw = {"type": "CLOSE", "ts": 2700.0, "price": 98.5, "exit_price": 98.5, "qty": 5.0,
           "bar_entry": 1, "bar_exit": 2, "entry_price": 100.0, "avg_price": 99.4,
           "pnl_dollars": -4.5, "pnl_pct": -1.2, "reason": "FIXED_STOP", "exit_reason": "FIXED_STOP",
           "fixed_stop_level": 98.5, "mandatory_stop_kind": "DC15_FROZEN_DEFAULT"}
    position, acts = _vector_acts([raw], 2, [900, 1800, 2700])
    assert position["engine_qty"] == 0.0
    assert acts == [{"type": "CLOSE", "qty": 5.0, "price": 98.5, "reason": "FIXED_STOP",
                     "qty_before": 0.0, "qty_after": 0.0, "qty_frac": 1.0,
                     "exit_reason": "FIXED_STOP", "entry_price": 100.0, "exit_price": 98.5,
                     "avg_price": 99.4, "pnl_dollars": -4.5, "pnl_pct": -1.2,
                     "fixed_stop_level": 98.5, "mandatory_stop_kind": "DC15_FROZEN_DEFAULT"}]
