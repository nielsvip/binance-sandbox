from tools import analyze_r5_tim50_requalification as analyze


def test_tim_55_is_recompute_priority_not_qualification():
    source = {"results": [{
        "key": "ABC_LONG", "entry": {"family": "E"}, "exit": {"family": "X"},
        "folds": [{
            "fold": 1, "strategy_return_pct": 10.0, "bh_return_pct": 2.0,
            "alpha_vs_bh_pp": 8.0, "alpha_vs_same_entry_e02_pp": 1.0,
            "real_close_trades": 11, "weighted_tim_pct": 55.0,
            "emergency_exit_share": 0.0,
        }],
    }]}
    report = analyze.build(source)
    row = report["rows"][0]
    assert row["visible_train_metrics_all_pass_50_80"] is True
    assert row["not_a_qualification"] is True
    assert report["strict_survivors"] == 0
