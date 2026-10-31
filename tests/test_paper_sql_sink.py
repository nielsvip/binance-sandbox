import asyncio
import hashlib

import pytest

from tools.paper_frame_stream import FrameJournal
from tools.paper_journal_session import JournalForwardSession
from tools.paper_scenario_db import ScenarioDB
from tools.paper_sql_sink import PaperSQLSink


def make_db(path):
    db = ScenarioDB(path)
    db.add_path(path_id="p", venue="binance", path_name="paper", switch_name="S", classification="forward", provenance={})
    db.add_scenario(scenario_id="s", path_id="p", side="LONG", parameters={}, created_at="t")
    db.add_run(run_id="r", scenario_id="s", sym_side="BTCUSDC_LONG", host="test", settings_hash="settings", code_hash="code", evidence_kind="independent_forward", status="RUNNING", started_at="t", ended_at=None)
    return db


def test_sink_idempotence_conflicts_raw_payload_and_no_action(tmp_path):
    db = make_db(tmp_path / "db.sqlite")
    sink = PaperSQLSink(db, "r")
    receipt = {"events": [{"action": "OPEN", "timestamp": 123.0, "qty": 2}], "positions": {"BTC": {"entry_price": 42.0}}}
    ids = sink.record_step("123.000000", receipt)
    assert sink.record_step("123.000000", receipt) == ids
    rows = db.query("SELECT * FROM decisions ORDER BY source_ts")
    assert len(rows) == 1
    assert rows[0]["source_ts"] == "123.000000"
    assert rows[0]["payload_json"] == '{"event":{"action":"OPEN","qty":2,"timestamp":123.0},"event_index":0,"positions":{"BTC":{"entry_price":42.0}}}'
    with pytest.raises(ValueError, match="conflict"):
        sink.record_step("123.000000", {"events": [{"action": "OPEN", "qty": 3}], "positions": receipt["positions"]})
    sink.record_step("124", {"events": [], "positions": {"BTC": {"entry_price": 42.0}}})
    no_action = db.query("SELECT action FROM decisions WHERE source_ts='124'")[0]["action"]
    assert no_action == "NO_ACTION"
    db.close()


def test_sink_requires_independent_forward_run(tmp_path):
    db = make_db(tmp_path / "db.sqlite")
    db.add_run(run_id="obs", scenario_id="s", sym_side="BTCUSDC_LONG", host="test", settings_hash="x", code_hash="x", evidence_kind="observational", status="done", started_at="t", ended_at=None)
    with pytest.raises(ValueError, match="independent_forward"):
        PaperSQLSink(db, "obs")
    db.close()


def test_journal_session_calls_sql_sink_after_receipt(tmp_path):
    async def run():
        db = make_db(tmp_path / "db.sqlite")
        code_path = tmp_path / "engine.py"
        code_path.write_text("engine")
        identity = {"settings_sha256": hashlib.sha256(b"settings").hexdigest(), "code_sha256": hashlib.sha256(code_path.read_bytes()).hexdigest(), "scenario_id": "s"}
        journal = FrameJournal(tmp_path / "journal.sqlite", identity)
        journal.append_frame(100, {"ts": 100})

        class Store:
            timestamps = []
            ts_to_idx = {}

        store = Store()

        async def apply_frame(payload, stores):
            store.timestamps.append(100.0)
            store.ts_to_idx[100.0] = 0

        sql_sink = PaperSQLSink(db, "r")
        session = JournalForwardSession(journal, {"BTC": store}, apply_frame, code_path=code_path, mode="crypto", result_sink=sql_sink)
        session.bind([], {"BTC": {"entry_price": 1}})
        stream = session.frames()
        frame = await anext(stream)
        await session.before_step(frame["ts"])
        await session.after_step(frame["ts"])
        stored = db.query("SELECT source_ts,action FROM decisions")
        assert [(row["source_ts"], row["action"]) for row in stored] == [("100.0", "NO_ACTION")]
        assert journal.frames()[0]["receipt"]["positions"]["BTC"]["entry_price"] == 1
        await stream.aclose()
        journal.close()
        db.close()

    asyncio.run(run())
