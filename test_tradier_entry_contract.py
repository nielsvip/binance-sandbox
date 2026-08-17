from tradier_entry_contract import (
    dc_4h_boundary_breached,
    exact_side_allowlist_tradeable,
    flat_key_needs_evaluation,
    flat_key_needs_per_bar_evaluation,
    path_switch,
    per_sym_overlay_is_approved,
)


def test_isolated_wt_dc_routes_flat_key_to_real_process_position():
    assert flat_key_needs_evaluation(
        satoshit_ok=False,
        stdev_ok=False,
        wt_force_open_enabled=False,
        wt_dc_path_enabled=True,
    )


def test_no_entry_family_does_not_waste_exact_engine_call():
    assert not flat_key_needs_evaluation(
        satoshit_ok=False,
        stdev_ok=False,
        wt_force_open_enabled=False,
        wt_dc_path_enabled=False,
    )


def test_existing_upstream_routes_remain_additive():
    for field in (
        "satoshit_ok",
        "stdev_ok",
        "wt_force_open_enabled",
        "ordinary_ladder_enabled",
        "mandatory_reclaim_pending",
        "direct_route_enabled",
    ):
        values = {
            "satoshit_ok": False,
            "stdev_ok": False,
            "wt_force_open_enabled": False,
            "wt_dc_path_enabled": False,
            "ordinary_ladder_enabled": False,
            "mandatory_reclaim_pending": False,
            "direct_route_enabled": False,
        }
        values[field] = True
        assert flat_key_needs_evaluation(**values)


def test_pending_reclaim_routes_even_when_every_fresh_entry_family_is_off():
    assert flat_key_needs_evaluation(
        satoshit_ok=False,
        stdev_ok=False,
        wt_force_open_enabled=False,
        wt_dc_path_enabled=False,
        mandatory_reclaim_pending=True,
    )


def test_ladder_and_reclaim_bypass_generic_candidate_cadence():
    assert flat_key_needs_per_bar_evaluation(
        ordinary_ladder_enabled=True,
        mandatory_reclaim_pending=False,
    )
    assert flat_key_needs_per_bar_evaluation(
        ordinary_ladder_enabled=False,
        mandatory_reclaim_pending=True,
    )
    assert not flat_key_needs_per_bar_evaluation(
        ordinary_ladder_enabled=False,
        mandatory_reclaim_pending=False,
    )


def test_shared_direct_route_is_admitted_on_every_bar_without_unrelated_trigger():
    assert flat_key_needs_evaluation(
        satoshit_ok=False,
        stdev_ok=False,
        wt_force_open_enabled=False,
        wt_dc_path_enabled=False,
        direct_route_enabled=True,
    )
    assert flat_key_needs_per_bar_evaluation(
        ordinary_ladder_enabled=False,
        mandatory_reclaim_pending=False,
        direct_route_enabled=True,
    )


def test_seed_close_flat_key_is_not_stranded_after_reclaim_latch():
    # The exact-engine lifecycle that regressed on TTD_SHORT: the seed is
    # active, a full exit latches reclaim state, and the now-flat key must be
    # routed on the very next RTH bar even with every fresh entry family off.
    obligation = {"pending": True}
    routed = flat_key_needs_evaluation(
        satoshit_ok=False,
        stdev_ok=False,
        wt_force_open_enabled=False,
        wt_dc_path_enabled=False,
        mandatory_reclaim_pending=obligation["pending"],
    )
    per_bar = flat_key_needs_per_bar_evaluation(
        ordinary_ladder_enabled=False,
        mandatory_reclaim_pending=obligation["pending"],
    )
    assert routed
    assert per_bar


def test_exact_side_allowlist_does_not_inherit_mutable_live_disable_overlay():
    assert exact_side_allowlist_tradeable(
        "TTD",
        "SHORT",
        long_symbols=[],
        short_symbols=["TTD"],
    )
    assert not exact_side_allowlist_tradeable(
        "TTD",
        "LONG",
        long_symbols=[],
        short_symbols=["TTD"],
    )


def test_exact_side_allowlist_preserves_safety_blocks():
    common = {"long_symbols": ["MU"], "short_symbols": ["TTD"]}
    assert not exact_side_allowlist_tradeable(
        "TTD", "SHORT", **common, non_shortable=["TTD"]
    )
    assert not exact_side_allowlist_tradeable(
        "MU", "LONG", **common, blacklist=["MU"]
    )


def test_explicit_path_switch_respects_false_and_missing_default():
    class Config:
        WT_DC_EXIT_ENABLED = False

    assert not path_switch(Config(), "WT_DC_EXIT_ENABLED", True)
    assert path_switch(Config(), "MISSING_PATH", True)


def test_trc_requires_trb_approval_and_positive_7d_overlay():
    trb = {"LEXX_LONG": {"wsharpe": 0.4}}
    trc_without_result = {"LEXX_LONG": {"wsharpe": 0.0, "sample_tag": "BELOW_THRESHOLD"}}
    trc_with_result = {"LEXX_LONG": {"wsharpe": 0.2}}

    assert not per_sym_overlay_is_approved(
        "LEXX", "LONG", trb_configs=trb, trc_configs=trc_without_result,
        trb_symbols=["LEXX"],
    )
    assert per_sym_overlay_is_approved(
        "LEXX", "LONG", trb_configs=trb, trc_configs=trc_with_result,
        trb_symbols=["LEXX"],
    )
    assert not per_sym_overlay_is_approved(
        "LEXX", "LONG", trb_configs=trb, trc_configs=trc_with_result,
        trb_symbols=[],
    )


def test_dc_4h_boundary_blocks_entries_and_forces_exits():
    indicators = {"dc_low_4h": 10.0, "dc_high_4h": 20.0}
    assert dc_4h_boundary_breached(9.99, "LONG", indicators, require_level=True)[0]
    assert dc_4h_boundary_breached(20.0, "SHORT", indicators, require_level=True)[0]
    assert not dc_4h_boundary_breached(10.01, "LONG", indicators, require_level=True)[0]
    assert dc_4h_boundary_breached(10.0, "LONG", {}, require_level=True)[0]
    assert not dc_4h_boundary_breached(10.0, "LONG", {}, require_level=False)[0]
