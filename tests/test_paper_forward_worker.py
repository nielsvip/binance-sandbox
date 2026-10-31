import asyncio
import hashlib
import json
import sqlite3
import zlib
from datetime import datetime, timezone

import pytest

from tools.paper_forward_worker import ContinuousJournalForwardSession
from tools.paper_frame_stream import FrameJournal
from tools.paper_snapshot_store import PaperSnapshotStore
from tools.paper_native_journal import _connect


def _publish_native_snapshot(snapshot_path, receipt_path, source_ts, captured_ts):
    stamp = datetime.fromtimestamp(source_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    captured = datetime.fromtimestamp(captured_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    raw = json.dumps({"BTCUSDT": {"current_price": 100.0, "timestamp_1m": stamp}}, separators=(",", ":")).encode()
    snapshot_path.write_bytes(raw)
    receipt_path.write_text(json.dumps({"host": "s1", "venue": "crypto", "captured_at_utc": captured, "source_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")


def test_continuous_session_waits_for_new_source_timestamp_and_snapshots_sql(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr("tools.paper_forward_worker.time.time", lambda: now)
    snapshot_path = tmp_path / "crypto.json"
    receipt_path = tmp_path / "crypto.receipt.json"
    _publish_native_snapshot(snapshot_path, receipt_path, now - 120, now)
    output = tmp_path / "run"
    output.mkdir(mode=0o700)
    identity = {"scenario_id": "fixture", "settings_sha256": "a" * 64, "code_sha256": "b" * 64, "run_id": "r", "epoch_id": "e", "sym_side": "BTCUSDT_LONG"}
    journal = FrameJournal(output / "receipts.sqlite3", identity)
    stores = {}

    async def apply_frame(payload, store_map):
        packed = payload["BTCUSDT"]
        store_map["BTCUSDT"].append(session._current["ts"], packed["indicators"])

    session = ContinuousJournalForwardSession(
        journal, stores, apply_frame, code_path=tmp_path / "engine.py", mode="crypto",
        snapshot_path=snapshot_path, receipt_path=receipt_path, symbol="BTCUSDT", expected_host="s1",
        frame_timeframe="1m", required_timeframes=["1m"], poll_seconds=0.001,
        max_age_seconds=180, run_root=output, max_frames=4,
    )

    async def exercise():
        first = await session.prime_first_frame()
        stores["BTCUSDT"] = PaperSnapshotStore([{"ts": first["ts"], "indicators": first["indicators"]}])
        session.stores = stores
        session.bind([], {})
        stream = session.frames()
        row1 = await stream.__anext__()
        await session.before_step(row1["ts"])
        await session.after_step(row1["ts"])
        _publish_native_snapshot(snapshot_path, receipt_path, now - 60, now)
        row2 = await stream.__anext__()
        await session.before_step(row2["ts"])
        await session.after_step(row2["ts"])
        session.stop_requested = True
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
        assert [row["ts"] for row in journal.frames()] == [now - 120, now - 60]
        assert all(row["receipt_hash"] for row in journal.frames())
        assert (output / "receipts.snapshot.db").is_file()

    asyncio.run(exercise())
    journal.close()


def test_continuous_session_reports_stale_input_as_waiting(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr("tools.paper_forward_worker.time.time", lambda: now)
    snapshot_path = tmp_path / "crypto.json"
    receipt_path = tmp_path / "crypto.receipt.json"
    _publish_native_snapshot(snapshot_path, receipt_path, now - 600, now)
    output = tmp_path / "run"
    output.mkdir()
    journal = FrameJournal(output / "receipts.sqlite3", {"scenario_id": "fixture", "settings_sha256": "a" * 64, "code_sha256": "b" * 64})
    session = ContinuousJournalForwardSession(
        journal, {}, lambda *_: None, code_path=tmp_path / "engine.py", mode="crypto",
        snapshot_path=snapshot_path, receipt_path=receipt_path, symbol="BTCUSDT", expected_host="s1",
        frame_timeframe="1m", required_timeframes=["1m"], poll_seconds=0.001,
        max_age_seconds=180, run_root=output,
    )

    async def stop_after_wait(_seconds):
        session.stop_requested = True

    monkeypatch.setattr("tools.paper_forward_worker.asyncio.sleep", stop_after_wait)
    with pytest.raises(RuntimeError, match="stop requested before a fresh first source frame"):
        asyncio.run(session.prime_first_frame())
    status = json.loads((output / "worker.status.json").read_text(encoding="utf-8"))
    assert status["status"] == "WAITING_FRESH_INPUT"
    assert status["reason"] == "WAITING_STALE_TF_1m"
    journal.close()


def test_source_journal_reader_verifies_zlib_payload_and_cursor(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr("tools.paper_forward_worker.time.time", lambda: now)
    source_path = tmp_path / "native.sqlite3"
    source = _connect(source_path)
    indicators = {"timestamp_1m": datetime.fromtimestamp(now - 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "current_price": 100.0}
    raw = json.dumps(indicators, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    source.execute("INSERT INTO payloads(input_hash,payload_encoding,payload_blob,raw_bytes,compressed_bytes) VALUES(?,?,?,?,?)", (digest, "zlib", zlib.compress(raw), len(raw), len(zlib.compress(raw))))
    source.execute("INSERT INTO frame_index(observed_ts,source_ts,symbol,venue,host,timeframe,source_sha256,input_hash) VALUES(?,?,?,?,?,?,?,?)", (now, now - 60, "BTCUSDT", "crypto", "s1", "1m", "c" * 64, digest))
    source.execute("INSERT INTO journal_meta(key,value) VALUES('latest_sequence','1')")
    source.commit()
    source.close()
    output = tmp_path / "run"
    output.mkdir()
    identity = {"scenario_id": "fixture", "settings_sha256": "a" * 64, "code_sha256": "b" * 64}
    journal = FrameJournal(output / "receipts.sqlite3", identity)
    session = ContinuousJournalForwardSession(
        journal, {}, lambda *_: None, code_path=tmp_path / "engine.py", mode="crypto",
        snapshot_path=tmp_path / "unused.json", receipt_path=tmp_path / "unused.receipt.json",
        source_journal_path=source_path, symbol="BTCUSDT", expected_host="s1",
        frame_timeframe="1m", required_timeframes=["1m"], poll_seconds=0.5,
        max_age_seconds=180, run_root=output,
    )
    frame, reason = session._read_source_journal_frame()
    assert reason is None
    assert frame["source_sequence"] == 1
    assert frame["indicators"] == indicators
    assert frame["observation"]["source_sha256"] == "c" * 64
    session._source_db.close()
    journal.close()


def test_observed_clock_uses_capture_time_and_preserves_native_source_time(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr("tools.paper_forward_worker.time.time", lambda: now)
    source_path = tmp_path / "native.sqlite3"
    source = _connect(source_path)
    observed_ts = now - 60
    source_ts = observed_ts - 60
    indicators = {
        "timestamp_1m": datetime.fromtimestamp(source_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "timestamp_3m": datetime.fromtimestamp(source_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "current_price": 100.0,
    }
    raw = json.dumps(indicators, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    compressed = zlib.compress(raw)
    provenance = json.dumps({"schema_version": 1, "timeframe": "1m", "status": "VERIFIED_NATIVE", "source_field": "timestamp_1m", "producer_source_sha256": "f" * 64, "receipt_verified_native_1m": True}, sort_keys=True, separators=(",", ":"))
    source.execute("INSERT INTO payloads(input_hash,payload_encoding,payload_blob,raw_bytes,compressed_bytes) VALUES(?,?,?,?,?)", (digest, "zlib", compressed, len(raw), len(compressed)))
    source.execute("INSERT INTO frame_index(sequence,observed_ts,source_ts,symbol,venue,host,timeframe,source_sha256,input_hash,provenance_json) VALUES(?,?,?,?,?,?,?,?,?,?)", (1, observed_ts, source_ts, "BTCUSDT", "crypto", "s1", "1m", "c" * 64, digest, provenance))
    source.execute("INSERT INTO source_conflicts(observed_ts,source_ts,symbol,venue,host,timeframe,existing_input_hash,incoming_input_hash,reason) VALUES(?,?,?,?,?,?,?,?,?)", (observed_ts + 30, source_ts, "BTCUSDT", "crypto", "s1", "1m", digest, "d" * 64, "SAME_SOURCE_TIMESTAMP_CHANGED_PAYLOAD"))
    source.execute("INSERT INTO journal_meta(key,value) VALUES('latest_sequence','1')")
    source.commit()
    source.close()
    output = tmp_path / "run"
    output.mkdir()
    journal = FrameJournal(output / "receipts.sqlite3", {"scenario_id": "fixture", "settings_sha256": "a" * 64, "code_sha256": "b" * 64})
    session = ContinuousJournalForwardSession(
        journal, {}, lambda *_: None, code_path=tmp_path / "engine.py", mode="crypto",
        snapshot_path=tmp_path / "unused.json", receipt_path=tmp_path / "unused.receipt.json",
        source_journal_path=source_path, symbol="BTCUSDT", expected_host="s1",
        frame_timeframe="1m", required_timeframes=["1m"], poll_seconds=0.5,
        max_age_seconds=180, run_root=output, decision_clock="observed_at_utc",
    )
    frame, reason = session._read_source_journal_frame()
    assert reason is None
    assert frame["ts"] == observed_ts
    assert frame["observation"]["source_timestamp_epoch_s"] == source_ts
    assert session.source_revision_count == 1
    payload = {"BTCUSDT": {"indicators": indicators, "observation": frame["observation"]}}
    session._accumulate_frame_provenance({"ts": frame["ts"], "payload": payload})
    assert session.observation_clock_verified is True
    assert session.source_alias_verified is True
    session._source_db.close()
    journal.close()


def test_source_journal_reader_fails_closed_after_retention_floor_advances(tmp_path):
    source_path = tmp_path / "native.sqlite3"
    source = _connect(source_path)
    source.execute("UPDATE journal_meta SET value='4' WHERE key='retention_floor_sequence'")
    source.commit()
    source.close()
    output = tmp_path / "run"
    output.mkdir()
    journal = FrameJournal(output / "receipts.sqlite3", {"scenario_id": "fixture", "settings_sha256": "a" * 64, "code_sha256": "b" * 64})
    session = ContinuousJournalForwardSession(
        journal, {}, lambda *_: None, code_path=tmp_path / "engine.py", mode="crypto",
        snapshot_path=tmp_path / "unused.json", receipt_path=tmp_path / "unused.receipt.json",
        source_journal_path=source_path, symbol="BTCUSDT", expected_host="s1",
        frame_timeframe="1m", required_timeframes=["1m"], poll_seconds=0.5,
        max_age_seconds=180, run_root=output,
    )
    with pytest.raises(RuntimeError, match="ARCHIVE_GAP"):
        session._read_source_journal_frame()
    journal.close()


def test_source_journal_experiment_window_selects_bounds_and_completes(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr("tools.paper_forward_worker.time.time", lambda: now)
    source_path = tmp_path / "native.sqlite3"
    source = _connect(source_path)
    for index, source_ts in enumerate((now - 120, now - 60), start=1):
        indicators = {"timestamp_1m": datetime.fromtimestamp(source_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "current_price": 100.0 + index}
        raw = json.dumps(indicators, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        compressed = zlib.compress(raw)
        source.execute("INSERT INTO payloads(input_hash,payload_encoding,payload_blob,raw_bytes,compressed_bytes) VALUES(?,?,?,?,?)", (digest, "zlib", compressed, len(raw), len(compressed)))
        source.execute("INSERT INTO frame_index(sequence,observed_ts,source_ts,symbol,venue,host,timeframe,source_sha256,input_hash) VALUES(?,?,?,?,?,?,?,?,?)", (index, now, source_ts, "BTCUSDT", "crypto", "s1", "1m", "d" * 64, digest))
    source.execute("INSERT INTO journal_meta(key,value) VALUES('latest_sequence','2')")
    source.commit()
    source.close()
    output = tmp_path / "run"
    output.mkdir()
    journal = FrameJournal(output / "receipts.sqlite3", {"scenario_id": "fixture", "settings_sha256": "a" * 64, "code_sha256": "b" * 64})
    session = ContinuousJournalForwardSession(
        journal, {}, lambda *_: None, code_path=tmp_path / "engine.py", mode="crypto",
        snapshot_path=tmp_path / "unused.json", receipt_path=tmp_path / "unused.receipt.json",
        source_journal_path=source_path, experiment_window={"window_id": "fixed", "source_timeframe": "1m", "start_source_ts": now - 180, "end_source_ts": now - 60},
        symbol="BTCUSDT", expected_host="s1", frame_timeframe="1m", required_timeframes=["1m"],
        poll_seconds=0.5, max_age_seconds=180, run_root=output,
    )
    first, reason = session._read_source_journal_frame()
    assert reason is None and first["source_sequence"] == 1
    assert session.coverage_gaps[0]["missing_seconds"] == 60
    session._source_cursor = first["source_sequence"]
    second, reason = session._read_source_journal_frame()
    assert reason is None and second["source_sequence"] == 2
    session._source_cursor = second["source_sequence"]
    final, reason = session._read_source_journal_frame()
    assert final is None and reason == "SOURCE_WINDOW_COMPLETE"
    assert session._window_complete
    journal.close()


def test_immutable_window_missing_required_timeframe_terminates_data_gap(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr("tools.paper_forward_worker.time.time", lambda: now)
    source_path = tmp_path / "native.sqlite3"
    source = _connect(source_path)
    source_ts = now - 60
    indicators = {"timestamp_1m": datetime.fromtimestamp(source_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    raw = json.dumps(indicators, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    compressed = zlib.compress(raw)
    source.execute("INSERT INTO payloads(input_hash,payload_encoding,payload_blob,raw_bytes,compressed_bytes) VALUES(?,?,?,?,?)", (digest, "zlib", compressed, len(raw), len(compressed)))
    source.execute("INSERT INTO frame_index(sequence,observed_ts,source_ts,symbol,venue,host,timeframe,source_sha256,input_hash) VALUES(?,?,?,?,?,?,?,?,?)", (1, now, source_ts, "BTCUSDT", "crypto", "s1", "1m", "e" * 64, digest))
    source.commit()
    source.close()
    output = tmp_path / "run"
    output.mkdir()
    journal = FrameJournal(output / "receipts.sqlite3", {"scenario_id": "fixture", "settings_sha256": "a" * 64, "code_sha256": "b" * 64})
    session = ContinuousJournalForwardSession(
        journal, {}, lambda *_: None, code_path=tmp_path / "engine.py", mode="crypto",
        snapshot_path=tmp_path / "unused.json", receipt_path=tmp_path / "unused.receipt.json",
        source_journal_path=source_path, experiment_window={"window_id": "fixed-gap", "source_timeframe": "1m", "start_source_ts": source_ts, "end_source_ts": source_ts},
        symbol="BTCUSDT", expected_host="s1", frame_timeframe="1m", required_timeframes=["1m", "3m"],
        poll_seconds=0.5, max_age_seconds=180, run_root=output,
    )
    frame = asyncio.run(session.prime_first_frame())
    assert frame is None
    assert session.wait_reason == "DATA_GAP_MISSING_TF_3m"
    status = json.loads((output / "worker.status.json").read_text())
    assert status["status"] == "DATA_GAP"
    journal.close()
