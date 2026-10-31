import asyncio

import pytest

from tools.paper_frame_stream import (
    FrameJournal,
    historical_or_forward_steps,
)


def test_historical_sequence_is_enumerated_without_mutation():
    async def run():
        timeline = [11, 22, 33]
        result = [item async for item in historical_or_forward_steps(timeline)]
        assert result == [(0, 11), (1, 22), (2, 33)]
        assert timeline == [11, 22, 33]

    asyncio.run(run())


class Session:
    def __init__(self, frames):
        self.rows = iter(frames)
        self.calls = []

    async def frames(self):
        for frame in self.rows:
            yield frame

    async def before_step(self, ts):
        self.calls.append(("before", ts))

    async def after_step(self, ts):
        self.calls.append(("after", ts))

    async def finish(self):
        self.calls.append(("finish",))


def test_forward_steps_update_then_commit_on_next_advance():
    async def run():
        session = Session([{"ts": 101}, {"ts": 116}])
        timeline = [1, 2]
        iterator = historical_or_forward_steps(timeline, session)
        assert await anext(iterator) == (0, 101.0)
        assert session.calls == [("before", 101.0)]
        assert await anext(iterator) == (1, 116.0)
        assert session.calls == [("before", 101.0), ("after", 101.0), ("before", 116.0)]
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)
        assert session.calls[-1] == ("after", 116.0)
        assert timeline == [101.0, 116.0]
        await session.finish()
        assert session.calls[-1] == ("finish",)

    asyncio.run(run())


def test_failed_body_does_not_commit_pending_step():
    async def run():
        session = Session([{"ts": 101}, {"ts": 116}])
        iterator = historical_or_forward_steps([], session)
        await anext(iterator)
        await iterator.aclose()
        assert session.calls == [("before", 101.0)]

    asyncio.run(run())


def test_journal_inputs_are_durable_idempotent_and_conflict_checked(tmp_path):
    path = tmp_path / "frames.sqlite3"
    identity = {"settings_sha256": "settings-a", "code_sha256": "code-a", "scenario_id": "paper-a"}
    journal = FrameJournal(path, identity)
    assert journal.append_frame(101, {"symbol": "BTCUSDT", "close": 5}) == 1
    assert journal.append_frame(101, {"symbol": "BTCUSDT", "close": 5}) == 1
    with pytest.raises(ValueError, match="conflicting duplicate"):
        journal.append_frame(101, {"symbol": "BTCUSDT", "close": 6})
    with pytest.raises(ValueError, match="strictly increasing"):
        journal.append_frame(100, {"symbol": "ETHUSDT", "close": 3})
    receipt = {"events": [{"action": "OPEN", "ts": 101}]}
    receipt_hash = journal.record_receipt(101, receipt)
    assert journal.record_receipt(101, receipt) == receipt_hash
    with pytest.raises(ValueError, match="conflicting receipt"):
        journal.record_receipt(101, {"events": []})
    assert journal.frames() == [{
        "sequence": 1,
        "ts": 101.0,
        "payload": {"close": 5, "symbol": "BTCUSDT"},
        "input_hash": journal.frames()[0]["input_hash"],
        "receipt_hash": receipt_hash,
        "receipt": receipt,
    }]
    journal.close()


def test_journal_identity_change_refuses_resume(tmp_path):
    path = tmp_path / "frames.sqlite3"
    journal = FrameJournal(path, {"settings_sha256": "a", "code_sha256": "x", "scenario_id": "paper"})
    journal.append_frame(101, {"close": 5})
    journal.close()
    with pytest.raises(ValueError, match="identity mismatch"):
        FrameJournal(path, {"settings_sha256": "b", "code_sha256": "x", "scenario_id": "paper"})


def test_journal_validates_identity_and_rejects_bool_timestamps(tmp_path):
    with pytest.raises(ValueError, match="requires nonempty"):
        FrameJournal(tmp_path / "missing.sqlite3", {"settings_sha256": "a", "code_sha256": "x"})
    journal = FrameJournal(
        tmp_path / "frames.sqlite3",
        {"settings_sha256": "a", "code_sha256": "x", "scenario_id": "paper"},
    )
    with pytest.raises(ValueError, match="not bool"):
        journal.append_frame(True, {"close": 1})
    with pytest.raises(ValueError, match="not bool"):
        journal.record_receipt(True, {})
    journal.close()


def test_two_connections_serialize_duplicate_and_receipt_writes(tmp_path):
    path = tmp_path / "frames.sqlite3"
    identity = {"settings_sha256": "a", "code_sha256": "x", "scenario_id": "paper"}
    first = FrameJournal(path, identity)
    second = FrameJournal(path, identity)
    assert first.append_frame(101, {"close": 5}) == 1
    assert second.append_frame(101, {"close": 5}) == 1
    with pytest.raises(ValueError, match="conflicting duplicate"):
        second.append_frame(101, {"close": 6})
    receipt = {"events": ["open"]}
    first.record_receipt(101, receipt)
    assert second.record_receipt(101, receipt) == first.frames()[0]["receipt_hash"]
    with pytest.raises(ValueError, match="conflicting receipt"):
        second.record_receipt(101, {"events": ["close"]})
    assert second.frames()[0]["receipt"] == receipt
    first.close()
    second.close()
