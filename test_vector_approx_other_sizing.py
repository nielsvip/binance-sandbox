import json

from tools import vector_approx_other_sizing as approx


def _fixture(tmp_path):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    manifest = {
        "params": {
            "POSITION_SIZE": {
                "sweepable": True,
                "test_values": [100, 200, 500],
            },
            "RSI_GATE": {
                "sweepable": True,
                "test_values": [20, 50, 80],
            },
            "GENERIC_GATE": {
                "sweepable": True,
                "test_values": [False, True],
            },
            "BACKTEST_DIAGNOSTIC": {
                "sweepable": True,
                "test_values": [False, True],
            },
        }
    }
    paths = []
    for param, group in (
        ("POSITION_SIZE", "SIZING"),
        ("RSI_GATE", "OTHER"),
        ("GENERIC_GATE", "OTHER"),
        ("BACKTEST_DIAGNOSTIC", "OTHER"),
    ):
        paths.append(
            {
                "param": param,
                "group": group,
                "role": "FILTER",
                "family": param,
                "differential_readiness": "READY_EXACT_ONLY",
                "deployment_scope": "PER_SYMBOL",
                "declared_live_decision_sites": ["tradier_manage.py:1"],
                "source_token_sites": [
                    "tradier_manage.py:1",
                    "backtest_v8_engine.py:2",
                ],
            }
        )
    (tmp_path / "data" / "param_sweep_manifest_tradier.json").write_text(
        json.dumps(manifest)
    )
    (
        reports / "SWITCH_MATRIX_INTERDEPENDENCY_20260729.json"
    ).write_text(json.dumps({"paths": paths}))


def test_registry_excludes_pure_ops_and_covers_other_sizing(tmp_path):
    _fixture(tmp_path)

    registry = approx.build_registry(tmp_path)
    cells, summary = approx.eligible_cells(tmp_path, registry)

    assert set(registry) == {"POSITION_SIZE", "RSI_GATE"}
    assert summary["cells"] == 6
    assert summary["by_source_group"] == {"SIZING": 3, "OTHER": 3}
    assert all(registry[param]["vector_evidence_class"] == "VEC_APPROX" for param in registry)
    assert all(registry[param]["exact_completion_credit"] is False for param in registry)
    assert all(registry[param]["engine_ranking_allowed"] is False for param in registry)
    assert all(registry[param]["promotion_allowed"] is False for param in registry)
    assert all(registry[param]["live_config_write_allowed"] is False for param in registry)
    assert all(registry[param]["db_engine_write_allowed"] is False for param in registry)
    assert ("BACKTEST_DIAGNOSTIC", "true") not in cells


def test_numeric_source_rank_maps_into_proxy_range_not_raw_units(tmp_path):
    _fixture(tmp_path)
    registry = approx.build_registry(tmp_path)

    overrides, metadata = approx.adapt(
        "POSITION_SIZE", 200, "LONG", registry, tmp_path
    )

    assert overrides == {"LONG_SIZE_MULT": 1.25}
    assert metadata["source_rank"] == 0.5
    assert metadata["proxy_field"] == "LONG_SIZE_MULT"
    assert metadata["proxy_value"] == 1.25
    assert metadata["source_value"] == 200
    assert metadata["approximation_confidence"] in {"LOW", "MEDIUM"}
    assert "COLLAPSED" in metadata["approximation_mismatch_class"]
    assert metadata["required_next_stage"] == "EXACT_V8"
    assert metadata["live_config_write_allowed"] is False
    assert metadata["db_engine_write_allowed"] is False


def test_side_specific_size_proxy_mirrors_field_only(tmp_path):
    _fixture(tmp_path)
    registry = approx.build_registry(tmp_path)

    long, long_meta = approx.adapt(
        "POSITION_SIZE", 500, "LONG", registry, tmp_path
    )
    short, short_meta = approx.adapt(
        "POSITION_SIZE", 500, "SHORT", registry, tmp_path
    )

    assert long == {"LONG_SIZE_MULT": 2.0}
    assert short == {"SHORT_SIZE_MULT": 2.0}
    assert long_meta["source_rank"] == short_meta["source_rank"] == 1.0


def test_real_registry_has_no_generic_numeric_or_low_confidence_sizing_fallback():
    registry = approx.build_registry()
    _cells, summary = approx.eligible_cells(registry=registry)

    assert summary["cells"] > 0
    assert all(
        spec["approximation_mismatch_class"]
        != "INCOMPATIBLE_THRESHOLD_UNITS_RANK_MAPPED_TO_ENTRY_SCORE"
        for spec in registry.values()
    )
    assert all(
        not (
            spec["source_group"] == "SIZING"
            and spec["approximation_confidence"] == "LOW"
        )
        for spec in registry.values()
    )
