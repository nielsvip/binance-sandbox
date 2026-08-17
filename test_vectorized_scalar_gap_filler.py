from tools import vectorized_scalar_gap_filler as filler


def test_vector_cells_only_returns_reviewed_empty_scalar_paths():
    manifest = {
        "READY": {"sweepable": True, "test_values": [True, False]},
        "EXACT_ONLY": {"sweepable": True, "test_values": [1, 2]},
        "GROUP_COMBO": {"sweepable": True, "test_values": ["r1"]},
        "RUNTIME": {"sweepable": False, "test_values": [1]},
    }
    paths = [
        {
            "param": "READY",
            "differential_readiness": "READY_VECTOR_SCREEN_THEN_EXACT",
            "screen_backends": ["VECTOR_DIAGNOSTIC", "EXACT_V8"],
            "deployment_scope": "GLOBAL_ONLY",
        },
        {
            "param": "EXACT_ONLY",
            "differential_readiness": "READY_EXACT_ONLY",
            "screen_backends": ["EXACT_V8"],
            "deployment_scope": "GLOBAL_ONLY",
        },
        {
            "param": "GROUP_COMBO",
            "differential_readiness": "READY_VECTOR_SCREEN_THEN_EXACT",
            "screen_backends": ["VECTOR_DIAGNOSTIC", "EXACT_V8"],
            "deployment_scope": "GLOBAL_ONLY",
        },
        {
            "param": "RUNTIME",
            "differential_readiness": "READY_VECTOR_SCREEN_THEN_EXACT",
            "screen_backends": ["VECTOR_DIAGNOSTIC", "EXACT_V8"],
            "deployment_scope": "GLOBAL_ONLY",
        },
    ]
    protected = {("READY", "true", "MU_LONG")}

    cells, inventory = filler.vector_cells(
        manifest,
        paths,
        {"READY", "EXACT_ONLY", "GROUP_COMBO", "RUNTIME"},
        protected,
        "MU_LONG",
    )

    assert cells == [("READY", False)]
    assert inventory == {
        "eligible_vector_cells": 2,
        "protected_exact_cells": 1,
        "empty_vector_cells": 1,
    }


def test_binding_probe_requires_both_vector_and_exact_backends():
    manifest = {
        "PROBE": {"sweepable": True, "test_values": [0, 1]},
    }
    paths = [
        {
            "param": "PROBE",
            "differential_readiness": "BINDING_PROBE_REQUIRED",
            "screen_backends": ["VECTOR_DIAGNOSTIC"],
            "deployment_scope": "GLOBAL_ONLY",
        }
    ]

    cells, inventory = filler.vector_cells(
        manifest,
        paths,
        {"PROBE"},
        set(),
        "TTD_SHORT",
    )

    assert cells == []
    assert inventory["eligible_vector_cells"] == 0


def test_behavior_fingerprint_changes_with_event_or_return():
    baseline = filler.behavior_fingerprint(
        [{"kind": "CLOSE", "ts": 1}],
        [1.0],
    )

    assert baseline != filler.behavior_fingerprint(
        [{"kind": "CLOSE", "ts": 2}],
        [1.0],
    )
    assert baseline != filler.behavior_fingerprint(
        [{"kind": "CLOSE", "ts": 1}],
        [2.0],
    )


def test_reviewed_exact_only_adapter_is_vector_eligible():
    manifest = {
        "BREAKOUT_SIZE_EMA200_T2_PCT": {
            "sweepable": True,
            "test_values": [0.5, 1.0],
        },
    }
    paths = [
        {
            "param": "BREAKOUT_SIZE_EMA200_T2_PCT",
            "differential_readiness": "READY_EXACT_ONLY",
            "screen_backends": ["EXACT_V8"],
            "deployment_scope": "GLOBAL_ONLY",
        },
    ]

    cells, inventory = filler.vector_cells(
        manifest,
        paths,
        {"BREAKOUT_SIZE_SMA200_T2_PCT"},
        set(),
        "MU_LONG",
    )

    assert cells == [
        ("BREAKOUT_SIZE_EMA200_T2_PCT", 0.5),
        ("BREAKOUT_SIZE_EMA200_T2_PCT", 1.0),
    ]
    assert inventory["eligible_vector_cells"] == 2


def test_unreviewed_exact_only_name_similarity_is_not_an_adapter():
    manifest = {
        "MIN_HOLD_MINUTES_TRADIER": {
            "sweepable": True,
            "test_values": [60],
        },
    }
    paths = [
        {
            "param": "MIN_HOLD_MINUTES_TRADIER",
            "differential_readiness": "READY_EXACT_ONLY",
            "screen_backends": ["EXACT_V8"],
            "deployment_scope": "GLOBAL_ONLY",
        },
    ]

    cells, _ = filler.vector_cells(
        manifest,
        paths,
        {"TRADIER_MIN_HOLD_MINUTES"},
        set(),
        "MU_LONG",
    )

    assert cells == []


def test_breakout_adapter_changes_only_reviewed_vector_field():
    adapted, applied = filler.adapt_vector_overrides(
        {
            "BREAKOUT_SIZE_EMA200_T2_MULT": 2.5,
            "UNRELATED": True,
        }
    )

    assert adapted == {
        "BREAKOUT_SIZE_SMA200_T2_MULT": 2.5,
        "UNRELATED": True,
    }
    assert applied == {
        "BREAKOUT_SIZE_EMA200_T2_MULT": "BREAKOUT_SIZE_SMA200_T2_MULT"
    }


def test_breakout_adapter_rejected_after_sampled_parity_mismatch():
    adapted, applied = filler.adapt_vector_overrides(
        {"BREAKOUT_SIZE_EMA200_T1_MULT": 1.875}
    )

    assert adapted == {"BREAKOUT_SIZE_EMA200_T1_MULT": 1.875}
    assert applied == {}


def test_ranked_exact_candidates_prioritizes_delta_then_sharpe(tmp_path):
    path = tmp_path / "rows.jsonl"
    rows = [
        {
            "key": "MU_LONG",
            "status": "MOVED",
            "param": "A",
            "value_json": "1",
            "delta_gain_mo_vs_bh_diagnostic": 2.0,
            "single_key_trade_sharpe_diagnostic": 0.5,
            "max_dd_pct": 4.0,
        },
        {
            "key": "MU_LONG",
            "status": "MOVED",
            "param": "B",
            "value_json": "2",
            "delta_gain_mo_vs_bh_diagnostic": 3.0,
            "single_key_trade_sharpe_diagnostic": 0.1,
            "max_dd_pct": 8.0,
        },
        {
            "key": "MU_LONG",
            "status": "INERT",
            "param": "C",
            "value_json": "3",
            "delta_gain_mo_vs_bh_diagnostic": 99.0,
        },
    ]
    path.write_text("\n".join(__import__("json").dumps(row) for row in rows))

    ranked = filler.ranked_exact_candidates(path, "MU_LONG")

    assert [row["param"] for row in ranked] == ["B", "A"]
    assert all(row["exact_completion_credit"] is False for row in ranked)
    assert all(row["required_next_stage"] == "EXACT_V8" for row in ranked)
