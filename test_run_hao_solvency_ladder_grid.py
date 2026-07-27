from tools.run_hao_solvency_ladder_grid import (
    TF_PROFILES,
    _candidate_id,
    freeze_discovery,
    preregistered_settings,
    scan_e05_candidate,
)


def _row(candidate_id, family, score, final=None):
    row = {
        "candidate_id": candidate_id,
        "exit_family": family,
        "ladder_setting_index": 0,
        "ladder_setting": {},
        "discovery_all_folds_strict": score > 8,
        "discovery_fold_evidence": [
            {
                "fold_gate_pass": score > 8,
                "insolvent": False,
                "weighted_tim_pct": 75.0,
                "alpha_vs_same_entry_e02_pp": score,
                "alpha_vs_bh_pp": score,
                "max_drawdown_account_pct": 20.0,
            },
            {
                "fold_gate_pass": score > 8,
                "insolvent": False,
                "weighted_tim_pct": 75.0,
                "alpha_vs_same_entry_e02_pp": score,
                "alpha_vs_bh_pp": score,
                "max_drawdown_account_pct": 20.0,
            },
        ],
    }
    if final is not None:
        row["untouched_final_validation"] = final
    return row


def test_grid_is_bounded_preregistered_and_stable():
    rows = preregistered_settings()
    assert len(rows) == 75
    assert set(row["tf_profile"] for row in rows) == set(TF_PROFILES)
    assert min(row["global_scale"] for row in rows) == 0.5
    assert max(row["delivered_cap_mult"] for row in rows) == 8.0
    assert len(
        {
            _candidate_id("EXIT_E02_DONCHIAN", row)
            for row in rows
        }
    ) == 75


def test_freeze_ignores_later_final_mutation():
    rows = []
    for family in ("A", "B", "C"):
        for index in range(10):
            rows.append(_row(f"{family}{index}", family, index))
    left = [row["candidate_id"] for row in freeze_discovery(rows)]
    mutated = [
        {**row, "not_a_final_field": 1_000_000 - index}
        for index, row in enumerate(rows)
    ]
    right = [row["candidate_id"] for row in freeze_discovery(mutated)]
    assert left == right


def test_freeze_rejects_final_fold_fields():
    rows = [_row("A", "A", 10, final={"return": 999})]
    try:
        freeze_discovery(rows)
    except ValueError as exc:
        assert "final-fold" in str(exc)
    else:
        raise AssertionError("freeze accepted final-fold data")


def test_one_e05_candidate_uses_validated_compiled_execution_route():
    calls = []

    class Execution:
        @staticmethod
        def _scan(data, ctx, events, commission, slippage, side):
            calls.append((data, ctx, events, commission, slippage, side))
            return {"capital_return_pct": 12.5}

    class E05:
        execution = Execution

    result = scan_e05_candidate(
        E05,
        "data",
        {"fold": 1},
        "events",
        0.0005,
        0.0002,
        "SHORT",
    )
    assert result == {"capital_return_pct": 12.5}
    assert calls == [
        ("data", {"fold": 1}, "events", 0.0005, 0.0002, "SHORT")
    ]
