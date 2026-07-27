from tools.ingest_short_guard_exact import exact_metric_scope


def test_exact_short_metric_scope_is_fixed_2k_final_with_both_tim_units():
    summary = {
        "audit": {
            "schedule": {
                "time_in_market": {
                    "actual_binary_pct": 4.2142468733,
                    "actual_weighted_pct": 0.7631200384,
                }
            }
        }
    }
    spec = {
        "fold_boundaries": {
            "validation_start": "2026-01-01",
            "validation_end_exclusive": "2026-07-25",
        }
    }

    scope = exact_metric_scope(summary, spec)

    assert scope["metric_scope"] == (
        "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD"
    )
    assert scope["return_unit"] == "FIXED_2000_USD_CAPITAL_RETURN_PCT"
    assert scope["capital_base_usd"] == 2000
    assert scope["tim_binary_pct"] == 4.2142468733
    assert scope["tim_weighted_pct"] == 0.7631200384
    assert scope["validation_window"]["start"] == "2026-01-01"
