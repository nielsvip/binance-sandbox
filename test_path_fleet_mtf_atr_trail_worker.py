from tools.path_fleet_mtf_atr_trail_worker import _paired_confirmation_rows


def test_pairs_exact_min2_to_min1_not_independent_winners():
    candidates = []
    for atr_mult in (1.5, 2.0, 2.5, 3.0, 4.0):
        for profit in (0.0, 0.25, 0.5, 1.0):
            for confirming in (1, 2):
                value = atr_mult * 10 + profit + confirming
                candidates.append(
                    {
                        "family": "EXIT_MTF_ATR_TRAIL",
                        "params": {
                            "atr_mult": atr_mult,
                            "min_profit_pct": profit,
                            "min_confirming_tfs": confirming,
                        },
                        "nested": {
                            "validation": {
                                "capital_return_pct_sum": value,
                                "exit_fills": confirming,
                            },
                            "robust_discovery_all_folds": True,
                            "robust_validation_fold": True,
                        },
                    }
                )
    rows = _paired_confirmation_rows({"candidates": candidates})
    assert len(rows) == 20
    assert all(row["incremental_min2_vs_min1_pp"] == 1.0 for row in rows)
    assert all(row["min1_exit_fills"] == 1 for row in rows)
    assert all(row["min2_exit_fills"] == 2 for row in rows)
