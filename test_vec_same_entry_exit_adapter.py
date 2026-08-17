import dataclasses
import copy
import math
from types import SimpleNamespace

import numpy as np

from tools.vec_same_entry_exit_adapter import (
    AlgoStructureParams,
    AlgoStoch4hParams,
    AlgoProfitTake15mParams,
    ChandelierParams,
    GrOppositeParams,
    MtfAtrTrailExitBook,
    MtfAtrTrailParams,
    ProtectiveTrailParams,
    StaticExitBook,
    WtMtfParams,
    _causal_action_evidence,
    _dc4h_entry_allowed,
    _dc4h_held_breached,
    _entry_schedule_hash,
    _golden_rule_completed_votes,
    algo_structure_grid,
    algo_stoch_4h_grid,
    algo_profit_take_15m_grid,
    build_donchian_book,
    build_algo_stoch_4h_book,
    build_algo_profit_take_15m_book,
    build_chandelier_book,
    build_gr_opposite_book,
    build_wt_mtf_book,
    e02_grid,
    chandelier_grid,
    bottom_delayed_grid,
    bottom_delayed_grid_extended,
    bottom_emergency_grid,
    bottom_emergency_variants,
    bottom_structural_v2_emergency_variants,
    bottom_structural_v2_grid,
    gr_opposite_grid,
    mtf_atr_trail_grid,
    protective_trail_grid,
    protective_trail_grid_extended,
    prune_bottom_b_extended_bases,
    simulate,
    simulate_structural_compiled,
    structural_grid,
    wt_grid,
)
from vec_paths.structural_wt_retest_exit import StructuralWtParams


class FakeZ:
    def __init__(self, rows):
        self.rows = rows
        self.files = list(rows)

    def __getitem__(self, key):
        return self.rows[key]


def _fake_data(n=12):
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    return SimpleNamespace(
        symbol="TEST",
        ts=ts,
        high=np.linspace(10, 12, n),
        low=np.linspace(9, 11, n),
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ(
            {
                "wt1_1h": np.array([0, 10, 30, 70, 60, 40, 20, 10, 0, 0, 0, 0], float),
                "wt2_1h": np.array([0, 5, 20, 60, 62, 50, 30, 20, 10, 0, 0, 0], float),
            }
        ),
    )


def test_wt_mtf_is_edge_triggered_and_causal():
    data = _fake_data()
    htf = SimpleNamespace(
        event_index=np.arange(12, dtype=np.int64),
        source_ts=data.ts.copy(),
        high=data.high.copy(),
        low=data.low.copy(),
        close=(data.high + data.low) / 2,
    )
    book = build_wt_mtf_book(
        data,
        {"1h": htf},
        WtMtfParams(
            timeframes=("1h",),
            min_against_tfs=1,
            extreme=65,
            velocity=0,
            recent_extreme_bars=4,
        ),
        side="LONG",
    )
    rows = np.flatnonzero(book.events)
    assert rows.tolist() == [4]
    decision = book.update(4, active=True)
    assert decision is not None
    assert decision.source_timestamps["1h"] <= data.ts[4]
    assert book.update(5, active=True) is None


def test_wt_grid_is_exact_five_tf_bounded_contract():
    rows = wt_grid()
    assert len(rows) == 4 * 4 * 4 * 4
    assert {row.timeframes for row in rows} == {
        ("15m", "1h", "4h", "D", "W")
    }
    assert {row.min_against_tfs for row in rows} == {1, 2, 3, 4}
    assert {row.extreme for row in rows} == {45.0, 55.0, 65.0, 75.0}
    assert {row.velocity for row in rows} == {0.0, 0.25, 0.5, 1.0}
    assert {row.profit_gate_pct for row in rows} == {0.0, 0.25, 0.5, 1.0}
    assert all(not row.require_fast_structure for row in rows)


def test_weekly_wt_event_uses_completed_source_only():
    data = _fake_data()
    data.z = FakeZ(
        {
            "wt1_W": data.z["wt1_1h"],
            "wt2_W": data.z["wt2_1h"],
        }
    )
    htf = SimpleNamespace(
        event_index=np.arange(12, dtype=np.int64),
        source_ts=data.ts - 1,
        high=data.high.copy(),
        low=data.low.copy(),
        close=(data.high + data.low) / 2,
    )
    book = build_wt_mtf_book(
        data,
        {"W": htf},
        WtMtfParams(
            timeframes=("W",),
            min_against_tfs=1,
            extreme=65,
            velocity=0,
            recent_extreme_bars=4,
        ),
        side="LONG",
    )
    decision = book.update(4, active=True)
    assert decision is not None
    assert decision.source_timestamps["W"] < data.ts[4]


def _fake_gr_data(n=8):
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    rows = {}
    for tf in ("15m", "1h", "4h", "D"):
        rows[f"close_{tf}"] = np.full(n, 10.0)
        rows[f"wt1_{tf}"] = np.array([2, 2, 2, -2, -2, -2, -2, -2], float)
        rows[f"wt2_{tf}"] = np.zeros(n)
        rows[f"rsi_{tf}"] = np.array([60, 60, 60, 40, 40, 40, 40, 40], float)
    return SimpleNamespace(
        symbol="GRTEST",
        ts=ts,
        high=np.full(n, 10.5),
        low=np.full(n, 9.5),
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ(rows),
    )


