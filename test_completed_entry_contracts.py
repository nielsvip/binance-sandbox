"""Durable pytest coverage for every not-yet-installed shared entry contract."""

from bounce_donchian_contract import BounceInputs, CausalScalar as BounceScalar, evaluate_bounce_donchian_direct
from stoch_hhhl_contract import CompletedParentSnapshot, evaluate_stoch_hhhl_direct
from stoch_parent_contract import CompletedStochParent, evaluate_completed_stoch_direct
from wt_dc_contract import CausalPair, CausalScalar as WTScalar, WTDCInputs, evaluate_wt_dc_direct
from tools.hotlist_v8_full_recipe_routes import full_recipe_route_audit
from tools.run_hotlist_v8_full_recipe import normalized_complete_recipe


def test_hhhl_completed_parent_episode_side_and_fail_closed_mutation():
    snap = CompletedParentSnapshot("1h", 1000, 11, 10, 9, 8, 15, 10)
    first = evaluate_stoch_hhhl_direct(
        {"1h": snap}, side="LONG", enabled_tfs=["1h"], min_confirming_tfs=1,
        stoch_threshold=20, asof_ts=1200,
    )
    assert first.episode_start
    assert not evaluate_stoch_hhhl_direct(
        {"1h": snap}, side="LONG", enabled_tfs=["1h"], min_confirming_tfs=1,
        stoch_threshold=20, asof_ts=1200, prior_state=first.next_state,
    ).episode_start
    mutated = evaluate_stoch_hhhl_direct(
        {"1h": CompletedParentSnapshot("1h", 1000, 12, 10, 9, 8, 15, 10)},
        side="LONG", enabled_tfs=["1h"], min_confirming_tfs=1,
        stoch_threshold=20, asof_ts=1200, prior_state=first.next_state,
    )
    assert "MUTATED_COMPLETED_PARENT:1h" in mutated.blockers
    short = evaluate_stoch_hhhl_direct(
        {"1h": CompletedParentSnapshot("1h", 1000, 9, 10, 7, 8, 85, 90)},
        side="SHORT", enabled_tfs=["1h"], min_confirming_tfs=1,
        stoch_threshold=20, asof_ts=1200,
    )
    assert short.episode_start


def test_bounce_completed_prior_channel_and_future_input_fail_closed():
    inputs = BounceInputs(BounceScalar(102.4, 1200), BounceScalar(100, 1000))
    first = evaluate_bounce_donchian_direct(
        inputs, side="LONG", timeframe="5m", distance=.025, recovery_only=False,
        confirmation="none", asof_ts=1200,
    )
    assert first.episode_start and abs(first.proximity - .024) < 1e-12
    future = evaluate_bounce_donchian_direct(
        BounceInputs(BounceScalar(102.4, 1200), BounceScalar(100, 1201)),
        side="LONG", timeframe="5m", distance=.025, recovery_only=False,
        confirmation="none", asof_ts=1200,
    )
    assert "FUTURE_COMPLETED_INPUT:prior_channel" in future.blockers


def test_parent_stoch_turn_is_parent_pulse_but_deep_is_level_state():
    turn = CompletedStochParent("1h", 1100, 30, stoch_k_prev=20, previous_source_close_ts=1000)
    first = evaluate_completed_stoch_direct(
        turn, family="ENTRY_1H_TURN_UP", side="LONG", stoch_k_threshold=40,
        turn_definition="rising-vs-prior", asof_ts=1200,
    )
    repeated = evaluate_completed_stoch_direct(
        turn, family="ENTRY_1H_TURN_UP", side="LONG", stoch_k_threshold=40,
        turn_definition="rising-vs-prior", asof_ts=1200, prior_state=first.next_state,
    )
    assert first.event_pulse and first.episode_start and not repeated.eligible
    deep = evaluate_completed_stoch_direct(
        CompletedStochParent("4h", 1100, 40), family="ENTRY_4H_DEEP_VALUE",
        side="SHORT", stoch_k_threshold=65, asof_ts=1200,
    )
    assert deep.eligible and not deep.event_pulse


