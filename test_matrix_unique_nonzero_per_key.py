from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

from tools.export_switch_matrix_xls import load_recovered_pilot_v8_cells
from tools.recover_pilot_v8_cells_s1 import canonical_value
from tools.recover_pilot_v8_cells_s1 import select_authoritative_rows


ROOT = Path(__file__).resolve().parent


def test_recovery_canonicalizes_boolean_and_numeric_value_aliases() -> None:
    assert canonical_value("True") == canonical_value("true") == "true"
    assert canonical_value("1") == canonical_value("1.0") == "1"


def test_recovery_preserves_superseded_contract_row_and_selects_explicit_generation():
    common = {
        "symbol": "TTD", "side": "SHORT", "param": "ENTRY_ZONE_SHORT",
        "campaign": "stocks_repaired_20260725_c2", "validation_status": "PASS",
        "ts": "2026-07-31T01:18:22Z", "delta_gain_mo_vs_bh": -2.8242,
    }
    c2 = {
        **common, "value_json": "30", "overrides_json": '{"ENTRY_ZONE_SHORT":30}',
        "contract_fingerprint": "tradier-matrix-c2-20260725:old",
        "trades_fingerprint": "old-actions", "source_file": "c2/30",
    }
    c3 = {
        **common, "value_json": "30.0", "overrides_json": '{"ENTRY_ZONE_SHORT":30.0}',
        "contract_fingerprint": "tradier-matrix-exec-c3-20260729:new",
        "trades_fingerprint": "new-actions", "source_file": "c3/30.0",
    }
    selected, history, audit = select_authoritative_rows([c2, c3])
    assert len(selected) == 1
    assert selected[0]["contract_fingerprint"].startswith("tradier-matrix-exec-c3")
    assert len(history) == 2
    assert {row["authority_selection_status"] for row in history} == {
        "SELECTED_DISPLAY_AUTHORITY", "PRESERVED_SUPERSEDED_AUTHORITY",
    }
    assert audit["preserved_superseded_rows"] == 1
    assert audit["query_order_used_as_authority"] is False


def test_recovery_fails_closed_on_same_priority_material_identity_conflict():
    common = {
        "symbol": "MU", "side": "LONG", "param": "P", "value_json": "1",
        "campaign": "stocks_repaired_20260730_c5", "validation_status": "PASS",
        "ts": "2026-08-01T00:00:00Z",
        "contract_fingerprint": "tradier-matrix-exec-c5-20260730:same",
    }
    selected, history, audit = select_authoritative_rows([
        {**common, "delta_gain_mo_vs_bh": 1.0, "trades_fingerprint": "a"},
        {**common, "delta_gain_mo_vs_bh": 2.0, "trades_fingerprint": "b"},
    ])
    assert selected == []
    assert len(history) == 2
    assert audit["top_priority_identity_conflicts"] == 1
    assert all(
        row["authority_selection_status"]
        == "QUARANTINED_TOP_PRIORITY_IDENTITY_CONFLICT"
        for row in history
    )


def _workbook(path: Path, *, same_key_duplicate: bool) -> None:
    wb = Workbook()
    for index, name in enumerate(("Entry", "Exit", "Sizing", "Other")):
        ws = wb.active if index == 0 else wb.create_sheet()
        ws.title = name
        ws.append(["main_switch", "sub_setting", "value", "description", "status", "MU_LONG", "NVDA_LONG"])
        mu = float(index + 1)
        if same_key_duplicate and index == 1:
            mu = 1.0
        # Cross-key equality is legitimate; MU_LONG and NVDA_LONG may have the
        # same measured value without violating either key's decision surface.
        ws.append([f"P{index}", "", index, "test", "OK", mu, float(index + 1)])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _run(root: Path) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/audit_matrix_unique_nonzero.py"), "--root", str(root)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads((root / "data/reports/SWITCH_MATRIX_UNIQUE_NONZERO_AUDIT_CURRENT.json").read_text())
    return result.returncode, payload


def test_equal_values_across_different_symbol_sides_are_allowed(tmp_path: Path) -> None:
    _workbook(tmp_path / "data/reports/SWITCH_MATRIX_TRB.xlsx", same_key_duplicate=False)
    code, payload = _run(tmp_path)
    assert code == 0
    assert payload["status"] == "PASS"
    assert payload["global_uniqueness_scope"] is False
    assert payload["duplicate_cells"] == 0