def test_gr_grid_is_exact_explicit_weight_contract():
    rows = gr_opposite_grid()
    assert len(rows) == 3 * 2 * 6 * 3 * 4
    assert {row.timeframes for row in rows} == {
        ("15m", "1h", "4h", "D")
    }
    assert {row.min_tfs for row in rows} == {1, 2, 3}
    assert {row.min_indicators for row in rows} == {1, 2}
    assert {row.min_weighted_score for row in rows} == {
        4.0, 6.0, 8.0, 10.0, 12.0, 15.5
    }
    assert {row.weights for row in rows} == {
        (1.0, 1.0, 1.0, 1.0),
        (0.5, 1.0, 2.0, 3.0),
        (0.0, 1.0, 2.0, 4.0),
    }
    assert {row.profit_gate_pct for row in rows} == {0.0, 0.25, 0.5, 1.0}


def test_gr_opposite_is_completed_causal_side_mirror_with_per_tf_audit():
    data = _fake_gr_data()
    htfs = {
        tf: SimpleNamespace(
            event_index=np.arange(8, dtype=np.int64),
            source_ts=data.ts - 1,
        )
        for tf in ("15m", "1h", "4h", "D")
    }
    params = GrOppositeParams(
        timeframes=("15m", "1h", "4h", "D"),
        min_tfs=3,
        min_indicators=2,
        min_weighted_score=8.0,
        weights=(1.0, 1.0, 1.0, 1.0),
    )
    long_book = build_gr_opposite_book(data, htfs, params, side="LONG")
    short_book = build_gr_opposite_book(data, htfs, params, side="SHORT")
    assert np.flatnonzero(long_book.events).tolist() == [3, 4, 5, 6, 7]
    assert np.flatnonzero(short_book.events).tolist() == [0, 1, 2]
    decision = long_book.update(3, active=True)
    assert decision is not None
    assert all(source < data.ts[3] for source in decision.source_timestamps.values())
    assert long_book.audit["signal_direction"] == "SHORT"
    assert long_book.audit["per_timeframe"]["15m"]["weight"] == 1.0
    assert long_book.audit["per_timeframe"]["15m"][
        "exit_event_vote_histogram"
    ] == {"2": 5}


def test_gr_weakest_arm_has_eligible_completed_events():
    data = _fake_gr_data()
    htfs = {
        tf: SimpleNamespace(
            event_index=np.arange(8, dtype=np.int64),
            source_ts=data.ts - 1,
        )
        for tf in ("15m", "1h", "4h", "D")
    }
    book = build_gr_opposite_book(
        data,
        htfs,
        GrOppositeParams(
            timeframes=("15m", "1h", "4h", "D"),
            min_tfs=1,
            min_indicators=1,
            min_weighted_score=4.0,
            weights=(1.0, 1.0, 1.0, 1.0),
        ),
        side="LONG",
    )
    assert np.count_nonzero(book.events) == 5
    assert book.audit["eligible_completed_update_count"] == 5
    assert book.audit["source_data_contract_valid"]
    availability = book.audit["per_timeframe"]["1h"][
        "indicator_input_availability"
    ]
    assert availability["WT"] and availability["RSI"]
    assert not availability["MFI"]


def test_gr_min_tf_and_min_indicator_knobs_are_wired():
    data = _fake_gr_data()
    # Only 15m turns against the held LONG side.
    for tf in ("1h", "4h", "D"):
        data.z.rows[f"wt1_{tf}"][:] = 2.0
        data.z.rows[f"rsi_{tf}"][:] = 60.0
    htfs = {
        tf: SimpleNamespace(
            event_index=np.arange(8, dtype=np.int64),
            source_ts=data.ts - 1,
        )
        for tf in ("15m", "1h", "4h", "D")
    }
    common = dict(
        timeframes=("15m", "1h", "4h", "D"),
        min_weighted_score=1.0,
        weights=(1.0, 1.0, 1.0, 1.0),
    )
    one_tf = build_gr_opposite_book(
        data,
        htfs,
        GrOppositeParams(min_tfs=1, min_indicators=2, **common),
        side="LONG",
    )
    two_tf = build_gr_opposite_book(
        data,
        htfs,
        GrOppositeParams(min_tfs=2, min_indicators=2, **common),
        side="LONG",
    )
    assert np.count_nonzero(one_tf.events) == 5
    assert np.count_nonzero(two_tf.events) == 0

    # Leave only RSI against: one raw vote qualifies min_ind=1, not min_ind=2.
    data.z.rows["wt1_15m"][:] = 2.0
    one_ind = build_gr_opposite_book(
        data,
        htfs,
        GrOppositeParams(min_tfs=1, min_indicators=1, **common),
        side="LONG",
    )
    two_ind = build_gr_opposite_book(
        data,
        htfs,
        GrOppositeParams(min_tfs=1, min_indicators=2, **common),
        side="LONG",
    )
    assert np.count_nonzero(one_ind.events) == 5
    assert np.count_nonzero(two_ind.events) == 0


