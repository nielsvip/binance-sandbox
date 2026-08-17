from pathlib import Path

from tools import vector_approx_entry_augment as approx


def test_numeric_proxy_uses_registered_source_rank():
    registry = {
        "P": {
            "target": "TARGET",
            "proxy_range": (10.0, 20.0),
            "companions": {"ENABLED": True},
            "source_values": [100, 200, 300],
            "param": "P",
            "source_group": "ENTRY",
            "action_group": "ENTRY",
            "vector_evidence_class": "VEC_APPROX",
            **approx.AUTHORITY_FLAGS,
        }
    }

    overrides, metadata = approx.adapt("P", 200, "LONG", registry)

    assert overrides == {"TARGET": 15.0, "ENABLED": True}
    assert metadata["source_rank"] == 0.5
    assert metadata["proxy_value"] == 15.0
    assert "source_rank" in metadata["approximation_formula"]


def test_boolean_master_maps_directly_and_has_no_authority():
    registry = {
        "P": {
            "target": "TARGET",
            "proxy_range": None,
            "companions": {},
            "source_values": [True, False],
            "param": "P",
            "source_group": "ENTRY",
            "action_group": "ENTRY",
            "vector_evidence_class": "VEC_APPROX",
            **approx.AUTHORITY_FLAGS,
        }
    }

    overrides, metadata = approx.adapt("P", "false", "SHORT", registry)

    assert overrides == {"TARGET": False}
    assert metadata["exact_completion_credit"] is False
    assert metadata["promotion_allowed"] is False
    assert metadata["engine_ranking_allowed"] is False


def test_side_specific_proxy_target():
    registry = {
        "P": {
            "target": {"LONG": "LONG_TARGET", "SHORT": "SHORT_TARGET"},
            "proxy_range": (0.0, 1.0),
            "companions": {},
            "source_values": [0, 10],
            "param": "P",
            "source_group": "ENTRY",
            "action_group": "ENTRY",
            "vector_evidence_class": "VEC_APPROX",
            **approx.AUTHORITY_FLAGS,
        }
    }

    overrides, metadata = approx.adapt("P", 10, "SHORT", registry)

    assert overrides == {"SHORT_TARGET": 1.0}
    assert metadata["vector_target_fields"] == ["SHORT_TARGET"]


def test_registry_excludes_pure_ops_and_external_data_controls():
    assert "SCALP_MAX_POSITIONS_PER_SIDE" not in approx.SPECS
    assert "SATOSHIT_ENTRY_FILTER" not in approx.SPECS
    assert "OVERNIGHT_GAP_HEDGE_OPEN_MINUTES" not in approx.SPECS
