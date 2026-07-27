from tools import compact_entry_exit_beam_campaign as compact


def test_candidate_drops_large_rejected_pool_fields():
    source = {
        "entry_family": "ENTRY_X",
        "entry_artifact": "a",
        "exit_family": "EXIT_Y",
        "exit_params": {},
        "discovery_fold_gate_pass": [True, False],
        "discovery_strict_fold_count": 1,
        "discovery_all_folds_strict": False,
        "discovery_fold_evidence": [{"fold": 1}],
        "untouched_final_validation": {"strict": True},
        "all_folds_strict": False,
        "_source": {"huge": True},
        "discovery_only_rejected": [1, 2, 3],
    }
    row = compact._candidate(source)
    assert "_source" not in row
    assert "discovery_only_rejected" not in row
    assert row["untouched_final_validation"]["strict"]


def test_markdown_reports_archive_provenance():
    payload = {
        "campaign_id": "x",
        "counts": {
            "keys": 1,
            "entry_schedules": 1,
            "candidate_rows": 10,
            "discovery_all_folds_strict": 0,
            "untouched_final_strict": 0,
            "all_folds_strict": 0,
            "exact_replay_queue": 0,
            "path_fleet_rows_appended": 1,
        },
        "results": [],
        "source_archive": {
            "raw_path": "raw.json",
            "raw_sha256": "abc",
            "raw_bytes": 123,
            "gzip_path": "raw.json.gz",
        },
    }
    text = compact.markdown(payload)
    assert "raw.json.gz" in text
    assert "abc" in text
