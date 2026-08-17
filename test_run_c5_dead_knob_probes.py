from __future__ import annotations

from pathlib import Path

from c5_dead_knob_probe_contract import PROBE_SPECS
from tools.run_c5_dead_knob_probes import (
    _receipt_matches,
    build_plan,
    build_recipe,
    recipe_differential,
    render_plan_markdown,
)


def test_primary_plan_is_c5_only_pairwise_and_never_canonical():
    plan = build_plan()
    assert plan["contract_scope"] == "c5-current-only"
    assert plan["campaign"] == "stocks_repaired_20260730_c5"
    assert plan["canonical_db_writes"] is False
    assert plan["canonical_matrix_writes"] is False
    assert plan["s1_writes"] is False
    assert len(plan["rows"]) == 11
    assert plan["exact_engine_legs"] == 22
    assert plan["excluded_prior_evidence"] == [
        {
            "param": "STRUCTURAL_RANGE_SHIFT_K_LOW",
            "evidence_scope": "NVDA_LONG/VT_LONG c4 collision rows",
            "classification": "WRONG_SIDE_INAPPLICABLE",
            "scheduled_exact_legs": 0,
            "reason": (
                "K_LOW is consumed only by the SHORT structural "
                "range-bottom branch."
            ),
        }
    ]
    for row in plan["rows"]:
        assert row["only_recipe_differential"] == [row["param"]]
        assert row["ranked_tim_contract"]["tim_min_pct"] in {20.0, 50.0}
        assert row["ranked_tim_contract"]["tim_max_pct"] in {60.0, 80.0}


def test_every_recipe_differs_only_at_target_and_enables_dependencies():
    for spec in PROBE_SPECS.values():
        low = build_recipe(spec, spec.low)
        high = build_recipe(spec, spec.high)
        assert recipe_differential(low, high, spec.param) == (spec.param,)
        for dependency in spec.activation_dependencies:
            assert low[dependency] is True
            assert high[dependency] is True


def test_all_keys_plan_never_registers_wrong_side_srs():
    plan = build_plan(all_keys=True)
    srs = [
        row
        for row in plan["rows"]
        if row["param"] == "STRUCTURAL_RANGE_SHIFT_K_LOW"
    ]
    assert {row["position_key"] for row in srs} == {
        "TTD_SHORT",
        "ACN_SHORT",
    }
    assert all(row["side"] == "SHORT" for row in srs)


def test_markdown_names_c5_tim_and_nonwriting_contract():
    rendered = render_plan_markdown(build_plan())
    assert "current c5 only" in rendered
    assert "50–80%" in rendered
    assert "20–60%" in rendered
    assert "does not write the canonical result DB" in rendered
    assert "WRONG_SIDE_C4_EVIDENCE" in rendered
    assert "`TTD_SHORT`" in rendered


def test_partial_or_unbound_receipt_is_never_reusable():
    spec = PROBE_SPECS["STDEV_REJECT_EXIT_ZONE"]
    binding = {
        "contract_fingerprint": "tradier-matrix-exec-c5-20260730:abc",
        "npz_sha256": "a" * 64,
        "override_sha256": "b" * 64,
        "window_start": "2026-05-01",
        "window_end_exclusive": "2026-07-21",
    }
    receipt = {
        **binding,
        "receipt_complete": True,
        "param": spec.param,
        "position_key": "VT_LONG",
        "requested_value": spec.low,
    }
    assert _receipt_matches(
        receipt,
        spec=spec,
        key="VT_LONG",
        value=spec.low,
        binding=binding,
    )
    for missing in binding:
        partial = dict(receipt)
        partial.pop(missing)
        assert not _receipt_matches(
            partial,
            spec=spec,
            key="VT_LONG",
            value=spec.low,
            binding=binding,
        )


def test_source_audit_matches_current_delta_consumers():
    source = (Path(__file__).parent / "wt_dc_delta.py").read_text()
    manager_source = (
        Path(__file__).parent / "tradier_manage.py"
    ).read_text()
    assert 'cfg["exit_speed_decay_pct"]' in source
    assert 'cfg["exit_min_tf_lost"]' in source
    assert 'cfg.get("exit_accel_threshold", -0.1)' in source
    assert 'cfg.get("exit_opposing_ratio", 1.0)' in source
    assert 'cfg.get("exit_min_hold", 4)' in source
    assert '"held_bars": float(hold_time_min) / 15.0' in manager_source
