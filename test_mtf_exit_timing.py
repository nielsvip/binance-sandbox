from mtf_exit_timing import (
    dc_reject_step,
    effective_mtf_min_open_ts,
    event_within_lookback,
    indicator_event_timestamp,
    position_open_is_eligible,
    timeframe_seconds,
)


def test_backtest_uses_simulation_start_instead_of_future_live_cutoff():
    live_restart_cutoff = 1_779_235_200.0
    historical_sim_start = 1_672_531_200.0
    historical_position_open = historical_sim_start + 86_400

    effective_cutoff = effective_mtf_min_open_ts(
        live_restart_cutoff,
        simulation_start_ts=historical_sim_start,
    )

    assert historical_position_open < live_restart_cutoff
    assert position_open_is_eligible(historical_position_open, effective_cutoff)
    assert not position_open_is_eligible(historical_position_open, live_restart_cutoff)


def test_live_restart_cutoff_is_unchanged_without_explicit_simulation_start():
    live_restart_cutoff = 1_779_235_200.0
    assert effective_mtf_min_open_ts(live_restart_cutoff) == live_restart_cutoff


def test_accelerated_simulation_uses_injected_tick_time_not_wall_clock():
    simulated_tick = 1_672_531_200.0
    wall_clock_years_later = 1_900_000_000.0

    assert (
        indicator_event_timestamp(
            {"_tick_ts": simulated_tick, "ts": simulated_tick - 300},
            fallback_now=wall_clock_years_later,
        )
        == simulated_tick
    )


def test_rejection_window_uses_selected_timeframe_duration():
    tag_ts = 1_672_531_200.0

    assert timeframe_seconds("1h") == 3_600
    assert event_within_lookback(tag_ts + 5 * 3_600, tag_ts, 5, "1h")
    assert not event_within_lookback(tag_ts + 5 * 3_600 + 1, tag_ts, 5, "1h")
    # This would have expired under the old fixed 5m × five-bar calculation.
    assert event_within_lookback(tag_ts + 30 * 60, tag_ts, 5, "1h")


def test_dc_rejection_lookback_expires_at_selected_tf_boundary():
    outside_ts = 1_672_531_200.0
    assert event_within_lookback(outside_ts + 3 * 900, outside_ts, 3, "15m")
    assert not event_within_lookback(
        outside_ts + 3 * 900 + 1, outside_ts, 3, "15m"
    )


def test_missing_event_timestamp_never_arms_rejection():
    assert not event_within_lookback(1_672_531_200.0, 0, 5, "1h")


def test_dc_reject_step_is_side_mirrored_and_recrosses_on_later_tick():
    now = 1_672_531_200.0
    short_state, short_fire = dc_reject_step(
        0,
        now_ts=now,
        price=89,
        band=90,
        lookback_bars=5,
        timeframe="1h",
        is_long=False,
    )
    assert short_state == now
    assert not short_fire
    short_state, short_fire = dc_reject_step(
        short_state,
        now_ts=now + 300,
        price=91,
        band=90,
        lookback_bars=5,
        timeframe="1h",
        is_long=False,
    )
    assert short_state == 0
    assert short_fire

    long_state, long_fire = dc_reject_step(
        0,
        now_ts=now,
        price=111,
        band=110,
        lookback_bars=5,
        timeframe="1h",
        is_long=True,
    )
    assert long_state == now
    assert not long_fire
    long_state, long_fire = dc_reject_step(
        long_state,
        now_ts=now + 300,
        price=109,
        band=110,
        lookback_bars=5,
        timeframe="1h",
        is_long=True,
    )
    assert long_state == 0
    assert long_fire


def test_dc_reject_step_clears_an_expired_arm():
    outside = 1_672_531_200.0
    state, fire = dc_reject_step(
        outside,
        now_ts=outside + 5 * 3600 + 1,
        price=91,
        band=90,
        lookback_bars=5,
        timeframe="1h",
        is_long=False,
    )
    assert state == 0
    assert not fire
