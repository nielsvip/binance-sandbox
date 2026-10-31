import asyncio
import hashlib
import sys
import types

import pytest

from tools.paper_frame_stream import FrameJournal, historical_or_forward_steps
from tools.paper_journal_session import JournalForwardSession, preserve_source_times


def test_engine_session_exposes_timestamp_preservation():
    result = {"timestamp_1m": "invented", "age_5m": 0, "current_price": 5}
    JournalForwardSession.preserve_source_times(result, {"timestamp_1m": "source"})
    assert result == {"timestamp_1m": "source", "current_price": 5}


class Store:
    def __init__(self):
        self.timestamps = []
        self.ts_to_idx = {}

    def append(self, ts):
        self.ts_to_idx[ts] = len(self.timestamps)
        self.timestamps.append(ts)


def make_journal(tmp_path, code_path):
    code_hash = hashlib.sha256(code_path.read_bytes()).hexdigest()
    return FrameJournal(
        tmp_path / "paper.sqlite3",
        {"settings_sha256": hashlib.sha256(b"settings").hexdigest(), "code_sha256": code_hash, "scenario_id": "scenario"},
    )


def ready_environment(monkeypatch, tmp_path, code_path):
    monkeypatch.setenv("PAPER_FORWARD_ISOLATED", "1")
    monkeypatch.setenv("V8_FORCE_REAL", "1")
    monkeypatch.setenv("V12_LIVE_ONLY_PRODUCERS", "1")
    monkeypatch.setenv("PAPER_TEST_WRITE_ROOT", str(tmp_path))
    isolation = types.ModuleType("paper_worker_isolation")
    isolation.require_installed = lambda write_root=None: {
        "network_denied": True, "write_root": str(write_root or tmp_path)
    }
    monkeypatch.setitem(sys.modules, "tools.paper_worker_isolation", isolation)
    running_engine = types.ModuleType("backtest_v12_engine")
    running_engine.__file__ = str(code_path)
    monkeypatch.setitem(sys.modules, "backtest_v12_engine", running_engine)


def test_session_applies_and_checks_frames_then_records_receipts(tmp_path, monkeypatch):
    async def run():
        code_path = tmp_path / "engine.py"
        code_path.write_text("test engine bytes")
        ready_environment(monkeypatch, tmp_path, code_path)
        journal = make_journal(tmp_path, code_path)
        journal.append_frame(100, {"ts": 100, "price": 5})
        journal.append_frame(200, {"ts": 200, "price": 6})
        store = Store()

        async def apply_frame(payload, stores):
            stores["BTC"].append(payload["ts"])

        session = JournalForwardSession(
            journal, {"BTC": store}, apply_frame,
            code_path=code_path, mode="crypto",
        )
        session.assert_ready("crypto", session.stores)
        events = []
        positions = {"acct:BTC_LONG": {"entry_price": 5.0, "max_gain": 1.2, "opened_at": "t0"}}
        session.bind(events, positions)
        stream = historical_or_forward_steps([999], session)
        assert await anext(stream) == (0, 100.0)
        events.append({"action": "OPEN", "timestamp": 100})
        await anext(stream)
        assert journal.frames()[0]["receipt"] == {
            "events": [{"action": "OPEN", "timestamp": 100}],
            "positions": {"acct:BTC_LONG": {
                "positionAmt": None, "quantity": None, "entry_price": 5.0,
                "max_gain": 1.2, "opened_at": "t0", "entry_ts": None,
                "position_side": None, "symbol": None, "was_reentered": None,
                "was_reduced": None, "is_reduced": None, "is_hedge": None,
                "hedge_for": None, "fixed_stop_price": None,
                "fixed_stop_anchor": None, "fixed_stop_armed": None,
                "exchange_stop_order_id": None,
            }},
        }
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        await session.finish()
        assert journal.frames()[1]["receipt"] is not None
        journal.close()

    asyncio.run(run())


