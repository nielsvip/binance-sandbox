import gzip
import hashlib
import json

import pytest

from tools.v8_research_top_exit_adapter import (
    ResearchReplayError,
    SPEC_KIND,
    TopExitReplayAdapter,
)


def _write_fixture(tmp_path, events):
    npz = tmp_path / "MU.npz"
    npz.write_bytes(b"frozen-npz")
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
        "seed_notional_usd": 2000.0,
        "commission_round_trip_pct": 0.10,
        "expected_gain_pct": 9.79,
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