def test_vector_gr_votes_match_canonical_raw_scorer():
    from golden_rule_htf import _ind_score

    data = _fake_gr_data()
    htf = SimpleNamespace(event_index=np.arange(8, dtype=np.int64))
    raw = _golden_rule_completed_votes(
        data, htf, "1h", vote_long=False
    )
    indicators = {
        key: values[3]
        for key, values in data.z.rows.items()
        if key.endswith("_1h")
    }
    canonical, _ = _ind_score(
        indicators, "1h", False, float(indicators["close_1h"])
    )
    assert raw[3] == canonical == 2


def test_static_book_never_crosses_side_or_fires_flat():
    events = np.array([0, 1, 0], dtype=np.uint8)
    refs = np.array([math.nan, 11.0, math.nan])
    book = StaticExitBook("TEST_EXIT", events, refs, {1: {"1h": 600}})
    assert book.update(1, active=False) is None
    assert book.update(1, active=True).reason == "TEST_EXIT"


def test_e02_grid_is_exact_bounded_contract():
    rows = e02_grid()
    assert len(rows) == 3 * 7 * 4
    assert {row["timeframe"] for row in rows} == {"1h", "4h", "D"}
    assert {row["lookback"] for row in rows} == {10, 15, 20, 30, 40, 55, 80}
    assert {row["profit_gate_pct"] for row in rows} == {0.0, 0.25, 0.5, 1.0}
    assert all("5m" not in str(row) for row in rows)


def test_algo_structure_grid_keeps_historical_events_separate():
    rows = algo_structure_grid()
    assert len(rows) == 4
    assert {
        (
            row.timeframe,
            row.lookback,
            row.historical_score_delta,
            row.profit_gate_pct,
        )
        for row in rows
    } == {
        ("1h", 20, -15, 0.0),
        ("1h", 20, -15, 3.0),
        ("15m", 20, -10, 0.0),
        ("15m", 20, -10, 3.0),
    }
    with np.testing.assert_raises_regex(ValueError, "provenance mismatch"):
        AlgoStructureParams("1h", 20, -10, 0.0).validate()


def test_algo_stoch_4h_grid_is_mirrored_and_completed_causal():
    rows = algo_stoch_4h_grid()
    assert len(rows) == 12
    assert {
        (row.long_k_min, row.short_k_max) for row in rows
    } == {(60.0, 40.0), (70.0, 30.0), (80.0, 20.0)}
    assert {row.event_mode for row in rows} == {"STATE", "CROSS"}
    n = 6
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    htf = SimpleNamespace(
        event_index=np.arange(n, dtype=np.int64),
        source_ts=ts - 1,
        high=np.full(n, 11.0),
        low=np.full(n, 9.0),
    )
    long_data = SimpleNamespace(
        symbol="STOCHL",
        ts=ts,
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ(
            {
                "k_4h": np.array([50, 65, 70, 65, 50, 40], float),
                "d_4h": np.array([55, 60, 60, 70, 60, 50], float),
            }
        ),
    )
    short_data = SimpleNamespace(
        symbol="STOCHS",
        ts=ts,
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ(
            {
                "k_4h": np.array([50, 35, 30, 35, 50, 60], float),
                "d_4h": np.array([45, 40, 40, 30, 40, 50], float),
            }
        ),
    )
    params = AlgoStoch4hParams(60.0, 40.0, "CROSS", -5, 0.0)
    long_book = build_algo_stoch_4h_book(
        long_data, {"4h": htf}, params, side="LONG"
    )
    short_book = build_algo_stoch_4h_book(
        short_data, {"4h": htf}, params, side="SHORT"
    )
    assert np.flatnonzero(long_book.events).tolist() == [3]
    assert np.flatnonzero(short_book.events).tolist() == [3]
    assert long_book.update(3, active=True).source_timestamps["4h"] < ts[3]
    assert short_book.update(3, active=True).source_timestamps["4h"] < ts[3]


def test_algo_profit_take_15m_grid_is_separate_mirrored_and_causal():
    rows = algo_profit_take_15m_grid()
    assert len(rows) == 8
    assert {row.event_mode for row in rows} == {"STATE", "CROSS"}
    assert {row.min_profit_pct for row in rows} == {3.0, 5.0, 7.0, 10.0}
    n = 5
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    htf = SimpleNamespace(
        event_index=np.arange(n, dtype=np.int64),
        source_ts=ts - 1,
        high=np.full(n, 11.0),
        low=np.full(n, 9.0),
    )
    data = SimpleNamespace(
        symbol="PT15",
        ts=ts,
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ(
            {
                "k_15m": np.array([50, 60, 55, 40, 30], float),
                "d_15m": np.array([55, 55, 60, 50, 40], float),
            }
        ),
    )
    book = build_algo_profit_take_15m_book(
        data,
        {"15m": htf},
        AlgoProfitTake15mParams("CROSS", 5.0),
        side="LONG",
    )
    assert np.flatnonzero(book.events).tolist() == [2]
    assert book.update(2, active=True).source_timestamps["15m"] < ts[2]


