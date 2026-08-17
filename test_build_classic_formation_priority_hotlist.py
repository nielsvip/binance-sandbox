from tools.build_classic_formation_priority_hotlist import positive_bh_multiple


def test_positive_bh_multiple_requires_positive_strategy_and_true_multiple():
    assert positive_bh_multiple({"return_pct": 12, "bh_return_pct": 4}) == (
        True,
        3.0,
    )
    assert positive_bh_multiple({"return_pct": 3, "bh_return_pct": 4}) == (
        False,
        0.75,
    )
    assert positive_bh_multiple({"return_pct": 3, "bh_return_pct": -4}) == (
        True,
        None,
    )
    assert positive_bh_multiple({"return_pct": -3, "bh_return_pct": -4}) == (
        False,
        None,
    )
