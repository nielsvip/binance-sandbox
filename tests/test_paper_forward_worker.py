import asyncio
import hashlib
import json
from datetime import datetime, timezone

import pytest

from tools.paper_forward_worker import ContinuousJournalForwardSession
from tools.paper_frame_stream import FrameJournal
from tools.paper_snapshot_store import PaperSnapshotStore


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