def test_chandelier_grid_is_exact_standard_registered_contract():
    rows = chandelier_grid()
    assert len(rows) == 2 * 4 * 5 * 4
    assert {row.timeframe for row in rows} == {"4h", "D"}
    assert {row.lookback for row in rows} == {10, 20, 30, 55}
    assert {row.atr_mult for row in rows} == {1.5, 2.0, 2.5, 3.0, 4.0}
    assert {row.profit_gate_pct for row in rows} == {0.0, 0.25, 0.5, 1.0}


def test_chandelier_is_monotonic_completed_causal_and_side_mirrored():
    n = 14
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    high = np.array(list(range(10, 20)) + [19, 19, 19, 19], dtype=float)
    low = np.array(list(range(20, 10, -1)) + [11, 11, 11, 11], dtype=float)
    close = np.full(n, 18.0)
    close[10:] = 16.0
    data = SimpleNamespace(ts=ts, high=high, low=low)
    htf = SimpleNamespace(
        event_index=np.arange(n, dtype=np.int64),
        source_ts=ts - 1,
        high=high,
        low=low,
        close=close,
        atr=np.ones(n),
    )
    params = ChandelierParams("4h", 10, 2.0)
    long_book = build_chandelier_book(
        data, {"4h": htf}, params, side="LONG"
    )
    short_book = build_chandelier_book(
        data, {"4h": htf}, params, side="SHORT"
    )
    long_decisions = [
        (row, decision)
        for row in range(n)
        if (decision := long_book.update(row, active=True)) is not None
    ]
    short_decisions = [
        (row, decision)
        for row in range(n)
        if (decision := short_book.update(row, active=True)) is not None
    ]
    assert long_decisions[0][0] == 10
    assert short_decisions[0][0] == 9
    assert long_decisions[0][1].source_timestamps["4h"] < ts[10]
    assert short_decisions[0][1].source_timestamps["4h"] < ts[9]
    assert long_book.current_trail == 17.0
    assert short_book.current_trail == 13.0
    long_book.update(11, active=False)
    assert math.isnan(long_book.current_trail)
    assert long_book.audit["monotonic_while_active"]
    assert long_book.audit["formula"] == "highest_high-ATR*mult"
    assert short_book.audit["formula"] == "lowest_low+ATR*mult"


def test_completed_donchian_book_is_causal_and_side_mirrored():
    n = 120
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    low = np.full(n, 10.0)
    high = np.full(n, 20.0)
    close = np.full(n, 15.0)
    close[10] = 9.0
    close[11] = 21.0
    data = SimpleNamespace(
        ts=ts,
        high=high,
        low=low,
        close=close,
    )
    htf = SimpleNamespace(
        event_index=np.arange(n, dtype=np.int64),
        source_ts=ts - 1,
        high=high,
        low=low,
        close=close,
    )
    long_book = build_donchian_book(
        data, {"1h": htf}, timeframe="1h", lookback=10, side="LONG"
    )
    short_book = build_donchian_book(
        data, {"1h": htf}, timeframe="1h", lookback=10, side="SHORT"
    )
    assert np.flatnonzero(long_book.events).tolist() == [10]
    assert np.flatnonzero(short_book.events).tolist() == [11]
    assert long_book.update(10, active=True).reclaim_reference == 20.0
    assert short_book.update(11, active=True).reclaim_reference == 10.0
    assert long_book.update(10, active=True).source_timestamps["1h"] < ts[10]
    assert short_book.update(11, active=True).source_timestamps["1h"] < ts[11]

    long_15m = build_donchian_book(
        data, {"15m": htf}, timeframe="15m", lookback=20, side="LONG"
    )
    assert all(
        decision.source_timestamps["15m"] <= data.ts[row]
        for row in range(n)
        if (decision := long_15m.update(row, active=True)) is not None
    )


def test_entry_schedule_hash_changes_only_when_entry_schedule_changes():
    data = SimpleNamespace(ts=np.array([1, 2, 3], dtype=np.int64))
    curve = SimpleNamespace()
    # dataclasses.asdict needs an actual ladder curve.
    from tools.vec_band_ladder_walkforward import Curve, SignalData

    curve = Curve("x", "linear", "green", "target", 30, 8, 4, 6, 3, 3, 1)
    first = SignalData(
        entry_mult=np.array([0.0, 2.0, 0.0]),
        event_tf=np.zeros((3, 3), dtype=np.uint8),
        exit_event=np.zeros(3, dtype=np.uint8),
        exit_ref=np.full(3, np.nan),
        causality={},
    )
    second = SignalData(
        entry_mult=np.array([0.0, 3.0, 0.0]),
        event_tf=first.event_tf,
        exit_event=first.exit_event,
        exit_ref=first.exit_ref,
        causality={},
    )
    assert _entry_schedule_hash(data, first, curve, 0, 3) == _entry_schedule_hash(
        data, first, curve, 0, 3
    )
    assert _entry_schedule_hash(data, first, curve, 0, 3) != _entry_schedule_hash(
        data, second, curve, 0, 3
    )


def test_structural_registry_grid_is_complete_and_hour_normalized():
    rows = structural_grid()
    assert len(rows) == 768
    observed = {
        (
            params.arm_tf,
            params.confirm_tf,
            params.rebound_atr,
            params.prebreak_lookback,
            wait_hours,
            profit_gate,
            params.max_wait_1h,
        )
        for params, profit_gate, wait_hours in rows
    }
    assert ("1h", "15m", 0.5, 3, 12, 0.25, 48) in observed
    assert ("4h", "1h", 4.0, 10, 48, 1.0, 48) in observed


