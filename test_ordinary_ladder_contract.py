import numpy as np

from backtest_v8_harness import IndicatorStore
from ordinary_ladder_contract import (
    Completed4hE02,
    CompletedParentEvent,
    ReclaimObligation,
    e02_exit_signal,
    next_strictly_later_index,
    reclaim_reference_from_reason,
    strongest_absolute_target,
)
from tools.vec_band_ladder_walkforward import ladder_mult


PAIRS = {"D": (10.0, 6.0), "4h": (6.0, 4.0), "1h": (4.0, 1.0)}


def _parent(tf, source, pb, cross="BULL", structure=False, availability=200):
    return CompletedParentEvent(
        timeframe=tf,
        source_close_ts=source,
        availability_ts=availability,
        pct_b=pb,
        wt_cross=cross,
        structure=structure,
    )


def test_simultaneous_parents_resolve_to_strongest_absolute_capped_target():
    seen = set()
    order = strongest_absolute_target(
        [
            _parent("D", 100, 0.0),
            _parent("4h", 101, 0.0),
            _parent("1h", 102, 0.0),
        ],
        side="LONG",
        trigger="union",
        current_notional_usd=6_000.0,
        pairs=PAIRS,
        seen=seen,
    )
    assert order.multiplier == 8.0
    assert order.target_notional_usd == 16_000.0
    assert order.add_notional_usd == 10_000.0
    assert seen == {("D", 100), ("4h", 101), ("1h", 102)}

    # Forward-broadcast copies of the same completed parents are inert.
    assert (
        strongest_absolute_target(
            [
                _parent("D", 100, 0.0),
                _parent("4h", 101, 0.0),
                _parent("1h", 102, 0.0),
            ],
            side="LONG",
            trigger="union",
            current_notional_usd=6_000.0,
            pairs=PAIRS,
            seen=seen,
        )
        is None
    )


def test_future_parent_is_not_consumed_and_can_fire_when_available():
    seen = set()
    future = _parent("D", 300, 0.0, availability=299)
    assert (
        strongest_absolute_target(
            [future],
            side="LONG",
            trigger="green",
            current_notional_usd=0,
            pairs=PAIRS,
            seen=seen,
        )
        is None
    )
    assert not seen
    available = _parent("D", 300, 0.0, availability=300)
    assert (
        strongest_absolute_target(
            [available],
            side="LONG",
            trigger="green",
            current_notional_usd=0,
            pairs=PAIRS,
            seen=seen,
        ).target_notional_usd
        == 16_000.0
    )


def test_short_ladder_is_band_mirror_not_long_negation():
    # A short near the upper band reflects to the deepest side-normalized rung.
    order = strongest_absolute_target(
        [_parent("D", 100, 1.0, cross="BEAR")],
        side="SHORT",
        trigger="green",
        current_notional_usd=0,
        pairs=PAIRS,
    )
    assert order.multiplier == 8.0
    assert order.target_notional_usd == 16_000.0


def test_e02_completed_parent_and_reclaim_reference_are_side_mirrors():
    long_event = Completed4hE02(100, 100, 89, 90, 120)
    long_signal = e02_exit_signal(long_event, side="LONG")
    assert long_signal.reclaim_reference == 120

    short_event = Completed4hE02(100, 100, 121, 90, 120)
    short_signal = e02_exit_signal(short_event, side="SHORT")
    assert short_signal.reclaim_reference == 90

    assert (
        e02_exit_signal(
            Completed4hE02(101, 100, 89, 90, 120),
            side="LONG",
        )
        is None
    )


def test_reclaim_obligation_persists_until_confirmed_fill():
    long = ReclaimObligation("LONG")
    long.latch(
        exit_fill=100,
        prior_opposite_level=110,
        exited_notional_usd=20_000,
        exit_ts=10,
    )
    assert long.pending
    assert long.reclaim_level == 110
    assert long.target_notional_usd == 16_000
    long.observe_price(95)
    assert long.favorable_gap_seen
    # An opposition veto is represented by doing nothing: obligation survives.
    assert long.pending
    assert not long.crossed(high=109.99, low=90)
    assert long.crossed(high=110, low=90)
    long.confirm_fill()
    assert not long.pending

    short = ReclaimObligation("SHORT")
    short.latch(
        exit_fill=100,
        prior_opposite_level=90,
        exited_notional_usd=8_000,
        exit_ts=10,
    )
    assert short.reclaim_level == 90
    short.observe_price(105)
    assert short.favorable_gap_seen
    assert short.crossed(high=110, low=90)


def test_next_fill_skips_same_availability_batch():
    assert next_strictly_later_index([100, 100, 100, 105], 0) == 3
    assert next_strictly_later_index([100, 100, 100], 1) is None


def test_e02_reason_preserves_signal_time_reclaim_reference():
    assert (
        reclaim_reference_from_reason(
            "E02_DONCHIAN_4h_N30_src123_"
            "reclaim_ref145.6250_MANDATORY_REENTRY",
            99.0,
        )
        == 145.625
    )
    assert reclaim_reference_from_reason("other", 99.0) == 99.0


def test_vector_ladder_uses_the_shared_bounded_multiplier():
    assert ladder_mult(0.0, 10.0, 6.0, "center_plateau") == 8.0
    assert ladder_mult(1.0, 10.0, 6.0, "center_plateau") == 6.0


def test_frozen_npz_gets_prior_n30_e02_fields_without_regeneration(tmp_path):
    parents = np.repeat(np.arange(1, 36, dtype=np.int64) * 14_400, 2)
    high = np.repeat(np.arange(100, 135, dtype=np.float64), 2)
    low = high - 10.0
    path = tmp_path / "MU.npz"
    np.savez(
        path,
        timestamps=np.arange(len(parents), dtype=np.int64) + 1_000_000,
        timestamp_4h=parents,
        high_4h=high,
        low_4h=low,
    )
    store = IndicatorStore(str(path))
    # Parent index 30 sees parents 0..29, strictly excluding itself.
    row = 30 * 2
    assert store.arrays["e02_prior_high_4h_n30"][row] == 129
    assert store.arrays["e02_prior_low_4h_n30"][row] == 90
