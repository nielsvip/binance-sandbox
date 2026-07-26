from types import SimpleNamespace

import numpy as np

from tools import vec_augment_trend_resume_walkforward as aug
from tools import vec_band_ladder_walkforward as ladder


class _FakeZ:
    def __init__(self, arrays):
        self._arrays = arrays
        self.files = list(arrays)

    def __getitem__(self, key):
        return self._arrays[key]


def _data(rows=120):
    close = np.linspace(100.0, 112.0, rows)
    arrays = {
        "dc_basis_5m": np.full(rows, 99.0),
        "stoch_k_5m": np.full(rows, 60.0),
        "stoch_d_5m": np.full(rows, 40.0),
        "rsi_5m": np.full(rows, 55.0),
    }
    return SimpleNamespace(
        open=close.copy(),
        close=close,
        high=close + 1.0,
        low=close - 1.0,
        ts=np.arange(rows, dtype=np.int64) + 1_700_000_000,
        full_indices=np.arange(rows),
        z=_FakeZ(arrays),
    )


def _curve():
    return ladder.Curve(
        label="test",
        mode="linear",
        trigger="union",
        semantics="add",
        stoch_low=30.0,
        d_bottom=8.0,
        d_top=6.0,
        h4_bottom=6.0,
        h4_top=4.0,
        h1_bottom=4.0,
        h1_top=1.0,
    )


def _signals(rows):
    entry = np.zeros(rows)
    entry[0] = 1.0
    return ladder.SignalData(
        entry_mult=entry,
        event_tf=np.zeros(rows, dtype=np.int8),
        exit_event=np.zeros(rows, dtype=bool),
        exit_ref=np.full(rows, np.nan),
        causality={},
    )


def test_grid_contains_last_real_source_setting():
    grid = aug.candidates()
    assert len(grid) == 32
    assert aug.Candidate(0.5, 70.0, 0.5) in grid


def test_long_short_predicates_are_side_mirrors():
    data = _data()
    long_mask, long_audit = aug.trend_resume_mask(data, "LONG", 70.0)
    assert long_mask.all()
    assert long_audit["future_htf_source_count"] == 0

    data.z._arrays["dc_basis_5m"][:] = 113.0
    data.z._arrays["stoch_k_5m"][:] = 20.0
    data.z._arrays["stoch_d_5m"][:] = 40.0
    data.z._arrays["rsi_5m"][:] = 45.0
    short_mask, _ = aug.trend_resume_mask(data, "SHORT", 70.0)
    assert short_mask.all()


def test_false_augment_mask_preserves_frozen_control_accounting():
    data = _data()
    signals = _signals(len(data.ts))
    expected = ladder._simulate(
        data, signals, _curve(), 0, len(data.ts), 0.0005, 0.0002, "LONG"
    )
    actual = aug.simulate(
        data,
        signals,
        _curve(),
        np.zeros(len(data.ts), dtype=bool),
        aug.Candidate(0.5, 70.0, 0.5),
        0,
        len(data.ts),
        0.0005,
        0.0002,
        "LONG",
    )
    for key in (
        "capital_return_pct",
        "bh_capital_return_pct",
        "exposure_weighted_tim_pct",
        "fill_count",
        "exit_count",
        "bars_flat_beyond_reclaim",
    ):
        assert actual[key] == expected[key]
    assert actual["augment_fill_count"] == 0


def test_augment_only_scales_an_open_position_and_respects_capacity():
    data = _data()
    signals = _signals(len(data.ts))
    mask = np.ones(len(data.ts), dtype=bool)
    result = aug.simulate(
        data,
        signals,
        _curve(),
        mask,
        aug.Candidate(0.0, 70.0, 0.5),
        0,
        len(data.ts),
        0.0,
        0.0,
        "LONG",
    )
    assert result["augment_fill_count"] > 0
    assert result["peak_post_fill_notional_usd"] <= ladder.CAPACITY + 1e-6
    assert result["entry_capacity_breach"] is False
