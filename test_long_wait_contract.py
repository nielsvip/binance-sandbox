import pytest

from long_wait_contract import CausalScalar, LongWaitInputs, evaluate_long_wait_direct, params_from_recipe


def scalar(value, ts=100):
    return CausalScalar(value, ts)


def rddt_short_inputs(*, channel=100., price=99., k4=60., k1=65., previous=70., d1=70., k5=60., d5=70.):
    return LongWaitInputs(
        scalar(price), scalar(channel, 95), scalar(k4, 90), scalar(k1), scalar(previous, 95), scalar(d1),
        scalar(k5), scalar(d5),
    )


def test_rddt_selected_short_recipe_is_causal_and_aggregate_episode_based():
    kwargs = dict(side="SHORT", bounce_timeframe="15m", bounce_distance=.015, deep_k4h=50., turn_k1h=40., confirmation="stoch5", asof_ts=100)
    first = evaluate_long_wait_direct(rddt_short_inputs(), **kwargs)
    assert first.eligible and first.episode_start
    assert first.proximity == pytest.approx(.01)
    same = evaluate_long_wait_direct(rddt_short_inputs(), **kwargs, prior_state=first.next_state)
    assert same.eligible and not same.episode_start


def test_inclusive_channel_boundary_and_short_mirror_match_vector_mask():
    long = LongWaitInputs(
        scalar(101.5), scalar(100., 95), scalar(40., 90), scalar(35.), scalar(30., 95), scalar(40.),
        scalar(60.), scalar(50.),
    )
    decision = evaluate_long_wait_direct(
        long, side="LONG", bounce_timeframe="15m", bounce_distance=.015,
        deep_k4h=50., turn_k1h=40., confirmation="stoch5", asof_ts=100,
    )
    assert decision.eligible and decision.proximity == pytest.approx(.015)


def test_missing_or_mutated_completed_input_fails_closed_and_selected_params_are_strict():
    kwargs = dict(side="SHORT", bounce_timeframe="15m", bounce_distance=.015, deep_k4h=50., turn_k1h=40., confirmation="stoch5", asof_ts=100)
    first = evaluate_long_wait_direct(rddt_short_inputs(), **kwargs)
    mutated = evaluate_long_wait_direct(rddt_short_inputs(k4=61.), **kwargs, prior_state=first.next_state)
    assert "MUTATED_COMPLETED_INPUT:stoch_k_4h" in mutated.blockers
    future = evaluate_long_wait_direct(
        rddt_short_inputs().__class__(
            scalar(99.), scalar(100., 101), scalar(60., 90), scalar(65.), scalar(70., 95), scalar(70.), scalar(60.), scalar(70.)
        ), **kwargs,
    )
    assert "FUTURE_COMPLETED_INPUT:completed_channel" in future.blockers
    assert params_from_recipe({"family": "ENTRY_LONG_WAIT_ENABLED", "params": {
        "bounce_timeframe": "15m", "bounce_distance": .015, "deep_k4h": 50., "turn_k1h": 40., "confirmation": "stoch5",
    }})["turn_k1h"] == 40.
    with pytest.raises(ValueError):
        params_from_recipe({"family": "ENTRY_LONG_WAIT_ENABLED", "params": {}})