def test_bottom_exit_grids_cover_smaller_tfs_modes_and_rare_brakes():
    path_a = protective_trail_grid()
    path_b = bottom_delayed_grid()
    path_c = bottom_emergency_grid()
    assert len(path_a) == 220
    assert {row.mode for row in path_a} == {"IMMEDIATE", "ATR", "STDEV", "DC"}
    assert {row.trail_timeframe for row in path_a} == {"5m", "15m", "1h"}
    assert len(path_b) == 864
    assert {params.confirm_tf for params, _ in path_b} == {"5m", "15m", "1h"}
    assert {params.confirmation_mode for params, _ in path_b} == {
        "PRICE_ONLY", "WT_ONLY", "AND", "OR"
    }
    assert {params.confirmation_bars for params, _ in path_b} == {1, 2}
    assert len(path_c) == 324
    labels = {label for _, _, label in path_c}
    assert {"ADVERSE_ATR_4", "ADVERSE_STDEV_5", "MAX_WAIT", "CONTINUED_5"} <= labels
    assert all(params.emergency_modes for params, _, _ in path_c)


def test_extended_bottom_grids_are_bounded_and_add_missing_dimensions():
    path_a = protective_trail_grid_extended()
    path_b = bottom_delayed_grid_extended()
    brakes = bottom_emergency_variants()
    assert len(path_a) == 444
    assert {row.arm_timeframe for row in path_a} == {
        "15m", "1h", "4h", "D"
    }
    assert {row.trail_timeframe for row in path_a} == {
        "5m", "15m", "1h", "4h"
    }
    assert {row.break_buffer_atr for row in path_a} == {0.0, 0.25, 0.5}
    assert len(path_b) == 624
    assert {params.arm_tf for params, _ in path_b} == {
        "15m", "1h", "4h", "D"
    }
    assert 3 in {params.confirmation_bars for params, _ in path_b}
    assert {6, 72} <= {wait for _, wait in path_b}
    assert len(brakes) == 12
    assert {"ADVERSE_ATR_6", "ADVERSE_STDEV_7", "CONTINUED_8"} <= {
        label for label, _ in brakes
    }


def test_bottom_structural_v2_grid_is_bounded_and_qualified():
    rows = bottom_structural_v2_grid()
    brakes = bottom_structural_v2_emergency_variants()
    assert len(rows) == 960
    assert {params.arm_tf for params, _ in rows} == {"15m", "1h"}
    assert {params.arm_break_mode for params, _ in rows} == {
        "ATR",
        "STDEV",
        "DC_SUPPORT",
    }
    assert {params.confirm_tf for params, _ in rows} == {"5m", "15m", "1h"}
    assert {params.confirmation_mode for params, _ in rows} == {
        "PRICE_ONLY",
        "WT_ONLY",
        "AND",
        "OR",
    }
    assert {label for label, _ in brakes} == {
        "ADVERSE_ATR_6",
        "ADVERSE_STDEV_7",
        "CONTINUED_8",
    }


def test_c_ext_prunes_exactly_eight_b_bases_without_final_fold_leakage():
    candidates = []
    for index in range(10):
        candidates.append(
            {
                "params": {"candidate": index},
                "nested": {
                    "discovery": {
                        "exit_fills": 4 + index,
                        "exposure_weighted_tim_pct_row_weighted": (
                            70.0 + index
                        ),
                        "max_drawdown_account_pct_max": 20.0 - index,
                    },
                    "discovery_alpha_vs_same_entry_e02_pp": (
                        100.0 - index
                    ),
                    "discovery_alpha_vs_bh_pp": 200.0 - index,
                    "robust_discovery_all_folds": index % 3 != 0,
                    "validation": {
                        "capital_return_pct_sum": index,
                        "exposure_weighted_tim_pct_row_weighted": index,
                    },
                    "validation_alpha_vs_same_entry_e02_pp": index,
                    "validation_alpha_vs_bh_pp": index,
                    "robust_validation_fold": index % 2 == 0,
                },
            }
        )
    selected = prune_bottom_b_extended_bases(
        candidates, exposure_min_pct=70.0, exposure_max_pct=80.0
    )
    mutated = copy.deepcopy(candidates)
    for index, row in enumerate(mutated):
        row["nested"]["validation"] = {
            "capital_return_pct_sum": 1e9 - index,
            "exposure_weighted_tim_pct_row_weighted": 100.0 - index,
        }
        row["nested"]["validation_alpha_vs_same_entry_e02_pp"] = -1e9 + index
        row["nested"]["validation_alpha_vs_bh_pp"] = -1e9 + index
        row["nested"]["robust_validation_fold"] = not row["nested"][
            "robust_validation_fold"
        ]
    selected_after_mutation = prune_bottom_b_extended_bases(
        mutated, exposure_min_pct=70.0, exposure_max_pct=80.0
    )
    assert len(selected) == 8
    assert [row["params"] for row in selected] == [
        row["params"] for row in selected_after_mutation
    ]
    assert len(selected) * len(bottom_emergency_variants()) == 96


