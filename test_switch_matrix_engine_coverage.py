import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from tools import export_switch_matrix_xls as export
from tools import persym_baseline_campaign as campaign


def _param_db(path: Path):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE param_cells(
        mode TEXT,symbol TEXT,side TEXT,campaign TEXT,param TEXT,value_json TEXT,
        validation_status TEXT,contract_fingerprint TEXT,ts TEXT,source_file TEXT,
        tier TEXT)"""
    )
    return con


def test_engine_coverage_separates_current_stale_and_vec(tmp_path):
    db = tmp_path / "params.db"
    con = _param_db(db)
    rows = [
        ("tradier", "MU", "LONG", export.CURRENT_ENGINE_CAMPAIGN, "A", "true",
         "PASS", "current", "2026-07-27T00:00:00Z", "engine", "ENGINE"),
        ("tradier", "MU", "LONG", export.CURRENT_ENGINE_CAMPAIGN, "B", "2",
         "PASS", "old", "2026-07-27T00:00:01Z", "engine", "ENGINE"),
        ("tradier", "MU", "LONG", export.CURRENT_ENGINE_CAMPAIGN, "C", "3",
         "FAIL", "current", "2026-07-27T00:00:02Z", "engine", "ENGINE"),
        ("tradier", "VT", "LONG", "vec", "D", "4",
         None, None, "2026-07-27T00:00:03Z", "vec", "VEC"),
    ]
    con.executemany(
        "INSERT INTO param_cells VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.commit()
    con.close()

    with patch.object(export, "DB", db), patch.object(export, "BASE", tmp_path), patch.object(
        campaign, "matrix_contract_fingerprint", return_value="current"
    ):
        audit = export.load_engine_coverage(export.CURRENT_ENGINE_CAMPAIGN)

    assert audit["raw_engine_rows"] == 3
    assert audit["current_engine_rows"] == 1
    assert audit["stale_engine_rows"] == 1
    assert audit["invalid_engine_rows"] == 1
    assert ("B", "2", "MU_LONG") in audit["stale"]
    assert ("C", "3", "MU_LONG") in audit["invalid"]
    assert ("D", "4", "VT_LONG") in audit["vec_only"]


def test_path_fleet_exact_requires_explicit_switch_attribution(tmp_path):
    db = tmp_path / "params.db"
    con = _param_db(db)
    con.close()
    fleet_dir = tmp_path / "data" / "reports" / "path_fleet"
    fleet_dir.mkdir(parents=True)
    fleet = sqlite3.connect(fleet_dir / "queue.db")
    fleet.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY,path_id TEXT)")
    fleet.execute(
        """CREATE TABLE results(
        job_id INTEGER,symbol TEXT,side TEXT,stage TEXT,status TEXT,
        strategy_return_pct REAL,bh_return_pct REAL,
        same_entry_control_return_pct REAL,payload_json TEXT,artifact TEXT,
        created_at REAL,exact_replay INTEGER)"""
    )
    fleet.execute("INSERT INTO jobs VALUES(1,'ENTRY_LADDER_GREEN')")
    fleet.executemany(
        "INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "TTD", "SHORT", "V8_EXACT_REPLAY", "RESEARCH_ONLY",
             280.0, 54.0, 280.0, json.dumps({"matrix_written": False}),
             "control", 1.0, 1),
            (1, "MU", "LONG", "V8_EXACT_REPLAY", "PASS",
             120.0, 20.0, 80.0,
             json.dumps({
                 "attributable": True, "matrix_param": "EXIT_X",
                 "matrix_value": True,
             }), "candidate", 2.0, 1),
        ],
    )
    fleet.commit()
    fleet.close()

    with patch.object(export, "DB", db), patch.object(
        export, "FLEET_DB", fleet_dir / "queue.db"
    ):
        audit = export.load_engine_coverage(export.CURRENT_ENGINE_CAMPAIGN)

    assert len(audit["fleet_exact"]) == 2
    assert audit["fleet_exact_attributable"] == 1
    assert audit["fleet_exact"][0]["attributable"] is False
    assert audit["fleet_exact"][1]["matrix_param"] == "EXIT_X"


def test_contract_fingerprint_cache_invalidates_when_source_changes(tmp_path):
    (tmp_path / "engine.py").write_text("version=1\n")
    npz_dir = tmp_path / "npz"
    npz_dir.mkdir()
    (npz_dir / "MU.npz").write_bytes(b"frozen-data")
    campaign._matrix_contract_fingerprint_cached.cache_clear()
    with patch.object(campaign, "SBX", tmp_path), patch.object(
        campaign, "MATRIX_CONTRACT_FILES", ["engine.py"]
    ), patch.object(campaign, "MATRIX_NPZ_DIR", npz_dir):
        first = campaign.matrix_contract_fingerprint("MU", "LONG")
        (tmp_path / "engine.py").write_text("version=2-longer\n")
        second = campaign.matrix_contract_fingerprint("MU", "LONG")
    campaign._matrix_contract_fingerprint_cached.cache_clear()
    assert first != second


def test_current_engine_pack_rows_are_visible_but_vec_unknowns_are_not():
    manifest = [("KNOWN_SWITCH", "true")]
    exact_cells = {
        ("STOP_PACK", "bb_frozen_d", "VT_LONG"): -0.57,
        ("KNOWN_SWITCH", "false", "VT_LONG"): 1.0,
    }
    engine_rows = export.include_observed_rows(
        manifest, exact_cells, "ENGINE"
    )
    vec_rows = export.include_observed_rows(manifest, exact_cells, "VEC")

    assert ("STOP_PACK", "bb_frozen_d") in engine_rows
    assert ("KNOWN_SWITCH", "false") in engine_rows
    assert ("STOP_PACK", "bb_frozen_d") not in vec_rows
    assert ("KNOWN_SWITCH", "false") in vec_rows


def test_historical_campaign_cannot_overwrite_canonical_matrix_filename():
    assert export.output_suffix(
        "ENGINE", export.CURRENT_ENGINE_CAMPAIGN
    ) == ""
    assert export.output_suffix(
        "ENGINE", "stocks_baseline_v2_s4h"
    ) == "_ENGINE_HIST_STOCKS_BASELINE_V2_S4H"
    assert export.output_suffix(
        "VEC", "stocks_baseline_v2_s4h"
    ) == "_VEC_DIAGNOSTIC"
