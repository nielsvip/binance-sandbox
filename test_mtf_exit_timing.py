from mtf_exit_timing import (
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
