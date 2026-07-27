from tools.ingest_mu_ladder_stability_fleet import _weighted_tim


def test_weighted_tim_uses_rows_not_mean_of_fold_percentages():
    folds = [
        {"metrics": {"rows": 1, "exposure_weighted_tim_pct": 0.0}},
        {"metrics": {"rows": 3, "exposure_weighted_tim_pct": 100.0}},
    ]
    assert _weighted_tim(folds) == 75.0
