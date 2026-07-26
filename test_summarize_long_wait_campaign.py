import json

from tools import summarize_long_wait_campaign as summary


def test_short_report_separates_nested_sum_from_final_side_specific_oos(tmp_path):
    artifact = tmp_path / (
        "entry_overlay_ENTRY_BOUNCE_15M_LOW_20260726T000000Z_ALB_SHORT"
    )
    artifact.mkdir()
    result = {
        "manifest": {
            "family": "ENTRY_BOUNCE_15M_LOW",
            "symbol": "ALB",
            "side": "SHORT",
        },
        "aggregate": {
            "candidate_capital_return_pct_sum": -667.6,
            "bh_capital_return_pct_sum": -82.3,
            "control_capital_return_pct_sum": -818.4,
            "weighted_tim_pct": 56.4,
            "entry_signal_rows": 10,
            "entry_request_count": 5,
            "entry_fill_count": 4,
            "future_htf_count": 0,
            "all_folds_beat_bh": False,
            "all_folds_beat_control": False,
            "all_folds_exposure_policy_pass": False,
            "vector_survivor": False,
        },
        "outer_folds": [
            {
                "fold": 1,
                "validation": ["2026-01-01", "2026-02-01"],
                "beats_bh": True,
                "beats_control": True,
                "validation_metrics": {
                    "end_ts": 2,
                    "capital_return_pct": 21.5,
                    "bh_capital_return_pct": 19.41,
                    "exposure_weighted_tim_pct": 72.0,
                    "exit_count": 3,
                },
                "same_frozen_ladder_e02_control": {
                    "capital_return_pct": 3.87
                },
                "selected_candidate": {},
            }
        ],
    }
    (artifact / "result.json").write_text(json.dumps(result))
    payload = summary.summarize(tmp_path)
    row = payload["rows"][0]
    assert row["side"] == "SHORT"
    assert row["bh_return_pct"] == 19.41
    assert row["control_return_pct"] == 3.87
    assert row["metric_scope"] == "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD"
    assert row["return_unit"] == "CAPITAL_RETURN_PCT"
    assert row["aggregate_metrics"]["bh_return_pct"] == -82.3
    assert row["aggregate_metrics"]["metric_scope"] == (
        "NESTED_OUTER_VALIDATION_FOLD_AGGREGATE"
    )
    assert row["aggregate_metrics"]["return_unit"] == (
        "SUM_OF_FOLD_CAPITAL_RETURN_PCT"
    )
