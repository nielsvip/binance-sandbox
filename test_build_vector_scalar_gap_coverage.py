import json

from tools import build_vector_scalar_gap_coverage as builder
from tools import vector_scalar_gap_reporting as reporting


def test_builder_merges_raw_nested_approx_without_exact_authority(tmp_path):
    reports = tmp_path / "data" / "reports"
    native_dir = reports / "vectorized_scalar_gap"
    approx_dir = reports / "vector_approx_other_sizing"
    native_dir.mkdir(parents=True)
    approx_dir.mkdir(parents=True)
    receipt_path = reports / "VECTOR_SCALAR_GAP_COVERAGE_20260731.json"
    receipt_path.write_text(json.dumps({
        "schema_version": 1,
        "generated_at": "2026-07-31T01:00:00Z",
        "tier": "VEC_DIAGNOSTIC",
        "display_contract": "SEPARATE_AMBER_ITALIC",
        "exact_completion_credit": False,
        "engine_ranking_allowed": False,
        "promotion_allowed": False,
        "approved_vector_adapters": [],
        "screened_cells": 1,
        "per_key": {
            "MU_LONG": {
                "source":
                    "data/reports/vectorized_scalar_gap/MU_LONG.jsonl",
                "screened_cells": 1,
                "moved": 1,
                "inert": 0,
                "zero_trade": 0,
                "native_vector_cells": 1,
                "sampled_exact_parity_adapter_cells": 0,
            }
        },
    }))
    (reports / "VECTOR_ADAPTER_PARITY_AUDIT_20260731.json").write_text(
        json.dumps({"approved_params": [], "rejected_params": []})
    )
    native = {
        "key": "MU_LONG",
        "param": "NATIVE",
        "value_json": "true",
        "status": "MOVED",
        "tier": "VEC_DIAGNOSTIC",
        "exact_completion_credit": False,
        "promotion_allowed": False,
        "generated_at": "2026-07-31T01:00:00Z",
    }
    (native_dir / "MU_LONG.jsonl").write_text(json.dumps(native) + "\n")
    raw_approx = {
        "key": "MU_LONG",
        "param": "POSITION_SIZE",
        "value_json": "200",
        "status": "INERT",
        "tier": "VEC_APPROX",
        "vector_evidence_class": "VEC_APPROX",
        "exact_completion_credit": False,
        "engine_ranking_allowed": False,
        "promotion_allowed": False,
        "live_config_write_allowed": False,
        "db_engine_write_allowed": False,
        "generated_at": "2026-07-31T02:00:00Z",
        "delta_gain_mo_vs_bh_approx": 0.2,
        "approximation": {
            "approximation_confidence": "MEDIUM",
            "approximation_mismatch_class":
                "PATH_SPECIFIC_SIZING_COLLAPSED_TO_SIDE_SIZE_MULT",
            "source_group": "SIZING",
            "action_group": "SIZING",
            "source_rank": 0.5,
            "proxy_field": "LONG_SIZE_MULT",
            "proxy_value": 1.25,
            "required_next_stage": "EXACT_V8",
            "exact_completion_credit": False,
            "engine_ranking_allowed": False,
            "promotion_allowed": False,
            "live_config_write_allowed": False,
            "db_engine_write_allowed": False,
        },
    }
    (approx_dir / "MU_LONG.jsonl").write_text(
        json.dumps(raw_approx) + "\n"
    )

    merged = builder.build(tmp_path, receipt_path)
    receipt_path.write_text(json.dumps(merged))
    payload = reporting.load(tmp_path)

    assert merged["screened_cells"] == 2
    assert merged["vec_native_cells"] == 1
    assert merged["vec_approx_cells"] == 1
    assert merged["approximation_confidence"] == {
        "HIGH": 0,
        "MEDIUM": 1,
        "LOW": 0,
    }
    assert payload["available"] is True
    approx = next(
        row for row in payload["rows"]
        if row["vector_evidence_class"] == "VEC_APPROX"
    )
    assert approx["tier"] == "VEC_APPROX"
    assert approx["proxy_field"] == "LONG_SIZE_MULT"
    assert approx["delta_gain_mo_vs_bh_diagnostic"] == 0.2
    assert approx["exact_completion_credit"] is False
    assert approx["engine_ranking_allowed"] is False
    assert approx["promotion_allowed"] is False
