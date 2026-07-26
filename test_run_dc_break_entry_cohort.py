import json
import sqlite3

import pytest

from tools import path_fleet_campaign as fleet
from tools import run_dc_break_entry_cohort as runner
from tools.run_dc_break_entry_cohort import FAMILY, _claim_exact


def _db(root, status):
    root.mkdir()
    con = sqlite3.connect(root / "queue.db")
    con.executescript(fleet.SCHEMA)
    con.execute(
        """INSERT INTO jobs
           (path_id,kind,priority,status,universe_json,contract_json)
           VALUES (?,?,?,?,?,?)""",
        (FAMILY, "ENTRY", 20, status, json.dumps({}), json.dumps({})),
    )
    con.commit()
    con.close()


def test_claim_exact_can_reconstruct_adapter_required_job(tmp_path):
    root = tmp_path / "fleet"
    _db(root, "ADAPTER_REQUIRED")
    row = _claim_exact(root, "worker")
    assert row["path_id"] == FAMILY
    assert row["status"] == "RUNNING"
    assert row["claimed_by"] == "worker"


def test_claim_exact_refuses_already_screened_job(tmp_path):
    root = tmp_path / "fleet"
    _db(root, "SCREENED")
    with pytest.raises(RuntimeError, match="cannot start"):
        _claim_exact(root, "worker")


def test_result_writer_never_infers_aggregate_as_untouched_oos(
    tmp_path, monkeypatch
):
    captured = []
    monkeypatch.setattr(
        runner.fleet,
        "add_result",
        lambda _root, path: captured.append(json.loads(path.read_text())),
    )
    common = {
        "root": tmp_path,
        "result_dir": tmp_path,
        "job_id": 50,
        "symbol": "MU",
        "side": "LONG",
        "status": "DISCARD_GRAY_RESEARCH_RECONSTRUCTION",
        "strategy": 10.0,
        "bh": 2.0,
        "control": 9.0,
        "tim": 75.0,
        "trades": 3,
        "artifact": "artifact",
        "extra": {},
    }
    runner._add_result(
        **common,
        stage="VEC_NESTED_FOLD_AGGREGATE",
        untouched_oos=False,
    )
    runner._add_result(
        **common,
        stage="VEC_UNTOUCHED_OOS",
        untouched_oos=True,
    )
    assert captured[0]["stage"] == "VEC_NESTED_FOLD_AGGREGATE"
    assert captured[0]["untouched_oos"] is False
    assert captured[1]["stage"] == "VEC_UNTOUCHED_OOS"
    assert captured[1]["untouched_oos"] is True
