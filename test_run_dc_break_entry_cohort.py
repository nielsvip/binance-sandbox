import json
import sqlite3

import pytest

from tools import path_fleet_campaign as fleet
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