def test_new_routes_have_runtime_markers_and_exact_param_fail_closed_gates():
    hhhl = full_recipe_route_audit(
        "ENTRY_STOCH_HHHL", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        {"enabled_tfs": ["1h"], "min_confirming_tfs": 1, "stoch_threshold": 20},
    )
    bounce = full_recipe_route_audit(
        "ENTRY_BOUNCE_15M_LOW", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        {"timeframe": "15m", "distance": .015, "recovery_only": False, "confirmation": "none"},
    )
    parent = full_recipe_route_audit(
        "ENTRY_1H_TURN_UP", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        {"stoch_k_threshold": 80, "turn_definition": "rising-vs-prior"},
    )
    assert hhhl["blockers"] == []
    assert bounce["blockers"] == []
    assert parent["blockers"] == []


def test_normalized_recipe_preserves_exact_entry_params_without_substitution():
    params = {"timeframe": "5m", "distance": .025, "recovery_only": False, "confirmation": "none"}
    recipe, source = normalized_complete_recipe(
        {"key": "NKE_SHORT", "entry_params": params},
        {"complete_recipe": {"schema": "complete-vector-lifecycle-recipe-v1", "ENTRY": {"family": "ENTRY_BOUNCE_5M_LOW"}}},
    )
    assert source == "EXPLICIT_ENVELOPE"
    assert recipe["ENTRY"]["params"] == params


def test_wt_dc_exact_score_gates_and_runtime_marker_fail_closed():
    inputs = WTDCInputs(
        CausalPair(2, 1, 1000), CausalPair(2, 1, 1000), WTScalar("BULL", 1100),
        WTScalar(.25, 1100), WTScalar(20, 1200), WTScalar(20, 1200),
    )
    first = evaluate_wt_dc_direct(
        inputs, side="LONG", threshold=20, htf_gate="none", htf_align_required=0,
        combined_stoch_gate=100, asof_ts=1200,
    )
    assert first.score == 100 and first.episode_start
    future = evaluate_wt_dc_direct(
        WTDCInputs(CausalPair(2, 1, 1300), CausalPair(2, 1, 1000), WTScalar(1, 1100), WTScalar(.25, 1100), WTScalar(20, 1200), WTScalar(20, 1200)),
        side="LONG", threshold=20, htf_gate="none", htf_align_required=0,
        combined_stoch_gate=100, asof_ts=1200,
    )
    assert "FUTURE_COMPLETED_INPUT:wt_d" in future.blockers
    audit = full_recipe_route_audit(
        "ENTRY_WT_DC", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        {"threshold": 20, "htf_gate": "none", "htf_align_required": 0, "combined_stoch_gate": 100},
    )
    assert audit["blockers"] == []


def test_wt_dc_none_cross_is_valid_and_can_qualify_from_other_components():
    inputs = WTDCInputs(
        CausalPair(2, 1, 1000), CausalPair(2, 1, 1000), WTScalar("NONE", 1100),
        WTScalar(.25, 1100), WTScalar(80, 1200), WTScalar(80, 1200),
    )
    result = evaluate_wt_dc_direct(
        inputs, side="LONG", threshold=45, htf_gate="none",
        htf_align_required=0, combined_stoch_gate=100, asof_ts=1200,
    )
    assert result.blockers == ()
    assert result.score == 60
    assert result.episode_start


def test_long_wait_and_bottom_b_runtime_markers_are_installed_together():
    audit = full_recipe_route_audit(
        "ENTRY_LONG_WAIT_ENABLED",
        "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED",
        {
            "bounce_timeframe": "15m", "bounce_distance": .008,
            "deep_k4h": 35.0, "turn_k1h": 40.0,
            "confirmation": "stoch5",
        },
        {
            "arm_tf": "1h", "confirm_tf": "5m", "rebound_atr": .25,
            "prebreak_lookback": 2, "max_wait_1h": 3,
            "max_wait_hours": .25, "confirmation_mode": "PRICE_ONLY",
            "confirmation_bars": 1, "emergency_modes": [],
            "emergency_adverse_atr": 0.0,
            "emergency_adverse_stdev": 0.0,
            "emergency_continued_bars": 0,
            "arm_break_mode": "PREV_BAR", "arm_break_threshold": 0.0,
        },
    )
    assert audit["blockers"] == []
