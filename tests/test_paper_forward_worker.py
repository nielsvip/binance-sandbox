import json
import asyncio
import hashlib
from datetime import datetime, timezone

import pytest

from tools.paper_forward_worker import ContinuousJournalForwardSession, load_frames
from tools.paper_frame_stream import FrameJournal
from tools.paper_snapshot_store import PaperSnapshotStore


def test_load_frames_keeps_distinct_ordered_native_bars(tmp_path):
    source = tmp_path / "frames.ndjson"
    source.write_text(
        "\n".join(
            json.dumps({"ts": ts, "indicators": {"current_price": price, "timestamp_1m": f"bar-{ts}"}})
            for ts, price in ((1000, 10.0), (1060, 10.1))
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_frames(source, "BTCUSDT") == [
        {"ts": 1000.0, "indicators": {"current_price": 10.0, "timestamp_1m": "bar-1000"}},
        {"ts": 1060.0, "indicators": {"current_price": 10.1, "timestamp_1m": "bar-1060"}},
    ]


def test_load_frames_rejects_one_frame_probe(tmp_path):
    source = tmp_path / "frame.ndjson"
    source.write_text(json.dumps({"ts": 1000, "indicators": {"current_price": 10.0}}), encoding="utf-8")

    with pytest.raises(ValueError, match="at least two distinct"):
        load_frames(source, "BTCUSDT")


def test_load_frames_rejects_duplicate_or_out_of_order_timestamp(tmp_path):
    source = tmp_path / "frames.ndjson"
    source.write_text(
        "\n".join(json.dumps({"ts": ts, "indicators": {"current_price": 10.0}}) for ts in (1000, 1000)) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not increasing"):
        load_frames(source, "BTCUSDT")


def _publish_native_snapshot(snapshot_path, receipt_path, source_ts, captured_ts):
    stamp = datetime.fromtimestamp(source_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    captured = datetime.fromtimestamp(captured_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    raw = json.dumps({"BTCUSDT": {"current_price": 100.0, "timestamp_1m": stamp}}, separators=(",", ":")).encode()
    snapshot_path.write_bytes(raw)
    receipt_path.write_text(json.dumps({"venue": "crypto", "captured_at_utc": captured, "source_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")


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
        snapshot_path=snapshot_path, receipt_path=receipt_path, symbol="BTCUSDT",
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
        snapshot_path=snapshot_path, receipt_path=receipt_path, symbol="BTCUSDT",
        frame_timeframe="1m", required_timeframes=["1m"], poll_seconds=0.001,
        max_age_seconds=180, run_root=output,
    )

    frame, reason = session._read_source_frame()
    assert frame is None
    assert reason == "WAITING_STALE_TF_1m"
    journal.close()
