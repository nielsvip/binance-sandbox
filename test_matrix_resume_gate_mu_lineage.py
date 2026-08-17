from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools import matrix_resume_gate as gate
from tools.run_mu_826_vector_amber import (
    PRIOR_RESIDUAL_SOURCE,
    _prior_residual_records,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_package(
    root: Path,
    *,
    remaining_quarantined: int = 0,
    hide_prior_as_generic_unavailable: bool = False,
) -> dict[str, object]:
    out = root / "data" / "reports" / "mu_vector_materializations" / "test"
    out.mkdir(parents=True)
    records = _prior_residual_records()
    repaired = [records[0]["logical_id"]]
    other = [row["logical_id"] for row in records[-7:]]
    unsupported = set(other[-3:])
    remaining = [
        row["logical_id"]
        for row in records[1 : 1 + remaining_quarantined]
    ]
    inert = [
        row["logical_id"]
        for row in records[1:-7]
        if row["logical_id"] not in set(remaining)
    ]
    buckets = {
        "repaired_to_pass": repaired,
        "reclassified_inert_unavailable": inert,
        "reclassified_other_unavailable": other,
        "remaining_quarantined": remaining,
        "protected_exact": [],
        "unaccounted": [],
    }

    rows: list[dict[str, object]] = []
    raw_rows: list[dict[str, object]] = []
    for record in records:
        logical_id = record["logical_id"]
        row: dict[str, object] = {
            "key": record["key"],
            "param": record["param"],
            "value_json": record["value_norm"],
        }
        if logical_id in repaired:
            row.update(
                status="MOVED",
                result_uniqueness_status="PASS",
                delta_gain_mo_vs_bh_approx=1.25,
                behavior_fingerprint=f"pass-{logical_id}",
                trades=3,
            )
            raw_rows.append(dict(row))
        elif logical_id in remaining:
            row.update(
                status="MOVED",
                result_uniqueness_status="QUARANTINED",
                delta_gain_mo_vs_bh_approx=0.5,
                behavior_fingerprint="collision",
                trades=1,
            )
            raw_rows.append(dict(row))
        else:
            classification = (
                "DATA_UNAVAILABLE_UNSUPPORTED_CAUSAL_ADAPTER"
                if logical_id in unsupported
                else (
                    "DATA_UNAVAILABLE_DEGENERATE_NO_ACTIVITY"
                    if logical_id in other
                    else "DATA_UNAVAILABLE_INERT_CAUSAL_ADAPTER"
                )
            )
            raw_status = (
                "UNSUPPORTED_CAUSAL_ADAPTER"
                if logical_id in unsupported
                else ("DEGENERATE" if logical_id in other else "INERT")
            )
            raw_row = {
                **row,
                "status": raw_status,
                "delta_gain_mo_vs_bh_approx": 0.0,
                "behavior_fingerprint": f"raw-{logical_id}",
                "trades": 0,
            }
            raw_rows.append(raw_row)
            row.update(
                status="DATA_UNAVAILABLE",
                result_uniqueness_status="NOT_APPLICABLE_DATA_UNAVAILABLE",
                raw_measurement_status=raw_status,
                raw_evidence_sha256=hashlib.sha256(
                    json.dumps(
                        raw_row, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                causal_adapter_classification=classification,
            )
        rows.append(row)
    if hide_prior_as_generic_unavailable:
        victim = next(
            row
            for row in rows
            if row.get("raw_measurement_status") == "UNSUPPORTED_CAUSAL_ADAPTER"
        )
        victim.pop("raw_measurement_status")
        victim.pop("raw_evidence_sha256")
        victim.pop("causal_adapter_classification")

    for index in range(737):
        source_row = {
                "key": "MU_LONG",
                "param": f"SOURCE_UNAVAILABLE_{index}",
                "value_json": "0",
                "status": "DATA_UNAVAILABLE",
                "result_uniqueness_status": "NOT_APPLICABLE_DATA_UNAVAILABLE",
            }
        rows.append(source_row)
        raw_rows.append(dict(source_row))
    eligible = [
        row
        for row in rows
        if row.get("status") == "DATA_UNAVAILABLE"
        or row.get("result_uniqueness_status") == "PASS"
    ]
    passed = 1
    unavailable = len(eligible) - passed
    quarantined = remaining_quarantined

    raw_path = out / "MU_LONG.raw.test.jsonl"
    audited_path = out / "MU_LONG.audited.jsonl"
    portable_path = out / "MU_LONG.jsonl"
    audit_path = out / "MU_LONG.uniqueness_audit.json"
    _write_jsonl(raw_path, raw_rows)
    _write_jsonl(audited_path, rows)
    _write_jsonl(portable_path, eligible)
    strict_audit = {
        "quarantined_numeric_rows": quarantined,
        "inert_numeric_reclassified_data_unavailable_rows": len(inert),
        "no_activity_numeric_reclassified_data_unavailable_rows": len(other),
        "source_explicit_data_unavailable_rows": 737 + len(other),
    }
    audit_path.write_text(json.dumps(strict_audit, sort_keys=True), encoding="utf-8")

    prior_ids = [row["logical_id"] for row in records]
    prior_hash = hashlib.sha256(
        json.dumps(prior_ids, separators=(",", ":")).encode()
    ).hexdigest()
    transition = {
        "schema_version": 1,
        "prior_residual_source": PRIOR_RESIDUAL_SOURCE,
        "prior_residual_quarantined_logicals": prior_ids,
        "prior_residual_logical_records": records,
        "prior_residual_quarantined_logicals_sha256": prior_hash,
        **buckets,
        "bucket_counts": {name: len(values) for name, values in buckets.items()},
        "conservation_ok": True,
    }
    campaign = {
        "key": "MU_LONG",
        "target_cells": 826,
        "existing_exact_or_occupied": 0,
        "passed_numeric_rows": passed,
        "explicit_data_unavailable_rows": unavailable,
        "quarantined_numeric_rows": quarantined,
        "audited_row_count": len(rows),
        "portable_eligible_row_count": len(eligible),
        "unresolved_count": quarantined,
        "raw_path": str(raw_path.relative_to(root)),
        "raw_sha256": _sha256(raw_path),
        "audited_path": str(audited_path.relative_to(root)),
        "audited_sha256": _sha256(audited_path),
        "portable_key_path": str(portable_path.relative_to(root)),
        "portable_key_sha256": _sha256(portable_path),
        "uniqueness_audit_path": str(audit_path.relative_to(root)),
        "uniqueness_audit_sha256": _sha256(audit_path),
        "prior_residual_transition": transition,
        "prior_residual_quarantined_logicals": prior_ids,
        "prior_residual_quarantined_logicals_sha256": prior_hash,
        **{name: values for name, values in buckets.items() if name != "unaccounted"},
        "prior_residual_conservation_ok": True,
        "campaign_must_stop": bool(quarantined),
        "safe_to_merge": not quarantined,
        "portable_is_eligible_projection_of_audited": True,
    }
    campaign_path = out / "campaign_receipt.json"
    campaign_path.write_text(json.dumps(campaign, sort_keys=True), encoding="utf-8")
    return {
        "contract": "STRICT_PER_KEY_UNIQUE_METRIC_AND_ACTION_NO_DUPLICATES",
        "current_engine_campaign": "stocks_repaired_20260730_c5",
        "matrix_numeric_fill_requires_pass": True,
        "input_rows": 826,
        "passed_numeric_overlays": 1,
        "data_unavailable_rows": 825,
        "quarantined_numeric_overlays": 0,
        "metric_collision_groups": 0,
        "fingerprint_collision_groups": 0,
        "mu_materialization_lineage": {
            "schema": "mu-materialization-lineage-v1",
            "campaign_receipt_path": str(campaign_path.relative_to(root)),
            "campaign_receipt_sha256": _sha256(campaign_path),
        },
    }


def test_valid_full_826_materialization_conserves_all_prior_89(tmp_path: Path) -> None:
    receipt = _build_package(tmp_path)

    assert gate.validate_mu_materialization_lineage(tmp_path, receipt) == []


def test_prior_quarantine_cannot_be_hidden_as_generic_unavailable(
    tmp_path: Path,
) -> None:
    receipt = _build_package(tmp_path, hide_prior_as_generic_unavailable=True)

    reasons = gate.validate_mu_materialization_lineage(tmp_path, receipt)

    assert any(
        reason.startswith("MU_PRIOR_QUARANTINE_HIDDEN_AS_UNAVAILABLE:")
        for reason in reasons
    )


def test_eight_remaining_quarantines_cannot_publish_or_resume(tmp_path: Path) -> None:
    receipt = _build_package(tmp_path, remaining_quarantined=8)

    reasons = gate.validate_mu_materialization_lineage(tmp_path, receipt)

    assert "MU_MATERIALIZATION_QUARANTINED_8" in reasons
    assert "GLOBAL_RECEIPT_HIDES_MU_QUARANTINE" in reasons
    assert "MU_MATERIALIZATION_CAMPAIGN_MUST_STOP" in reasons
    assert "MU_MATERIALIZATION_NOT_SAFE_TO_MERGE" in reasons


def test_production_sized_zero_quarantine_receipt_requires_lineage() -> None:
    receipt = {
        "contract": "STRICT_PER_KEY_UNIQUE_METRIC_AND_ACTION_NO_DUPLICATES",
        "current_engine_campaign": "stocks_repaired_20260730_c5",
        "matrix_numeric_fill_requires_pass": True,
        "input_rows": 826,
        "passed_numeric_overlays": 737,
        "data_unavailable_rows": 89,
        "quarantined_numeric_overlays": 0,
        "metric_collision_groups": 0,
        "fingerprint_collision_groups": 0,
    }

    passed, reasons = gate.uniqueness_passes(
        receipt, "stocks_repaired_20260730_c5"
    )

    assert passed is False
    assert "MU_MATERIALIZATION_LINEAGE_MISSING" in reasons