def test_replay_receipt_mismatch_is_refused(tmp_path, monkeypatch):
    async def run():
        code_path = tmp_path / "engine.py"
        code_path.write_text("engine")
        ready_environment(monkeypatch, tmp_path, code_path)
        journal = make_journal(tmp_path, code_path)
        journal.append_frame(100, {"ts": 100})
        journal.record_receipt(100, {"events": [], "positions": {"p": {"entry_price": 1}}})
        store = Store()

        async def apply_frame(payload, stores):
            stores["S"].append(payload["ts"])

        session = JournalForwardSession(
            journal, {"S": store}, apply_frame,
            code_path=code_path, mode="tradier",
        )
        session.bind([], {"p": {"entry_price": 2}})
        iterator = historical_or_forward_steps([], session)
        await anext(iterator)
        with pytest.raises(ValueError, match="conflicting receipt"):
            await anext(iterator)
        journal.close()

    asyncio.run(run())


def test_assert_ready_requires_isolation_markers_and_matching_code(tmp_path, monkeypatch):
    code_path = tmp_path / "engine.py"
    code_path.write_text("engine bytes")
    journal = make_journal(tmp_path, code_path)
    store = Store()

    async def apply_frame(payload, stores):
        return None

    session = JournalForwardSession(
        journal, {"S": store}, apply_frame,
        code_path=code_path, mode="crypto",
    )
    with pytest.raises(RuntimeError, match="environment missing"):
        session.assert_ready("crypto", session.stores)
    ready_environment(monkeypatch, tmp_path, code_path)
    monkeypatch.setenv("V8_VEC_EXACT_INGEST", "1")
    with pytest.raises(RuntimeError, match="vector replay/ingest"):
        session.assert_ready("crypto", session.stores)
    monkeypatch.delenv("V8_VEC_EXACT_INGEST")
    code_path.write_text("changed engine")
    with pytest.raises(RuntimeError, match="code hash"):
        session.assert_ready("crypto", session.stores)
    journal.close()


def test_before_step_rejects_missing_or_future_store_rows(tmp_path, monkeypatch):
    async def run():
        code_path = tmp_path / "engine.py"
        code_path.write_text("engine")
        ready_environment(monkeypatch, tmp_path, code_path)
        journal = make_journal(tmp_path, code_path)
        journal.append_frame(100, {"ts": 100})
        store = Store()
        store.timestamps = [100, 101]
        store.ts_to_idx = {100: 0}

        async def apply_frame(payload, stores):
            return None

        store.timestamps = [101, 100]
        store.ts_to_idx = {100: 1}
        session = JournalForwardSession(
            journal, {"S": store}, apply_frame,
            code_path=code_path, mode="crypto",
        )
        session.bind([], {})
        iterator = historical_or_forward_steps([], session)
        with pytest.raises(RuntimeError, match="future row"):
            await anext(iterator)
        journal.close()

    asyncio.run(run())


def test_assert_ready_rejects_future_rows_already_loaded(tmp_path, monkeypatch):
    code_path = tmp_path / "engine.py"
    code_path.write_text("engine")
    ready_environment(monkeypatch, tmp_path, code_path)
    journal = make_journal(tmp_path, code_path)
    journal.append_frame(100, {"ts": 100})
    store = Store()
    store.timestamps = [90, 100, 101]
    store.ts_to_idx = {90: 0, 100: 1, 101: 2}

    async def apply_frame(payload, stores):
        return None

    session = JournalForwardSession(
        journal, {"S": store}, apply_frame, code_path=code_path, mode="crypto"
    )
    with pytest.raises(RuntimeError, match="future row"):
        session.assert_ready("crypto", session.stores)
    journal.close()


def test_preserve_source_times_restores_exact_fields_and_removes_generated():
    mutated = {
        "timestamp": "rewritten", "timestamp_15m": "rewritten", "ts": 8,
        "_tick_ts": 9, "age_15m": 0, "age_1h": 0, "value": 4,
    }
    original = {"timestamp": "source", "timestamp_15m": "parent", "ts": 1,
                "_tick_ts": 2, "age_15m": 77, "value": 3}
    assert preserve_source_times(mutated, original) is mutated
    assert mutated == {"timestamp": "source", "timestamp_15m": "parent", "ts": 1,
                       "_tick_ts": 2, "age_15m": 77, "value": 4}
