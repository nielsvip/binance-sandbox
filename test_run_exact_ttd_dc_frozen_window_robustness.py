import json

from tools import persym_baseline_campaign as psc
from tools import run_exact_ttd_dc_frozen_window_robustness as runner


def test_folds_are_preregistered_chronological_and_cover_fixed_c4_window():
    assert runner.FOLDS == (
        {
            "fold": 1,
            "start_inclusive": "2024-01-01",
            "end_exclusive": "2024-11-01",
        },
        {
            "fold": 2,
            "start_inclusive": "2024-11-01",
            "end_exclusive": "2025-09-01",
        },
        {
            "fold": 3,
            "start_inclusive": "2025-09-01",
            "end_exclusive": "2026-07-25",
        },
    )


def test_control_changes_only_preregistered_exit_switches():
    pair = runner.recipes()
    changed = runner.recipe_difference()
    assert set(changed) == set(runner.CONTROLLED_EXIT_SWITCHES)
    assert all(
        changed[key]["control"] is False
        for key in runner.CONTROLLED_EXIT_SWITCHES
    )
    assert pair["anchor"]["MTF_DC_REJECT_EXIT_TF"] == "1h"
    assert pair["anchor"]["MTF_DC_REJECT_EXIT_LOOKBACK"] == 5
    assert pair["anchor"]["TRADIER_MIN_HOLD_MINUTES"] == 1440.0
    assert pair["same_entry_all_exits_off"]["DELTA_ENGINE_ENABLED"] is True


def test_manifest_is_read_only_and_hashes_full_recipes():
    payload = runner.manifest()
    assert payload["matrix_written"] is False
    assert payload["live_written"] is False
    assert payload["promotion_allowed"] is False
    assert payload["expected_contract_fingerprint"].endswith(
        "eae9ee5cfc2b2cde46433c98cb54334bc826f899a23148611f9e2403a6ae77de"
    )
    for name, values in runner.recipes().items():
        assert payload["recipes"][name]["overrides_sha256"] == (
            runner.sha256_json(values)
        )
        assert json.dumps(values, sort_keys=True)
    assert payload["recipes"]["anchor"]["overrides_sha256"] == (
        "50de8f70e283b61f4cadf29a4b8bc02861d70576903620d9ebdcb3a352788b0f"
    )


def test_aggregate_is_equal_capital_and_span_weighted():
    def leg(gain, bh, tim, span, closes, violations=0):
        return {
            "metrics": {
                "acc_gain_pct": gain,
                "bh_pct": bh,
                "time_in_mkt_pct": tim,
            },
            "window_data": {"span_seconds": span},
            "lifecycle": {
                "real_closes": closes,
                "reentry_violations": violations,
                "reentry_pending": 0,
                "reclaim_pending": 0,
            },
            "capacity": {
                "max_requested_mult": 8,
                "requested_fill_ratio": 0.995,
                "size_clamp_count": 2,
            },
            "capacity_respected": True,
            "expected_capacity_saturation": True,
        }

    got = runner.aggregate_legs(
        [leg(10, 2, 50, 1, 3), leg(20, 4, 75, 2, 4), leg(30, 6, 100, 1, 5)]
    )
    assert got["return_pct_equal_capital_mean"] == 20
    assert got["side_bh_pct_equal_capital_mean"] == 4
    assert got["return_multiple_vs_side_bh_signed"] == 5
    assert got["time_in_mkt_pct_span_weighted"] == 75
    assert got["real_closes_sum"] == 12
    assert got["reentry_violations_sum"] == 0
    assert got["size_clamp_count_sum"] == 6


def test_fold_comparison_uses_cash_floor_and_same_entry_control():
    anchor = {
        "fold": 1,
        "metrics": {
            "acc_gain_pct": -19.5,
            "bh_pct": -7.3,
            "time_in_mkt_pct": 55,
        },
        "lifecycle": {"real_closes": 50, "reentry_violations": 0},
        "capacity_respected": True,
    }
    control = {"metrics": {"acc_gain_pct": -7.0}}
    got = runner.fold_comparison(anchor, control)
    assert got["checks"]["beats_side_bh_or_cash"] is False
    assert got["checks"]["beats_same_entry_control"] is False
    assert got["checks"]["tim_in_ranked_key_band"] is True
    assert got["alpha_vs_side_bh_or_cash_pp"] == -19.5
    assert got["strict_pass"] is False


def test_markdown_leads_with_temporal_verdict():
    report = {
        "comparison": {
            "classification": "FAIL_TEMPORAL_ROBUSTNESS",
            "folds": [
                {
                    "fold": 1,
                    "anchor_return_pct": -1.0,
                    "side_bh_pct": -0.5,
                    "same_entry_control_return_pct": -0.4,
                    "alpha_vs_side_bh_or_cash_pp": -1.0,
                    "alpha_vs_same_entry_control_pp": -0.6,
                    "time_in_mkt_pct": 85.0,
                    "strict_pass": False,
                }
            ],
        },
        "aggregate": {
            name: {
                "return_pct_equal_capital_mean": value,
                "side_bh_pct_equal_capital_mean": 1.0,
                "return_multiple_vs_side_bh_signed": value,
                "time_in_mkt_pct_span_weighted": 75.0,
                "real_closes_sum": 2 if name == "anchor" else 0,
                "reentry_violations_sum": 0,
            }
            for name, value in (
                ("anchor", 2.0),
                ("same_entry_all_exits_off", 1.0),
            )
        },
        "legs": [
            {
                "variant": "anchor",
                "fold": 1,
                "lifecycle": {"real_closes": 2, "reentry_violations": 0},
            }
        ],
        "contract_fingerprint": runner.EXPECTED_C4_FINGERPRINT,
        "npz": {"sha256": "npz"},
        "recipes": {"anchor": {"overrides_sha256": "override"}},
    }
    text = runner.render_markdown(report)
    assert "FAIL_TEMPORAL_ROBUSTNESS" in text
    assert "| 1 | -1.0000%" in text
    assert "matrix written: `false`" in text


def test_runner_not_part_of_exact_contract_and_does_not_import_store_connection():
    assert (
        "tools/run_exact_ttd_dc_frozen_window_robustness.py"
        not in psc.MATRIX_CONTRACT_FILES
    )
    source = (
        runner.ROOT
        / "tools"
        / "run_exact_ttd_dc_frozen_window_robustness.py"
    ).read_text()
    assert "prs.connect(" not in source
    assert "upsert_" not in source
    assert "tradier_symbols" not in source
