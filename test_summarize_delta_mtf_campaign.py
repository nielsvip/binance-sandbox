import json

from tools.summarize_delta_mtf_campaign import summarize


def _write_result(root, fold_tim):
    artifact = root / "entry_overlay_ENTRY_DELTA_MTF_20260726T000000Z_VLO_LONG"
    artifact.mkdir()
    candidate = {
        "params": {
            "min_favorable_tfs": 2,
            "directional_retention_ratio": 0.5,
            "structural_gate": False,
        }
    }
    payload = {
        "manifest": {
            "symbol": "VLO",
            "side": "LONG",
            "delta_input_audit": {"future_htf_count": 0},
        },
        "aggregate": {
            "candidate_capital_return_pct_sum": 600.0,
            "bh_capital_return_pct_sum": 100.0,
            "control_capital_return_pct_sum": 400.0,
            "weighted_tim_pct": 75.0,
            "all_folds_beat_bh": True,
            "all_folds_beat_control": True,
            "all_mandatory_reclaim": True,
            "future_htf_count": 0,
        },
        "outer_folds": [
            {
                "selected_candidate": candidate,
                "validation_metrics": {
                    "exposure_weighted_tim_pct": tim,
                    "fill_count": 1,
                    "exit_count": 1,
                },
            }
            for tim in fold_tim
        ],
    }
    (artifact / "result.json").write_text(json.dumps(payload))


def test_aggregate_tim_cannot_hide_fold_exposure_failures(tmp_path):
    _write_result(tmp_path, [33.68, 89.00, 96.55])

    row = summarize(tmp_path)["rows"][0]

    assert row["weighted_tim_pct"] == 75.0
    assert row["all_folds_exposure_policy_pass"] is False
    assert row["exposure_policy_pass"] is False
    assert row["status"] == "DISCARD_GRAY"


def test_survivor_requires_every_fold_inside_tim_policy(tmp_path):
    _write_result(tmp_path, [70.0, 75.0, 80.0])

    row = summarize(tmp_path)["rows"][0]

    assert row["all_folds_exposure_policy_pass"] is True
    assert row["exposure_policy_pass"] is True
    assert row["status"] == "VECTOR_SURVIVOR"
