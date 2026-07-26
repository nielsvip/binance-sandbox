import json
import sqlite3

from tools.path_fleet_gr_opposite_worker import (
    PATH_ID,
    _mark_prior_zero_exit_red,
)


def test_worker_is_bound_to_gr_opposite_exit_family():
    assert PATH_ID == "EXIT_GR_OPPOSITE"


def test_prior_zero_exit_rows_are_red_diagnostics(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "queue.db")
    con.execute(
        """CREATE TABLE results(
           id INTEGER PRIMARY KEY,job_id INTEGER,stage TEXT,trades INTEGER,
           status TEXT,payload_json TEXT)"""
    )
    con.execute(
        "INSERT INTO results VALUES(1,41,'VEC_UNTOUCHED_OOS',0,?,?)",
        ("GRAY_REJECTED", json.dumps({"status": "GRAY_REJECTED"})),
    )
    con.execute(
        "INSERT INTO results VALUES(2,41,'VEC_UNTOUCHED_OOS',3,?,?)",
        ("GRAY_REJECTED", json.dumps({"status": "GRAY_REJECTED"})),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(
        "tools.path_fleet_gr_opposite_worker.fleet.write_report",
        lambda root: root / "PROGRESS.md",
    )
    assert _mark_prior_zero_exit_red(tmp_path, 41) == 1
    con = sqlite3.connect(tmp_path / "queue.db")
    rows = con.execute(
        "SELECT status,payload_json FROM results ORDER BY id"
    ).fetchall()
    con.close()
    assert rows[0][0] == "RED_DIAGNOSTIC_INERT"
    assert json.loads(rows[0][1])["inert"] is True
    assert rows[1][0] == "GRAY_REJECTED"