def test_duplicate_within_one_symbol_side_is_rejected(tmp_path: Path) -> None:
    _workbook(tmp_path / "data/reports/SWITCH_MATRIX_TRB.xlsx", same_key_duplicate=True)
    code, payload = _run(tmp_path)
    assert code == 2
    assert payload["status"] == "FAIL_BLOCK_ADVANCEMENT"
    assert payload["duplicate_cells"] == 1
    assert payload["duplicate_samples"][0]["key"] == "MU_LONG"


def test_native_precision_rerun_resolves_only_its_real_collision(tmp_path: Path) -> None:
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    archive = reports / "PILOT_V8_CELLS_IMMUTABLE.jsonl"
    common = {
        "schema": "pilot-v8-cell-immutable-v1",
        "evidence_class": "RECOVERED_HISTORICAL_V8_DB_ROW",
        "immutable_preservation": True,
        "promotion_allowed": False,
        "key": "MU_LONG",
        "delta_gain_mo_vs_bh": 1.2345,
    }
    rows = [
        {**common, "param": "A", "value_json": "1"},
        {**common, "param": "B", "value_json": "2"},
    ]
    archive.write_text("".join(json.dumps(row) + "\n" for row in rows))
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (reports / "PILOT_V8_CELLS_IMMUTABLE_RECEIPT.json").write_text(
        json.dumps({"status": "PASS", "archive_sha256": archive_sha})
    )
    deployment = "tested-deployment"
    (reports / "PILOT_V8_FULL_PRECISION_RERUN_STATUS.json").write_text(
        json.dumps(
            {
                "schema": "pilot-v8-full-precision-rerun-status-v1",
                "status": "PASS",
                "source_archive_sha256": archive_sha,
                "deployment_sha256": deployment,
            }
        )
    )
    (reports / "PILOT_V8_FULL_PRECISION_RERUN_RESULTS.jsonl").write_text(
        json.dumps(
            {
                "status": "PASS",
                "deployment_sha256": deployment,
                "key": "MU_LONG",
                "param": "B",
                "value_json": "2",
                "delta_gain_mo_vs_bh_full_precision": 1.2345000007,
                "gain_per_mo_full_precision": 2.0,
                "acc_gain_pct_full_precision": 3.0,
                "run_identity": "real-run",
            }
        )
        + "\n"
    )

    index, audit = load_recovered_pilot_v8_cells(tmp_path)

    assert index[("A", "1", "MU_LONG")]["physical_display_status"] == "PASS_UNIQUE_NONZERO"
    rerun = index[("B", "2", "MU_LONG")]
    assert rerun["physical_display_status"] == "PASS_UNIQUE_NONZERO"
    assert rerun["delta_gain_mo_vs_bh"] == 1.2345000007
    assert rerun["native_precision_rerun"] is True
    assert audit["native_precision_reruns"]["accepted_pass_rows"] == 1


def test_selected_archive_duplicate_logical_identity_fails_before_overwrite(tmp_path):
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    archive = reports / "PILOT_V8_CELLS_IMMUTABLE.jsonl"
    common = {
        "schema": "pilot-v8-cell-immutable-v1",
        "evidence_class": "RECOVERED_HISTORICAL_V8_DB_ROW",
        "immutable_preservation": True,
        "promotion_allowed": False,
        "key": "MU_LONG", "param": "A", "value_json": "1",
    }
    archive.write_text(
        json.dumps({**common, "delta_gain_mo_vs_bh": 1.0}) + "\n"
        + json.dumps({**common, "delta_gain_mo_vs_bh": 2.0}) + "\n"
    )
    (reports / "PILOT_V8_CELLS_IMMUTABLE_RECEIPT.json").write_text(json.dumps({
        "status": "PASS",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }))
    with pytest.raises(RuntimeError, match="DUPLICATE_LOGICAL_IDENTITY"):
        load_recovered_pilot_v8_cells(tmp_path)


