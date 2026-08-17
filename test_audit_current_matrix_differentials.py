import csv
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from tools import audit_current_matrix_differentials as audit


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture(tmp_path: Path):
    (tmp_path / "data" / "reports").mkdir(parents=True)
    manifest = tmp_path / "data" / "param_sweep_manifest_tradier.json"
    registry = tmp_path / "data" / "knob_registry.json"
    dependencies = (
        tmp_path
        / "data"
        / "reports"
        / "SWITCH_MATRIX_INTERDEPENDENCY_20260729.json"
    )
    for path in (manifest, registry, dependencies):
        path.write_text("{}\n")
    (tmp_path / "symbols_trb_long.json").write_text('["MU","NVDA"]\n')
    (tmp_path / "symbols_trb_short.json").write_text("[]\n")

    matrix = tmp_path / "data" / "reports" / "SWITCH_MATRIX_TRB.csv.gz"
    matrix_rows = [
        ["LIVE", "", "false", "control", "OK", "", ""],
        ["LIVE", "", "true", "active", "RECONNECT", "", ""],
        ["DEAD", "", "false", "control", "OK", "", ""],
        ["DEAD", "", "true", "active", "RECONNECT", "", ""],
        ["DISTINCT", "", "false", "control", "OK", "", ""],
        ["DISTINCT", "", "true", "active", "OK", "", ""],
        ["CONTROL", "", "false", "control", "OK", "", ""],
        ["CONTROL", "", "true", "control", "OK", "", ""],
    ]
    with gzip.open(matrix, "wt", newline="") as handle:
        handle.write("# canonical current matrix\n")
        writer = csv.writer(handle)
        writer.writerow(
            [
                "main_switch",
                "sub_setting",
                "value",
                "description",
                "status",
                "MU_LONG",
                "NVDA_LONG",
            ]
        )
        writer.writerows(matrix_rows)

    static_rows = []
    for main, _sub, raw, *_ in matrix_rows:
        static_contract = "READY_EXACT_ONLY"
        if main == "DEAD":
            static_contract = "RECONNECT_REQUIRED_NO_LIVE_DECISION_READ"
        static_rows.append(
            {
                "main_switch": main,
                "sub_setting": "",
                "value": raw,
                "canonical_param": main,
                "intentional_control": main == "CONTROL",
                "static_contract": static_contract,
                "side_applicability": "BOTH_OR_RUNTIME_DEPENDENT",
                "activation_dependencies": [],
            }
        )
    static = tmp_path / "data" / "reports" / "static.json"
    static.write_text(
        json.dumps(
            {
                "scope": {
                    "manifest_sha256": _sha(manifest),
                    "registry_sha256": _sha(registry),
                    "frozen_dependency_inventory_sha256": _sha(dependencies),
                },
                "rows": static_rows,
            }
        )
    )

    db = tmp_path / "data" / "param_results_stocks.db"
    connection = sqlite3.connect(db)
    metric_columns = ", ".join(f"{field} REAL" for field in audit.METRIC_FIELDS)
    connection.execute(
        f"""
        CREATE TABLE param_cells (
          mode TEXT, campaign TEXT, tier TEXT, symbol TEXT, side TEXT,
          param TEXT, value_json TEXT, contract_fingerprint TEXT,
          trades_fingerprint TEXT, {metric_columns}
        )
        """
    )

    def add(param, value, symbol, action_fp, gain, contract="c5-current"):
        values = {
            "trades": 2,
            "acc_gain_pct": gain,
            "gain_per_mo": gain,
            "delta_gain_mo_vs_bh": gain,
            "time_in_mkt_pct": 55,
            "real_closes": 2,
            "mtm_count": 0,
            "opens_long": 2,
            "opens_short": 0,
            "requested_fill_ratio": 1,
            "size_clamp_count": 0,
            "reentry_pending": 0,
            "reentry_violations": 0,
        }
        columns = list(values)
        connection.execute(
            f"""
            INSERT INTO param_cells (
              mode,campaign,tier,symbol,side,param,value_json,
              contract_fingerprint,trades_fingerprint,{",".join(columns)}
            ) VALUES ({",".join("?" for _ in range(9 + len(columns)))})
            """,
            (
                "tradier",
                "current",
                "ENGINE",
                symbol,
                "LONG",
                param,
                json.dumps(value),
                contract,
                action_fp,
                *(values[column] for column in columns),
            ),
        )

    for symbol in ("MU", "NVDA"):
        add("LIVE", False, symbol, "same-action", 1)
        add("LIVE", True, symbol, "same-action", 1)
    add("DEAD", False, "MU", "same-dead", 1)
    add("DEAD", True, "MU", "same-dead", 1)
    add("DISTINCT", False, "MU", "action-a", 1)
    add("DISTINCT", True, "MU", "action-b", 2)
    add("LIVE", True, "MU", "stale-action", 9, contract="old")
    connection.commit()
    connection.close()
    return matrix, static, db


def test_current_only_audit_classifies_collisions_and_excludes_stale(tmp_path):
    matrix, static, db = _write_fixture(tmp_path)
    result = audit.build_audit(
        matrix_path=matrix,
        db_path=db,
        static_path=static,
        campaign="current",
        accepted_by_key={
            "MU_LONG": {"c5-current"},
            "NVDA_LONG": {"c5-current"},
        },
        root=tmp_path,
    )
    states = {row["param"]: row for row in result["params"]}
    assert (
        states["LIVE"]["classification"]
        == "CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT"
    )
    assert (
        states["DEAD"]["classification"]
        == "CONFIRMED_NO_LIVE_DECISION_READ"
    )
    assert (
        states["DISTINCT"]["classification"]
        == "PAIRWISE_DISTINCT_EXACT_PROOF"
    )
    assert states["CONTROL"]["classification"] == "INTENTIONAL_CONTROL"
    assert result["summary"]["excluded_stale_or_foreign_contract_rows"] == 1
    assert result["summary"]["current_c5_exact_rows"] == 0
    assert result["summary"]["current_tradeable_keys"] == 2
    assert (
        result["summary"]["current_matrix_nonactive_key_numeric_cells"] == 0
    )
    assert result["tim_policy"]["top_10_long"] == ["MU", "NVDA"]
    assert result["tim_policy"]["current_exact_row_counts"]["pass"] == 8
    assert not result["verdict"]["every_actionable_row_currently_proven"]


def test_static_metadata_drift_fails_closed(tmp_path):
    matrix, static, db = _write_fixture(tmp_path)
    (tmp_path / "data" / "knob_registry.json").write_text('{"changed":true}\n')
    try:
        audit.build_audit(
            matrix_path=matrix,
            db_path=db,
            static_path=static,
            campaign="current",
            accepted_by_key={},
            root=tmp_path,
        )
    except RuntimeError as exc:
        assert "STATIC_AUTHORITY_DRIFT" in str(exc)
    else:
        raise AssertionError("changed static authority must be rejected")
