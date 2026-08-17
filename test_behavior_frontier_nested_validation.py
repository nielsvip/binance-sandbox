from tools.run_behavior_frontier_nested_validation import _freeze_one_final_candidate


def row(candidate_id: str, *, strict: bool, distinct: bool = True) -> dict:
    return {
        "candidate_id": candidate_id,
        "strict_gate": strict,
        "behavior_distinct_in_fold": distinct,
    }


def test_no_strict_train_and_holdout_intersection_reads_no_final() -> None:
    train = [row("train_only", strict=True), row("near_miss", strict=False)]
    holdout = [row("train_only", strict=False), row("near_miss", strict=True)]
    assert _freeze_one_final_candidate(train, holdout) == []


def test_freezes_exactly_one_in_train_rank_order() -> None:
    train = [row("first", strict=True), row("second", strict=True)]
    holdout = [row("second", strict=True), row("first", strict=True)]
    assert _freeze_one_final_candidate(train, holdout) == ["first"]


def test_non_distinct_candidate_cannot_reach_final() -> None:
    train = [row("duplicate", strict=True, distinct=False)]
    holdout = [row("duplicate", strict=True)]
    assert _freeze_one_final_candidate(train, holdout) == []
