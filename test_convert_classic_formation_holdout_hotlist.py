import hashlib
import json
from pathlib import Path

from tools.convert_classic_formation_holdout_hotlist import (
    convert_hotlist,
    formation_variant_identity,
)
from tools.hotlist_v8_full_recipe_routes import full_recipe_blockers


def _write(path: Path, value):
    path.write_text(json.dumps(value, sort_keys=True))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(key="ACN_LONG", validation=None, reenter=True):
    recipe = {
        "schema": "complete-vector-lifecycle-recipe-v1",
        "KEY": key,
        "ENTRY": {"family": "ENTRY_LADDER_GREEN", "artifact": "baseline-entry", "params": {"mode": "ordinary"}},
        "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"trail": 0.1}},
        "VALIDATION": validation or ["2025-01-01", "2025-03-31"],
    }
    if reenter:
        recipe["REENTER"] = {
            "family": "REENTER_RECLAIM",
            "params": {"threshold": 1},
            "action_evidence_status": "CAUSAL_EVENT_LEDGER",
            "reentry_pairs": [{"exit": "x", "entry": "y"}],
        }
    return {"lifecycle_all_folds_strict_gate": True, "complete_recipe": recipe}


def _row(tmp_path, source_sha, *, variant="wedge_entry_15m", **extra):
    train = tmp_path / "train.json"
    holdout = tmp_path / "holdout.json"
    train_sha = _write(train, {"fold": "train"})
    holdout_sha = _write(holdout, {"fold": "holdout"})
    row = {
        "key": "ACN_LONG",
        "formation_variant": variant,
        "formation_params": {"min_score": 0.65, "position_size_mult": 1.25, "exit_min_gain_pct": 1.1},
        "formation_artifact": "formation-entry-artifact",
        "formation_artifact_sha256": "a" * 64,
        "train_qualified": True,
        "holdout_qualified": True,
        "lifecycle_all_folds_strict_gate": True,
        "beats_bh_and_over_10_real_trades": True,
        "train_receipt": train.name,
        "train_receipt_sha256": train_sha,
        "holdout_receipt": holdout.name,
        "holdout_receipt_sha256": holdout_sha,
        "npz_sha256": "b" * 64,
        "classic_formations_sha256": "c" * 64,
        "source_recipe": "source.json",
        "source_recipe_sha256": source_sha,
        "validation": ["2025-01-01", "2025-03-31"],
    }
    row.update(extra)
    return row


def _convert(tmp_path, source=None, **row_extra):
    source_path = tmp_path / "source.json"
    source_sha = _write(source_path, source or _source())
    hotlist_path = tmp_path / "CLASSIC_FORMATION_HOLDOUT_HOTLIST.json"
    _write(hotlist_path, {"rows": [_row(tmp_path, source_sha, **row_extra)]})
    return convert_hotlist(hotlist_path)


def test_entry_candidate_is_hash_bound_complete_recipe_and_never_authorizes_execution(tmp_path):
    output = _convert(tmp_path)
    candidate = output["rows"][0]
    recipe = candidate["complete_recipe"]
    assert output["candidate_count"] == 1
    assert candidate["authorized_exact_run"] is False
    assert recipe["ENTRY"]["family"] == "ENTRY_CLASSIC_FORMATION_WEDGE"
    assert recipe["ENTRY"]["params"] == {
        "family": "wedge", "timeframe": "15m", "min_score": 0.65, "position_size_mult": 1.25
    }
    assert recipe["EXIT"]["family"] == "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"
    assert recipe["REENTER"]["family"] == "REENTER_RECLAIM"
    assert candidate["bindings"]["frozen_npz_sha256"] == "b" * 64
    assert candidate["bindings"]["classic_formations_sha256"] == "c" * 64
    # The candidate's ENTRY/EXIT envelope is consumable by the strict exact
    # dispatcher; this converter nevertheless authorizes no execution.
    assert not full_recipe_blockers(
        recipe["ENTRY"]["family"], recipe["EXIT"]["family"],
        recipe["ENTRY"]["params"], recipe["EXIT"]["params"],
    )


def test_exit_candidate_preserves_immutable_entry_and_maps_exit(tmp_path):
    output = _convert(
        tmp_path,
        variant="triangle_exit_4h",
        formation_params={"min_score": 0.55, "exit_min_gain_pct": 2.0},
    )
    recipe = output["rows"][0]["complete_recipe"]
    assert recipe["ENTRY"]["family"] == "ENTRY_LADDER_GREEN"
    assert recipe["EXIT"]["family"] == "EXIT_CLASSIC_FORMATION_TRIANGLE"
    assert recipe["EXIT"]["params"] == {
        "family": "triangle", "timeframe": "4h", "min_score": 0.55, "exit_min_gain_pct": 2.0
    }


def test_isolated_component_result_is_semantically_blocked(tmp_path):
    output = _convert(tmp_path, lifecycle_all_folds_strict_gate=False)
    row = output["rows"][0]
    assert output["candidate_count"] == 0
    assert "FORMATION_RESULT_ISOLATED_NO_FULL_LIFECYCLE_PROOF" in row["blockers"]
    assert row["authorized_exact_run"] is False


def test_missing_source_reenter_is_blocked(tmp_path):
    output = _convert(tmp_path, source=_source(reenter=False))
    assert "IMMUTABLE_SOURCE_RECIPE_REENTER_MISSING_OR_UNPROVEN" in output["rows"][0]["blockers"]


def test_variant_mapping_rejects_ambiguous_or_unknown_labels():
    assert formation_variant_identity("head_shoulders_entry_ALL")[:3] == (
        "ENTRY_CLASSIC_FORMATION_HEAD_SHOULDERS", "ENTRY", "ALL"
    )
    assert formation_variant_identity("wedge_entry_5m")[3] == ["CLASSIC_FORMATION_VARIANT_INVALID"]


def test_canonical_ranker_qualified_rows_get_explicit_blockers(tmp_path):
    hotlist = tmp_path / "CLASSIC_FORMATION_HOLDOUT_HOTLIST.json"
    _write(hotlist, {"qualified": [{"key": "ACN_LONG", "variant": "wedge_entry_1h"}]})

    output = convert_hotlist(hotlist)

    assert output["candidate_count"] == 0
    assert "FORMATION_RESULT_ISOLATED_NO_FULL_LIFECYCLE_PROOF" in output["rows"][0]["blockers"]
