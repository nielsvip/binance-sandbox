import json
import sqlite3
from pathlib import Path

from tools.normalize_entry_fleet_metrics import (
    AGGREGATE_STAGE,
    FINAL_STAGE,
    NORMALIZATION_VERSION,
    migrate,
    normalized_payloads,
)


def _artifact() -> dict:
    def fold(index, end, strategy, bh, control, tim, exits, rows):
        return {
            "fold": index,
            "validation": [f"start-{index}", f"end-{index}"],
            "beats_bh": strategy > bh,
            "beats_control": strategy > control,
            "validation_metrics": {
                "end_ts": end,
                "capital_return_pct": strategy,
                "bh_capital_return_pct": bh,
                "exposure_weighted_tim_pct": tim,
                "exit_count": exits,
                "rows": rows,
            },
            "same_frozen_ladder_e02_control": {
                "capital_return_pct": control,
            },
        }

    folds = [
        fold(0, 10, 10, 5, 8, 20, 1, 100),
        fold(1, 20, 30, 10, 25, 40, 2, 200),
        fold(2, 30, 60, 15, 55, 80, 3, 300),
    ]
    return {
        "aggregate": {
            "candidate_capital_return_pct_sum": 100,
            "bh_capital_return_pct_sum": 30,
            "control_capital_return_pct_sum": 88,
            "weighted_tim_pct": (20 * 100 + 40 * 200 + 80 * 300) / 600,
            "future_htf_count": 0,
        },
        "outer_folds": folds,
    }


def _source() -> dict:
    return {
        "id": 7,
        "job_id": 34,
        "symbol": "MU",
        "side": "LONG",
        "stage": "VEC_UNTOUCHED_OOS",
        "status": "DISCARD_GRAY",
        "artifact": "artifact",
    }


def test_normalized_payloads_separate_fold_sum_from_final_oos():
    aggregate, final = normalized_payloads(_source(), _artifact())
    assert aggregate["stage"] == AGGREGATE_STAGE
    assert aggregate["strategy_return_pct"] == 100
    assert aggregate["return_unit"] == "SUM_OF_FOLD_CAPITAL_RETURN_PCT"
    assert aggregate["trades"] == 6
    assert aggregate["untouched_oos"] is False

    assert final["stage"] == FINAL_STAGE
    assert final["strategy_return_pct"] == 60
    assert final["bh_return_pct"] == 15
    assert final["same_entry_control_return_pct"] == 55
    assert final["tim_pct"] == 80
    assert final["trades"] == 3
    assert final["validation_window"] == ["start-2", "end-2"]
    assert final["return_unit"] == "CAPITAL_RETURN_PCT"
    assert final["untouched_oos"] is True


def _fleet(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "reports" / "path_fleet"
    artifact = tmp_path / "artifact"
    root.mkdir(parents=True)
    artifact.mkdir()
    (artifact / "result.json").write_text(json.dumps(_artifact()))
    con = sqlite3.connect(root / "queue.db")
    con.executescript(
        """
        CREATE TABLE jobs (id INTEGER PRIMARY KEY,path_id TEXT);
        CREATE TABLE results (
          id INTEGER PRIMARY KEY,job_id INTEGER,symbol TEXT,side TEXT,
          stage TEXT,status TEXT,strategy_return_pct REAL,bh_return_pct REAL,
          same_entry_control_return_pct REAL,alpha_vs_bh_pp REAL,
          alpha_vs_control_pp REAL,tim_pct REAL,trades INTEGER,
          untouched_oos INTEGER,exact_replay INTEGER,future_htf_count INTEGER,
          artifact TEXT,payload_json TEXT,created_at REAL
        );
        INSERT INTO jobs VALUES(34,'ENTRY_WT_DC');
        """
    )
    source = _source()
    source["artifact"] = str(artifact)
    con.execute(
        """INSERT INTO results VALUES
           (7,34,'MU','LONG','VEC_UNTOUCHED_OOS','DISCARD_GRAY',
            100,30,88,70,12,60,6,1,0,0,?, ?,1)""",
        (str(artifact), json.dumps(source)),
    )
    con.commit()
    con.close()
    return root


def test_migration_is_append_only_and_idempotent(tmp_path):
    root = _fleet(tmp_path)
    first = migrate(root, [34], apply=True)
    assert first["legacy_rows"] == 1
    assert first["inserted_aggregate_rows"] == 1
    assert first["inserted_final_oos_rows"] == 1

    second = migrate(root, [34], apply=True)
    assert second["inserted_aggregate_rows"] == 0
    assert second["inserted_final_oos_rows"] == 0

    con = sqlite3.connect(root / "queue.db")
    rows = con.execute(
        "SELECT id,stage,payload_json FROM results ORDER BY id"
    ).fetchall()
    con.close()
    assert len(rows) == 3
    assert rows[0][0] == 7  # historical row was not rewritten
    corrected = [json.loads(row[2]) for row in rows[1:]]
    assert {row["normalization_version"] for row in corrected} == {
        NORMALIZATION_VERSION
    }
    assert {row["source_legacy_result_id"] for row in corrected} == {7}
