import hashlib
import json
from pathlib import Path

from tools import build_classic_formation_parity_digest as digest


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def test_digest_keeps_group_numbers_but_blocks_exact_admission(tmp_path):
    artifact_dir = tmp_path / "data/reports/vec_research/campaign/keys/ABC_LONG/entry"
    artifact_path = artifact_dir / "result.json"
    selected_and_group = [
        {
            "ts": 1,
            "type": "OPEN",
            "reason": "CLASSIC_FORMATION_ENTRY_TREND_STRUCTURE_score0.8",
        },
        {"ts": 2, "type": "CLOSE", "reason": "HYBRID_STRUCT_EXIT_BEAR"},
        {"ts": 3, "type": "OPEN", "reason": "GOLDEN_RULE_ENTRY"},
        {
            "ts": 4,
            "type": "CLOSE",
            "reason": "CLASSIC_FORMATION_EXIT_WEDGE_score0.7",
        },
    ]
    _write(
        artifact_path,
        {
            "schema": "classic-formation-shared-vector-artifact-v1",
            "outer_folds": [
                {"name": name, "event_ledger": selected_and_group}
                for name in ("train", "holdout")
            ],
        },
    )
    artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    recipe = {
        "schema": "complete-vector-lifecycle-recipe-v1",
        "KEY": "ABC_LONG",
        "ENTRY": {
            "family": "ENTRY_CLASSIC_FORMATION_TREND_STRUCTURE",
            "params": {"timeframe": "ALL", "min_score": 0.65},
            "artifact": str(artifact_dir.relative_to(tmp_path)),
            "artifact_result_sha256": artifact_sha,
        },
        "EXIT": {
            "family": "EXIT_CLASSIC_FORMATION_WEDGE",
            "params": {"timeframe": "ALL", "min_score": 0.65},
        },
    }
    priority_path = tmp_path / "priority.json"
    _write(
        priority_path,
        {
            "entry_exit_combos": [
                {
                    "key": "ABC_LONG",
                    "exact_v8_authorized": False,
                    "holdout_alpha_vs_bh_pp": 8.0,
                }
            ]
        },
    )
    materialized_path = tmp_path / "materialized.json"
    _write(
        materialized_path,
        {
            "exact_v8_queued": False,
            "hotlist": [
                {
                    "key": "ABC_LONG",
                    "champion": {
                        "key": "ABC_LONG",
                        "entry_family": recipe["ENTRY"]["family"],
                        "exit_family": recipe["EXIT"]["family"],
                        "strategy_return_pct": 12.0,
                        "bh_return_pct": 4.0,
                        "ledger_backed_vector_close_fills": 14,
                        "tim_pct": 31.5,
                        "complete_recipe": recipe,
                    },
                }
            ],
        },
    )
    exact_path = tmp_path / "exact.json"
    _write(
        exact_path,
        {
            "rows": [
                {
                    "key": "ABC_LONG",
                    "exact_v8_status": "V8_FULL_RECIPE_PASS",
                    "strategy_return_pct": 6.0,
                    "side_aware_bh_return_pct": 4.0,
                    "alpha_vs_bh_pp": 2.0,
                    "real_closes": 12,
                    "time_in_market_pct": 40.0,
                    "entry_route_fills": 3,
                    "selected_exit_route_fills": 2,
                    "complete_recipe": recipe,
                }
            ]
        },
    )

    payload = digest.build_digest(
        tmp_path, priority_path, materialized_path, exact_path
    )
    row = payload["rows"][0]
    assert row["vector_evidence_class"] == "GROUP_VECTOR_RESEARCH"
    assert row["vector_group_return_pct"] == 12.0
    assert row["vector_side_aware_bh_return_pct"] == 4.0
    assert row["vector_positive_bh_multiple"] == 3.0
    assert row["selected_entry_event_count"] == 1
    assert row["selected_exit_event_count"] == 1
    assert row["undeclared_event_count"] == 2
    assert set(row["undeclared_event_families"]) == {
        "CLOSE:HYBRID_STRUCT_EXIT",
        "OPEN:GOLDEN_RULE",
    }
    assert row["exact_status"] == "V8_FULL_RECIPE_PASS"
    assert row["exact_recipe_matches_selected"] is True
    assert row["exact_strategy_return_pct"] == 6.0
    assert row["exact_real_closes"] == 12
    assert row["exact_admission_authorized"] is False
    assert (
        row["exact_admission_blocker"]
        == "UNDECLARED_VECTOR_EVENT_PATHS_DO_NOT_MATCH_SELECTED_FORMATION_RECIPE"
    )
    assert payload["summary"]["independent_exact_pass_count"] == 1
    assert payload["summary"]["formation_exact_ready_pass_count"] == 0
    rendered = digest.render_md(payload)
    assert "GROUP_VECTOR_RESEARCH" in rendered
    assert "UNDECLARED_VECTOR_EVENT_PATHS" in rendered


def test_clean_unapproved_recipe_remains_research_only():
    from tools.classic_formation_event_parity import classify_evidence

    evidence, admitted, blocker = classify_evidence(
        {"holdout": {"pass": True, "undeclared_paths": []}},
        exact_authorized=False,
    )
    assert evidence == "ISOLATED_FORMATION_VECTOR_RESEARCH"
    assert admitted is False
    assert blocker == "EXACT_AUTHORIZATION_NOT_EXPLICIT"
