import dataclasses
import json
from pathlib import Path

import build_sweep_manifest as builder
from config_tradier import TradierConfig
from sweep_value_semantics import executable_values, validate_test_values
from tradier_sweep_grid_contract import TRADIER_GRID_CONTRACTS


def _defaults():
    return {
        field.name: field.default
        for field in dataclasses.fields(TradierConfig)
        if field.default is not dataclasses.MISSING
    }


def test_all_contract_grids_are_typed_and_include_active_default():
    defaults = _defaults()
    assert len(TRADIER_GRID_CONTRACTS) == 14
    for name, contract in TRADIER_GRID_CONTRACTS.items():
        assert name in defaults
        values = contract["values"]
        validation = validate_test_values(name, defaults[name], values)
        assert validation["status"] == "VALID", (name, validation)
        assert executable_values(validation) == values
        assert any(
            builder._same_typed_value(defaults[name], value)
            for value in values
        ), name
        assert contract["evidence"].strip()


def test_numeric_reentry_grid_contains_no_booleans():
    values = TRADIER_GRID_CONTRACTS["REENTRY_RALLY_HTF_MIN"]["values"]
    assert values == [0, 1, 2, 3]
    assert all(type(value) is int for value in values)


def test_runtime_timestamps_are_classified_as_operational_not_alpha():
    assert builder.RUNTIME_FENCE_PAT.search("MTF_EXIT_MIN_OPEN_TS")
    assert builder.RUNTIME_FENCE_PAT.search("SOME_START_TS")
    assert builder.RUNTIME_FENCE_PAT.search("SOME_END_TS")
    assert not builder.RUNTIME_FENCE_PAT.search("MIN_HOLD_MINUTES")


def test_default_control_insertion_is_strictly_typed_and_idempotent():
    assert builder._include_default_control(3, [True, False]) == [
        True,
        False,
        3,
    ]
    assert builder._include_default_control(3, [1, 3]) == [1, 3]
    assert builder._include_default_control(0.01, [0.0]) == [0.0, 0.01]


def test_json_transport_preserves_float_defaults():
    assert builder._jsonable(1.0) == 1.0
    assert type(builder._jsonable(1.0)) is float
    assert type(builder._jsonable(1)) is int


def test_regenerated_manifest_has_no_prior_blocked_grid():
    manifest = json.loads(
        Path("data/param_sweep_manifest_tradier.json").read_text()
    )["params"]
    for name, contract in TRADIER_GRID_CONTRACTS.items():
        row = manifest[name]
        assert row["sweepable"] is True, name
        assert row["test_values"] == contract["values"], name
        assert row["range_validation"]["status"] == "VALID", name
        assert row["grid_contract_evidence"] == contract["evidence"], name
    runtime = manifest["MTF_EXIT_MIN_OPEN_TS"]
    assert runtime["sweep_tier"] == "RUNTIME_CONTROL"
    assert runtime["sweepable"] is False
    assert runtime["test_values"] is None
    assert runtime["runtime_control_not_alpha"] is True