def _mtf_atr_fixture(*, side="LONG"):
    n = 8
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    if side == "LONG":
        tf_close = {
            "1h": np.array([10.0, 12.0, 9.0]),
            "4h": np.array([10.0, 13.0, 10.0]),
            "D": np.array([10.0, 11.0]),
        }
    else:
        tf_close = {
            "1h": np.array([10.0, 8.0, 11.0]),
            "4h": np.array([10.0, 7.0, 10.0]),
            "D": np.array([10.0, 9.0]),
        }
    tf_rows = {
        "1h": np.array([0, 1, 2], dtype=np.int64),
        "4h": np.array([0, 3, 4], dtype=np.int64),
        "D": np.array([0, 5], dtype=np.int64),
    }
    data = SimpleNamespace(
        ts=ts,
        high=np.full(n, 15.0),
        low=np.full(n, 5.0),
    )
    htfs = {}
    for timeframe, closes in tf_close.items():
        rows = tf_rows[timeframe]
        htfs[timeframe] = SimpleNamespace(
            event_index=rows,
            source_ts=ts[rows] - 1,
            close=closes,
            high=closes + 0.5,
            low=closes - 0.5,
            atr=np.ones(len(rows)),
        )
    return data, htfs


def test_mtf_atr_grid_is_exact_registered_contract():
    rows = mtf_atr_trail_grid()
    assert len(rows) == 40
    assert {row.timeframes for row in rows} == {("1h", "4h", "D")}
    assert {row.atr_mult for row in rows} == {1.5, 2.0, 2.5, 3.0, 4.0}
    assert {row.min_profit_pct for row in rows} == {0.0, 0.25, 0.5, 1.0}
    assert {row.min_confirming_tfs for row in rows} == {1, 2}


def test_mtf_atr_completed_agreement_profit_gate_and_side_mirror():
    for side, entry in (("LONG", 5.0), ("SHORT", 15.0)):
        data, htfs = _mtf_atr_fixture(side=side)
        one = MtfAtrTrailExitBook(
            data,
            htfs,
            MtfAtrTrailParams(("1h", "4h", "D"), 2.0, 0.0, 1),
            side=side,
        )
        two = MtfAtrTrailExitBook(
            data,
            htfs,
            MtfAtrTrailParams(("1h", "4h", "D"), 2.0, 0.0, 2),
            side=side,
        )
        one_decisions = []
        two_decisions = []
        for row in range(6):
            gain = 10.0
            first = one.update_with_position(
                row,
                active=True,
                entry_price=entry,
                current_gain_pct=gain,
            )
            second = two.update_with_position(
                row,
                active=True,
                entry_price=entry,
                current_gain_pct=gain,
            )
            if first is not None:
                one_decisions.append((row, first))
            if second is not None:
                two_decisions.append((row, second))
        assert one_decisions[0][0] == 2
        assert two_decisions[0][0] == 4
        assert set(two_decisions[0][1].source_timestamps) == {"1h", "4h"}
        assert all(
            source <= data.ts[two_decisions[0][0]]
            for source in two_decisions[0][1].source_timestamps.values()
        )

        gated = MtfAtrTrailExitBook(
            data,
            htfs,
            MtfAtrTrailParams(("1h", "4h", "D"), 2.0, 0.5, 1),
            side=side,
        )
        gated.update_with_position(
            0, active=True, entry_price=entry, current_gain_pct=1.0
        )
        gated.update_with_position(
            1, active=True, entry_price=entry, current_gain_pct=1.0
        )
        assert (
            gated.update_with_position(
                2, active=True, entry_price=entry, current_gain_pct=0.49
            )
            is None
        )
        assert (
            gated.update_with_position(
                3, active=True, entry_price=entry, current_gain_pct=0.5
            )
            is not None
        )


def test_mtf_atr_rejects_future_completed_source():
    data, htfs = _mtf_atr_fixture()
    htfs["D"].source_ts[0] = data.ts[0] + 1
    with np.testing.assert_raises_regex(RuntimeError, "future MTF ATR source"):
        MtfAtrTrailExitBook(
            data,
            htfs,
            MtfAtrTrailParams(("1h", "4h", "D"), 2.0, 0.0, 1),
            side="LONG",
        )


