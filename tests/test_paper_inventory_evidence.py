import json
import sqlite3

from tools.paper_inventory_evidence import ingest_evidence
from tools.paper_scenario_db import ScenarioDB


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_ingest_is_idempotent_and_keeps_source_times_and_evidence_types(tmp_path):
    root = tmp_path / "repo"
    base = root / "data" / "paper_forward_comprehensive"
    _write_json(base / "inventory.json", {
        "PATH_A": {"gate_knob": "SWITCH_A", "kind": "entry", "venue": "crypto", "tf": "15m"}
    })
    _write_json(base / "scores.json", {
        "generated_at": "2026-10-31T02:46:53Z",
        "per_sym": [{"path": "PATH_A", "venue": "crypto", "kind": "entry",
                     "sym_side": "BTCUSDC_LONG", "events": 4, "scored": 3,
                     "pending": 1, "mean_delta": 0.125, "pos": True}],
        "paths": [{"path": "PATH_A", "venue": "crypto", "kind": "entry",
                   "events": 4, "scored": 3, "pooled_delta": 0.25,
                   "days": 8, "verdict": "WATCH"}],
    })
    shadow_path = root / "data" / "paper_shadow" / "shadow.db"
    shadow_path.parent.mkdir(parents=True)
    source = sqlite3.connect(shadow_path)
    source.execute("CREATE TABLE decisions(ts REAL,kind TEXT,leg TEXT,sym_side TEXT,venue TEXT,action TEXT,reason TEXT,ref TEXT,p_vec REAL,p_shadow REAL,lead_s REAL,edge_bps REAL,pending INTEGER,horizon_bars INTEGER,window_days INTEGER)")
    source.execute("INSERT INTO decisions VALUES(1700000000,'shadow_entry','shadow_5m','ETHUSDT_SHORT','crypto','OPEN','REASON_A','ref',1,2,3,4,0,16,14)")
    source.commit()
    source.close()

    db = ScenarioDB(tmp_path / "inventory.sqlite")
    counts = ingest_evidence(db, root)
    assert counts == {"inventory_paths": 1, "per_sym_results": 1,
                      "path_summary_results": 1, "shadow_decisions": 1, "skipped_sources": 0}
    assert db.query("SELECT COUNT(*) n FROM trades")[0]["n"] == 0
    result = db.query("SELECT computed_at,metric_name,metric_value,unit,payload_json FROM results ORDER BY metric_name")[0]
    assert result["computed_at"] == "2026-10-31T02:46:53Z"
    assert (result["metric_name"], result["metric_value"], result["unit"]) == ("mean_delta", 0.125, "fraction")
    assert json.loads(result["payload_json"])["settings"] == "UNRECORDED"
    decision = db.query("SELECT source_ts,payload_json FROM decisions")[0]
    assert decision["source_ts"] == "1700000000.0"
    assert json.loads(decision["payload_json"])["execution_status"] == "decision_only"
    assert db.query("SELECT DISTINCT evidence_kind FROM runs ORDER BY evidence_kind")[0][0] == "observational"
    assert db.query("SELECT COUNT(*) n FROM runs WHERE evidence_kind='timing_shadow'")[0]["n"] == 1

    again = ingest_evidence(db, root)
    assert again == {"inventory_paths": 0, "per_sym_results": 0,
                     "path_summary_results": 0, "shadow_decisions": 0, "skipped_sources": 3}
    assert db.query("SELECT COUNT(*) n FROM results")[0]["n"] == 2
    assert db.query("SELECT COUNT(*) n FROM decisions")[0]["n"] == 1
    db.close()
