import json

from tools.audit_switch_matrix_uniqueness import HELPER_PARAMS, audit
from tools.matrix_guard import fatal_authority_errors


def test_composite_and_foreign_alias_rows_are_non_scalar_provenance():
    assert {
        "BAND_LADDER", "COMBO", "COMBO_LOO", "COMBO_PROBE", "GROUP_COMBO",
        "LADDER_STAGE0", "NOLOSS_MIN_PROFIT_PCT",
    } <= HELPER_PARAMS


def test_real_unannotated_live_flags_have_typed_manifest_authority():
    manifest = json.load(open("data/param_sweep_manifest_tradier.json"))["params"]
    for name in ("MANAGE_REDUCE", "SERVICE_STOP"):
        row = manifest[name]
        assert row["type"] == "bool"
        assert row["default"] is True
        assert row["consumed_by"]["live"] is True
        assert row["sweepable"] is False


def test_current_classifier_has_no_fatal_authority_error():
    payload = audit()
    assert fatal_authority_errors(payload) == []
