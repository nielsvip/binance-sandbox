import json
from tools import run_vector_approx_scalar as runner


def test_seen_decodes_persisted_value_json(tmp_path):
    path = tmp_path / "rows.jsonl"
    rows = [
        {
            "key": "MU_LONG",
            "status": "MOVED",
            "param": "BOOL_PARAM",
            "value_json": "true",
        },
        {
            "key": "MU_LONG",
            "status": "INERT",
            "param": "NUMBER_PARAM",
            "value_json": "1.5",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    seen = runner._seen(path, "MU_LONG")

    assert ("BOOL_PARAM", runner.scalar_filler.norm_value(True), "MU_LONG") in seen
    assert ("NUMBER_PARAM", runner.scalar_filler.norm_value(1.5), "MU_LONG") in seen


def test_ranked_candidates_excludes_exact_present_and_non_moved(tmp_path):
    path = tmp_path / "rows.jsonl"
    rows = [
        {
            "key": "MU_LONG",
            "tier": "VEC_APPROX",
            "status": "MOVED",
            "param": "A",
            "value_json": "1",
            "protected_exact_present": False,
            "delta_gain_mo_vs_bh_approx": 2.0,
            "single_key_trade_sharpe_approx": 0.5,
            "max_dd_pct_approx": 4.0,
            "approximation": {
                "source_rank": 0.5,
                "proxy_value": 10.0,
                "proxy_overrides": {"X": 10.0},
                "approximation_confidence": "LOW",
                "approximation_mismatch_class": "FAMILY_PROXY",
                "action_group": "ENTRY",
            },
        },
        {
            "key": "MU_LONG",
            "tier": "VEC_APPROX",
            "status": "MOVED",
            "param": "B",
            "value_json": "2",
            "protected_exact_present": True,
            "delta_gain_mo_vs_bh_approx": 99.0,
            "approximation": {},
        },
        {
            "key": "MU_LONG",
            "tier": "VEC_APPROX",
            "status": "INERT",
            "param": "C",
            "value_json": "3",
            "protected_exact_present": False,
            "delta_gain_mo_vs_bh_approx": 100.0,
            "approximation": {},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    ranked = runner.ranked_candidates(path, "MU_LONG")

    assert [row["param"] for row in ranked] == ["A"]
    assert ranked[0]["vector_evidence_class"] == "VEC_APPROX"
    assert ranked[0]["exact_completion_credit"] is False
    assert ranked[0]["promotion_allowed"] is False


def test_provider_filter_keeps_ownership_disjoint(monkeypatch):
    entry_registry = {
        "ENTRY_PARAM": {"action_group": "ENTRY"},
    }
    other_registry = {
        "OTHER_PARAM": {"action_group": "OTHER"},
    }
    monkeypatch.setattr(
        runner.entry_approx,
        "inventory",
        lambda *_args, **_kwargs: {
            "entry_exact_only_cells": 1,
            "approx_eligible_cells": 1,
            "mapped_pct": 100.0,
            "cells": [{"param": "ENTRY_PARAM", "value": "true"}],
        },
    )
    monkeypatch.setattr(
        runner,
        "_current_other_sizing_cells",
        lambda *_args, **_kwargs: [
            {
                "provider": "OTHER_SIZING",
                "param": "OTHER_PARAM",
                "value": "1",
            }
        ],
    )

    entry_cells, _ = runner.discovery_cells(
        "NVDA_LONG",
        entry_registry=entry_registry,
        other_registry=other_registry,
        providers={"ENTRY_AUGMENT"},
    )
    other_cells, _ = runner.discovery_cells(
        "NVDA_LONG",
        entry_registry=entry_registry,
        other_registry=other_registry,
        action_groups={"OTHER"},
        providers={"OTHER_SIZING"},
    )

    assert entry_cells
    assert other_cells
    assert {row["provider"] for row in entry_cells} == {"ENTRY_AUGMENT"}
    assert {row["provider"] for row in other_cells} == {"OTHER_SIZING"}
    assert {
        (row["param"], runner.scalar_filler.norm_value(row["value"]))
        for row in entry_cells
    }.isdisjoint(
        {
            (row["param"], runner.scalar_filler.norm_value(row["value"]))
            for row in other_cells
        }
    )