def test_compiled_structural_scanner_matches_python_oracle():
    from tools.vec_band_ladder_walkforward import Curve, SignalData
    from tools.vec_same_entry_exit_adapter import StructuralWtExitBookAdapter

    n = 120
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    open_ = np.full(n, 10.0)
    high = np.full(n, 10.2)
    low = np.full(n, 9.8)
    close = np.full(n, 10.0)
    wt4h = np.zeros(n)
    wt1h = np.zeros(n)
    arm_rows = np.array([2, 4, 6, 8], dtype=np.int64)
    confirm_rows = np.array([9, 10, 11], dtype=np.int64)
    arm_high = np.array([12.0, 13.0, 14.0, 11.0])
    arm_low = np.array([10.0, 10.5, 11.0, 9.0])
    arm_close = np.array([11.0, 11.5, 12.0, 9.5])
    arm_wt = np.array([20.0, 25.0, 30.0, 10.0])
    confirm_high = np.array([9.0, 10.0, 9.0])
    confirm_low = np.array([8.0, 8.5, 8.2])
    confirm_close = np.array([8.5, 9.5, 9.0])
    confirm_wt = np.array([5.0, 15.0, 10.0])
    wt4h[arm_rows] = arm_wt
    wt1h[confirm_rows] = confirm_wt
    data = SimpleNamespace(
        symbol="PARITY",
        ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ(
            {
                "wt1_4h": wt4h,
                "wt1_1h": wt1h,
                "dc_low_4h": np.full(n, 1.0),
            }
        ),
        contract={"valid": True},
    )
    htfs = {
        "4h": SimpleNamespace(
            event_index=arm_rows,
            source_ts=ts[arm_rows] - 1,
            high=arm_high,
            low=arm_low,
            close=arm_close,
            atr=np.ones(len(arm_rows)),
        ),
        "1h": SimpleNamespace(
            event_index=confirm_rows,
            source_ts=ts[confirm_rows] - 1,
            high=confirm_high,
            low=confirm_low,
            close=confirm_close,
            atr=np.ones(len(confirm_rows)),
        ),
    }
    curve = Curve("x", "linear", "green", "target", 30, 8, 4, 6, 3, 3, 1)
    entry = np.zeros(n)
    entry[0] = 4.0
    signals = SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=np.zeros(n, dtype=np.uint8),
        exit_ref=np.full(n, np.nan),
        causality={},
    )
    params = StructuralWtParams(
        arm_tf="4h",
        confirm_tf="1h",
        rebound_atr=0.5,
        prebreak_lookback=3,
        max_wait_1h=12,
    )
    python_result = simulate(
        data,
        signals,
        curve,
        StructuralWtExitBookAdapter(data, htfs, params, side="LONG"),
        0,
        n,
        0.0005,
        0.0005,
        side="LONG",
        profit_gate_pct=0.0,
    )
    compiled_result = simulate_structural_compiled(
        data,
        signals,
        curve,
        htfs,
        params,
        0,
        n,
        0.0005,
        0.0005,
        side="LONG",
        profit_gate_pct=0.0,
    )
    for key in (
        "capital_return_pct",
        "bh_capital_return_pct",
        "binary_tim_pct",
        "exposure_weighted_tim_pct",
        "max_drawdown_account_pct",
        "minimum_account_equity_usd",
        "peak_post_fill_notional_usd",
        "requested_notional_usd",
        "filled_notional_usd",
    ):
        assert math.isclose(
            compiled_result[key], python_result[key], abs_tol=1e-9
        ), key
    for key in (
        "signals",
        "rejected_by_profit_gate",
        "exit_fills",
        "entry_fills",
        "lower_or_higher_reentries",
        "reclaim_reentries",
        "clamp_count",
        "bars_flat_beyond_reclaim",
    ):
        assert compiled_result[key] == python_result[key], key

    emergency_params = dataclasses.replace(
        params,
        arm_break_mode="ATR",
        arm_break_threshold=1.0,
        emergency_modes=("ADVERSE_ATR",),
        emergency_adverse_atr=1.0,
    )
    python_emergency = simulate(
        data,
        signals,
        curve,
        StructuralWtExitBookAdapter(
            data, htfs, emergency_params, side="LONG"
        ),
        0,
        n,
        0.0005,
        0.0005,
        side="LONG",
        profit_gate_pct=-999.0,
    )
    compiled_emergency = simulate_structural_compiled(
        data,
        signals,
        curve,
        htfs,
        emergency_params,
        0,
        n,
        0.0005,
        0.0005,
        side="LONG",
        profit_gate_pct=-999.0,
    )
    assert compiled_emergency["emergency_exit_fills"] == 1
    assert python_emergency["emergency_exit_fills"] == 1
    assert compiled_emergency["normal_exit_fills"] == 0
    assert compiled_emergency["exit_fills"] == python_emergency["exit_fills"]
    assert math.isclose(
        compiled_emergency["emergency_exit_pnl_usd"],
        python_emergency["emergency_exit_pnl_usd"],
        abs_tol=1e-9,
    )
    assert math.isclose(
        compiled_emergency["capital_return_pct"],
        python_emergency["capital_return_pct"],
        abs_tol=1e-9,
    )


