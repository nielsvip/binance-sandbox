import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools.v8_research_top_exit_adapter import (
    ResearchReplayError,
    SPEC_KIND,
    TopExitReplayAdapter,
)


def _write_fixture(tmp_path, events, *, open_by_ts=None):
    npz = tmp_path / "MU.npz"
    timestamps = np.array(
        sorted(
            {100, 190, 200, 290, 300, 400}
            | {int(e["fill_ts"]) for e in events}
            | {int(e["signal_ts"]) for e in events if e.get("signal_ts")}
        ),
        dtype=np.int64,
    )
    opens = np.full(len(timestamps), 100.0, dtype=np.float64)
    closes = opens.copy()
    event_by_fill = {int(e["fill_ts"]): e for e in events}
    for index, timestamp in enumerate(timestamps):
        event = event_by_fill.get(int(timestamp))
        if event:
            event_type = str(event["type"]).upper()
            fill = float(event["fill_px"])
            if event_type in {"ENTRY", "REENTRY"}:
                opens[index] = fill / 1.0002
            elif event_type == "EXIT":
                opens[index] = fill / 0.9998
            elif event_type == "MTM_FINAL":
                closes[index] = fill / 0.9998
        if open_by_ts and int(timestamp) in open_by_ts:
            opens[index] = float(open_by_ts[int(timestamp)])
    np.savez(
        npz,
        timestamps=timestamps,
        open_5m=opens,
        close_5m=closes,
    )
    schedule = tmp_path / "events.jsonl.gz"
    with gzip.open(schedule, "wt") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    spec = {
        "kind": SPEC_KIND,
        "version": 1,
        "promotion_allowed": False,
        "source_artifact": str(tmp_path),
        "symbol": "MU",
        "side": "LONG",
        "account": "trb",
        "event_schedule": str(schedule),
        "npz_path": str(npz),
        "expected_npz_sha256": hashlib.sha256(npz.read_bytes()).hexdigest(),
        "expected_schedule_sha256": hashlib.sha256(schedule.read_bytes()).hexdigest(),
        "seed_notional_usd": 2000.0,
        "commission_round_trip_pct": 0.10,
        "slippage_bps_one_way": 2.0,
        "expected_gain_pct": 9.79,
        "expected_tim_rth_pct": 50.0,
        "expected_counts": {
            "entries": 1,
            "exits": 1,
            "reentries": 1,
            "mtm_final": 1,
            "e10_reclaims": 1,
            "e11_lower": 0,
        },
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    return path, npz


def _events():
    return [
        {
            "type": "ENTRY",
            "fill_ts": 100,
            "fill_px": 100.0,
            "equity_after_fill": 0.9995,
        },
        {
            "type": "EXIT",
            "fill_ts": 200,
            "fill_px": 110.0,
            "signal_ts": 190,
            "latency_bars": 1,
            "equity_after_fill": 1.09835,
        },
        {
            "type": "REENTRY",
            "reason": "E10_RECLAIM",
            "fill_ts": 300,
            "fill_px": 111.0,
            "signal_ts": 290,
            "latency_bars": 1,
            "equity_after_fill": 1.0978,
        },
        {
            "type": "MTM_FINAL",
            "reason": "END_OF_SAMPLE",
            "fill_ts": 400,
            "fill_px": 112.0,
            "signal_ts": 400,
            "latency_bars": 0,
            "equity_after_fill": 1.107,
        },
    ]


def test_valid_schedule_hash_runtime_and_mtm(tmp_path):
    spec, npz = _write_fixture(tmp_path, _events())
    adapter = TopExitReplayAdapter(spec)
    assert [a.event_type for a in adapter.actions] == [
        "ENTRY",
        "EXIT",
        "REENTRY",
        "MTM_FINAL",
    ]
    assert adapter.actions[-1].full_close
    assert adapter.validate_npz(npz) == adapter.spec["expected_npz_sha256"]
    adapter.validate_runtime(
        account="trb",
        symbols=["MU"],
        mode="tradier",
        round_trip_cost_pct=0.10,
    )


def test_rejects_noncausal_reentry(tmp_path):
    events = _events()
    events[2]["latency_bars"] = 0
    spec, _ = _write_fixture(tmp_path, events)
    with pytest.raises(ResearchReplayError, match="REENTRY latency"):
        TopExitReplayAdapter(spec)


def test_rejects_npz_fingerprint_and_live_seed(tmp_path):
    spec, npz = _write_fixture(tmp_path, _events())
    adapter = TopExitReplayAdapter(spec)
    npz.write_bytes(b"mutated")
    with pytest.raises(ResearchReplayError, match="NPZ hash mismatch"):
        adapter.validate_npz(npz)
    with pytest.raises(ResearchReplayError, match="seeded live positions"):
        adapter.validate_runtime(
            account="trb",
            symbols=["MU"],
            mode="tradier",
            seed_positions_file="/tmp/live.json",
            round_trip_cost_pct=0.10,
        )


def test_final_audit_detects_missing_and_quantity_mismatch(tmp_path):
    spec, _ = _write_fixture(tmp_path, _events())
    adapter = TopExitReplayAdapter(spec)
    first = adapter.actions[0]
    adapter.record_result(
        first,
        result="SUCCESS",
        actual_quantity=float(first.quantity) + 1.0,
        actual_price=first.fill_price,
    )
    audit = adapter.final_audit()
    assert audit["status"] == "FAIL"
    assert audit["missing"]
    assert audit["quantity_mismatch"]


def test_requires_and_validates_expected_schedule_fingerprint(tmp_path):
    spec, _ = _write_fixture(tmp_path, _events())
    payload = json.loads(spec.read_text())
    payload.pop("expected_schedule_sha256")
    spec.write_text(json.dumps(payload))
    with pytest.raises(ResearchReplayError, match="schedule.*sha|sha.*schedule"):
        TopExitReplayAdapter(spec)

    spec, _ = _write_fixture(tmp_path, _events())
    payload = json.loads(spec.read_text())
    schedule = Path(payload["event_schedule"])
    with gzip.open(schedule, "at") as fh:
        fh.write(json.dumps({"type": "ENTRY", "fill_ts": 999, "fill_px": 1}) + "\n")
    with pytest.raises(ResearchReplayError, match="schedule.*mismatch|mismatch.*schedule"):
        TopExitReplayAdapter(spec)


def test_rejects_duplicate_fill_timestamp_and_same_bar_close_reentry(tmp_path):
    events = _events()
    events[1]["fill_ts"] = 300
    events[2]["fill_ts"] = 300
    spec, _ = _write_fixture(tmp_path, events)
    with pytest.raises(
        ResearchReplayError,
        match="duplicate|strictly increasing|same.bar",
    ):
        TopExitReplayAdapter(spec)


def test_loaded_npz_enforces_signal_to_immediately_next_execution_row(tmp_path):
    events = _events()
    # Metadata still claims one bar, but signal_ts is not the execution row
    # immediately before fill_ts in the loaded NPZ.
    events[1]["signal_ts"] = 100
    spec, npz = _write_fixture(tmp_path, events)
    adapter = TopExitReplayAdapter(spec)
    with pytest.raises(
        ResearchReplayError,
        match="signal.*next|next.*signal|latency",
    ):
        adapter.validate_npz(npz)


def test_loaded_npz_enforces_fill_against_actual_bar_open(tmp_path):
    events = _events()
    # Keep the NPZ fingerprint valid, but make the actual EXIT bar open
    # incompatible with the scheduled 2bp-adverse fill.
    spec, npz = _write_fixture(tmp_path, events, open_by_ts={200: 50.0})
    adapter = TopExitReplayAdapter(spec)
    with pytest.raises(
        ResearchReplayError,
        match="fill.*open|open.*fill|price",
    ):
        adapter.validate_npz(npz)


def test_engine_hashes_actual_loaded_npz_not_spec_declared_path():
    source = Path("backtest_v8_engine.py").read_text()
    assert 'validate_npz(_research_adapter_t.spec["npz_path"])' not in source
    assert "validate_npz" in source
    assert "stores" in source[source.index("validate_npz") - 500 : source.index("validate_npz") + 500]


def test_tim_is_audited_and_mismatch_fails(tmp_path):
    spec, _ = _write_fixture(tmp_path, _events())
    adapter = TopExitReplayAdapter(spec)
    audit = adapter.final_audit(actual_tim_rth_pct=51.0)
    assert audit["tim"]["expected_rth_pct"] == 50.0
    assert audit["tim"]["actual_rth_pct"] == 51.0
    assert audit["tim"]["status"] == "FAIL"
    assert audit["status"] == "FAIL"


def test_schedule_replay_explicitly_disclaims_signal_parity(tmp_path):
    spec, _ = _write_fixture(tmp_path, _events())
    adapter = TopExitReplayAdapter(spec)
    audit = adapter.final_audit(actual_tim_rth_pct=50.0)
    assert audit["signal_parity"] is False