def test_native_precision_export_dedupes_same_logical_pass_across_deployments(
    tmp_path,
):
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    archive = reports / "PILOT_V8_CELLS_IMMUTABLE.jsonl"
    common = {
        "schema": "pilot-v8-cell-immutable-v1",
        "evidence_class": "RECOVERED_HISTORICAL_V8_DB_ROW",
        "immutable_preservation": True,
        "promotion_allowed": False,
        "key": "MU_LONG",
        "param": "A",
        "value_json": "1",
        "delta_gain_mo_vs_bh": 1.2345,
    }
    archive.write_text(json.dumps(common) + "\n")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (reports / "PILOT_V8_CELLS_IMMUTABLE_RECEIPT.json").write_text(
        json.dumps({"status": "PASS", "archive_sha256": archive_sha})
    )
    logical = "stable-logical-setting"
    (reports / "PILOT_V8_FULL_PRECISION_RERUN_STATUS.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "source_archive_sha256": archive_sha,
                "deployment_sha256": "new-deployment",
                "selection": [
                    {
                        "key": "MU_LONG",
                        "param": "A",
                        "value_json": "1",
                        "logical_run_identity": logical,
                    }
                ],
            }
        )
    )
    rows = [
        {
            "status": "PASS",
            "deployment_sha256": deployment,
            "logical_run_identity": logical,
            "run_identity": logical,
            "key": "MU_LONG",
            "param": "A",
            "value_json": "1",
            "delta_gain_mo_vs_bh_full_precision": metric,
        }
        for deployment, metric in (("old-deployment", 1.2345001), ("new-deployment", 1.2345002))
    ]
    (reports / "PILOT_V8_FULL_PRECISION_RERUN_RESULTS.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )

    index, audit = load_recovered_pilot_v8_cells(tmp_path)

    assert index[("A", "1", "MU_LONG")]["delta_gain_mo_vs_bh"] == 1.2345002
    assert audit["native_precision_reruns"]["accepted_pass_rows"] == 1
    assert audit["native_precision_reruns"]["pass_ledger_rows"] == 2
    assert audit["native_precision_reruns"]["ledger_rows_preserved"] == 2


def test_native_precision_export_preserves_contract_bound_cross_deployment_pass(
    tmp_path,
):
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    archive = reports / "PILOT_V8_CELLS_IMMUTABLE.jsonl"
    source = {
        "schema": "pilot-v8-cell-immutable-v1",
        "evidence_class": "RECOVERED_HISTORICAL_V8_DB_ROW",
        "immutable_preservation": True,
        "promotion_allowed": False,
        "key": "MU_LONG",
        "param": "P",
        "value_json": "1",
        "delta_gain_mo_vs_bh": 1.0,
    }
    archive.write_text(json.dumps(source) + "\n")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (reports / "PILOT_V8_CELLS_IMMUTABLE_RECEIPT.json").write_text(
        json.dumps({"status": "PASS", "archive_sha256": archive_sha})
    )
    logical = "stable-setting"
    contract = "engine-contract"
    (reports / "PILOT_V8_FULL_PRECISION_RERUN_STATUS.json").write_text(
        json.dumps(
            {
                "status": "FAIL_PARTIAL",
                "source_archive_sha256": archive_sha,
                "deployment_sha256": "new-runner",
                "selection": [
                    {
                        "key": "MU_LONG",
                        "param": "P",
                        "value_json": "1",
                        "logical_run_identity": logical,
                        "target_contract_fingerprint": contract,
                    }
                ],
            }
        )
    )
    (reports / "PILOT_V8_FULL_PRECISION_RERUN_RESULTS.jsonl").write_text(
        json.dumps(
            {
                "status": "PASS",
                "deployment_sha256": "old-runner",
                "logical_run_identity": logical,
                "key": "MU_LONG",
                "param": "P",
                "value_json": "1",
                "new_contract_fingerprint": contract,
                "delta_gain_mo_vs_bh_full_precision": 1.0000001,
            }
        )
        + "\n"
    )

    index, audit = load_recovered_pilot_v8_cells(tmp_path)

    assert index[("P", "1", "MU_LONG")]["delta_gain_mo_vs_bh"] == 1.0000001
    assert audit["native_precision_reruns"][
        "verified_cross_deployment_pass_rows"
    ] == 1