def test_dc4h_safety_precedes_selected_exit_and_blocks_later_entry():
    from tools.vec_band_ladder_walkforward import Curve, SignalData

    n = 120
    close = np.full(n, 10.0)
    open_ = np.full(n, 10.0)
    close[10:] = 8.5
    open_[11:] = 8.5
    data = SimpleNamespace(
        symbol="DC4PARITY",
        ts=np.arange(1, n + 1, dtype=np.int64) * 300,
        open=open_,
        high=np.maximum(open_, close) + 0.1,
        low=np.minimum(open_, close) - 0.1,
        close=close,
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ({"dc_low_4h": np.full(n, 9.0)}),
        contract={"valid": True},
    )
    curve = Curve("x", "linear", "green", "target", 30, 8, 4, 6, 3, 3, 1)
    entry = np.zeros(n)
    entry[0] = 4.0
    entry[20] = 4.0
    signals = SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=np.zeros(n, dtype=np.uint8),
        exit_ref=np.full(n, np.nan),
        causality={},
    )
    result = simulate(
        data,
        signals,
        curve,
        StaticExitBook(
            "NEVER",
            np.zeros(n, dtype=np.uint8),
            np.full(n, np.nan),
            {},
        ),
        0,
        n,
        0.0,
        0.0,
        side="LONG",
    )
    assert result["dc4h_safety_enforced"] is True
    assert result["dc4h_safety_exit_fills"] == 1
    assert result["emergency_exit_fills"] == 1
    assert result["normal_exit_fills"] == 0
    assert result["reclaim_reentries"] == 0
    assert result["open_reclaim_obligations"] == 0
    assert result["dc4h_entry_blocks"] == 1
    reasons = [row.get("reason", "") for row in result["event_ledger"]]
    assert any(reason.startswith("EMERGENCY_DC4H_BREACH_") for reason in reasons)
    assert "ENTRY_DC4H_SAFETY" in reasons


def test_dc4h_boundary_helpers_fail_closed_for_entry_and_not_for_exit():
    assert _dc4h_entry_allowed(101.0, 100.0, is_long=True)
    assert not _dc4h_entry_allowed(100.0, 100.0, is_long=True)
    assert _dc4h_held_breached(100.0, 100.0, is_long=True)
    assert _dc4h_entry_allowed(99.0, 100.0, is_long=False)
    assert not _dc4h_entry_allowed(100.0, 100.0, is_long=False)
    assert _dc4h_held_breached(100.0, 100.0, is_long=False)
    assert not _dc4h_entry_allowed(100.0, math.nan, is_long=True)
    assert not _dc4h_held_breached(100.0, math.nan, is_long=True)


def test_partial_clip_keeps_runner_and_reclaims_clip():
    from tools.vec_band_ladder_walkforward import Curve, SignalData

    n = 120
    data = SimpleNamespace(
        ts=np.arange(1, n + 1, dtype=np.int64) * 300,
        open=np.full(n, 10.0),
        high=np.full(n, 10.2),
        low=np.full(n, 9.8),
        close=np.full(n, 10.0),
    )
    curve = Curve("x", "linear", "green", "target", 30, 8, 4, 6, 3, 3, 1)
    entry = np.zeros(n)
    entry[0] = 4.0
    signals = SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=np.zeros(n, dtype=np.uint8),
        exit_ref=np.full(n, np.nan),
        causality={},
    )
    events = np.zeros(n, dtype=np.uint8)
    events[10] = 1
    refs = np.full(n, np.nan)
    refs[10] = 10.0
    result = simulate(
        data,
        signals,
        curve,
        StaticExitBook("WT_PARTIAL", events, refs, {10: {"1h": 3000}}),
        0,
        n,
        0.0,
        0.0,
        side="LONG",
        partial_exit_fraction=0.25,
        runner_exit_book=StaticExitBook(
            "E02_DONCHIAN_4H_N30",
            np.zeros(n, dtype=np.uint8),
            np.full(n, np.nan),
            {},
        ),
    )
    assert result["partial_exit_fills"] == 1
    assert result["clip_reclaim_reentries"] == 1
    assert result["runner_exit_fills"] == 0
    assert result["clip_obligations_unfilled_at_end"] == 0
    assert result["insolvent"] is False
    assert result["entry_capacity_breach"] is False
    assert result["peak_post_fill_notional_usd"] <= 16_000.0
    evidence = _causal_action_evidence(result)
    assert evidence["action_evidence_status"] == "CAUSAL_EVENT_LEDGER"
    assert evidence["strictly_later_reentry_proven"] is True
    assert evidence["reentry_violations"] == 0
    assert evidence["exit_indices"] == [11]
    assert evidence["reentry_indices"] == [12]
    assert evidence["reentry_pairs"] == [
        {"exit_index": 11, "reentry_index": 12}
    ]
    assert len(evidence["action_fingerprint"]) == 64
    assert evidence["action_fingerprint"] == evidence["ledger_sha256"]


def test_action_evidence_fails_closed_without_ordered_causal_ledger():
    evidence = _causal_action_evidence(
        {
            "exit_fills": 4,
            "reclaim_reentries": 4,
            "clip_obligations_unfilled_at_end": 0,
        }
    )
    assert evidence["action_evidence_status"] == (
        "UNAVAILABLE_NO_CAUSAL_EVENT_LEDGER"
    )
    assert evidence["action_fingerprint"] is None
    assert evidence["exit_indices"] == []
    assert evidence["reentry_indices"] == []
    assert evidence["strictly_later_reentry_proven"] is False

    mismatched = _causal_action_evidence(
        {
            "exit_fills": 2,
            "event_ledger": [
                {
                    "type": "EXIT",
                    "fill_index": 5,
                    "fill_ts": 1500,
                }
            ],
        }
    )
    assert mismatched["action_evidence_status"] == (
        "UNAVAILABLE_EVENT_LEDGER_EXIT_COUNT_MISMATCH"
    )
    assert mismatched["action_fingerprint"] is None
