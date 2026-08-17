import json

from tools import publish_coupled_qualification_progress as publish


def test_receipt_with_missing_result_producing_hash_is_stale(tmp_path):
    campaign = tmp_path / "data" / "reports" / "vec_research" / "campaign"
    receipt = campaign / "results" / "ABC_LONG" / "QUALIFICATION_RESULT.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "key": "ABC_LONG",
        "source_hashes": {"executor_sha256": "old"},
        "strict_survivor_count": 1,
        "untouched_final_read": True,
        "train_ranked_candidate": {},
    }))
    row = publish.summarize_receipt(
        receipt, campaign, {"executor_sha256": "new", "beam_sha256": "new"}
    )
    assert row["truth"] == "STALE_RESULT_PRODUCING_SOURCE_HASH"
    assert row["result_producing_source_current"] is False
    assert row["stale_or_missing_source_hashes"] == ["beam_sha256", "executor_sha256"]


def test_current_receipt_retains_qualification_truth(tmp_path):
    campaign = tmp_path / "data" / "reports" / "vec_research" / "campaign"
    receipt = campaign / "results" / "ABC_LONG" / "QUALIFICATION_RESULT.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "key": "ABC_LONG", "source_hashes": {"executor_sha256": "new"},
        "strict_survivor_count": 0, "untouched_final_read": False,
        "train_ranked_candidate": {},
    }))
    row = publish.summarize_receipt(
        receipt, campaign, {"executor_sha256": "new"}
    )
    assert row["truth"] == "REJECTED_NO_STRICT_TRAIN_SURVIVOR"
    assert row["result_producing_source_current"] is True


def test_receipt_reports_recipe_and_unique_screen_counts(tmp_path):
    campaign = tmp_path / "data" / "reports" / "vec_research" / "campaign"
    receipt = campaign / "results" / "ABC_LONG" / "QUALIFICATION_RESULT.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "key": "ABC_LONG", "source_hashes": {"executor": "same"},
        "recipe_candidates": [
            {"status": "TRAIN_SCREENED", "discovery_screen_id": "one"},
            {"status": "TRAIN_SCREENED", "discovery_screen_id": "one"},
            {"status": "NO_DISCOVERY_GRID_CANDIDATE"},
        ],
    }))
    row = publish.summarize_receipt(receipt, campaign, {"executor": "same"})
    assert row["recipe_candidates_evaluated"] == 3
    assert row["unique_discovery_screens"] == 1
    assert row["candidate_status_counts"] == {
        "TRAIN_SCREENED": 2, "NO_DISCOVERY_GRID_CANDIDATE": 1
    }
