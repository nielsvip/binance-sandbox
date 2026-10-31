import numpy as np
import pytest

from vec_decisions.live_indicator_arrays import wt_intelligence_arrays
from vec_decisions.live_indicator_labels import decode_live_indicator_labels


def test_live_indicator_array_codes_decode_to_live_labels():
    wt1 = np.asarray([-40, -30, -10, 10, 30, 10, -10, -30, -10, 10], dtype=float)
    wt2 = np.asarray([-35, -32, -15, 5, 25, 15, -5, -25, -15, 5], dtype=float)
    arrays = wt_intelligence_arrays(
        wt1, wt2, np.arange(10) + 100, np.arange(10) + 101, np.arange(10) + 99, "15m"
    )
    row = {key: values[-1].item() for key, values in arrays.items()}
    decoded = decode_live_indicator_labels(row)
    assert decoded["wt_cross_15m"] in (None, "BULL", "BEAR")
    assert decoded["wt_divergence_15m"] in (
        None,
        "BULL",
        "HIDDEN_BULL",
        "BEAR",
        "HIDDEN_BEAR",
    )
    assert decoded["wt_momentum_state_15m"] in (
        "EXHAUST_DOWN",
        "IMPULSE_DOWN",
        "IMPULSE_UP",
        "EXHAUST_UP",
    )
    assert decoded["wt_peak_structure_15m"] in (None, "HH", "LH")
    assert decoded["wt_trough_structure_15m"] in (None, "HL", "LL")
    assert decoded["wt_wave_phase_15m"] in ("TRANSITIONING", "EXPANDING", "CONTRACTING")
    assert decoded["wt_signal_15m"] in ("NEUTRAL", "BUY", "SELL")
    assert isinstance(decoded["wt_cross_rising_15m"], bool)
    assert isinstance(decoded["wt_extreme_15m"], bool)


def test_decodes_all_documented_integer_enum_values_and_structure_combination():
    source = {
        "wt_cross_1h": -1,
        "wt_divergence_1h": 2,
        "wt_momentum_state_1h": 1,
        "wt_peak_structure_1h": 1,
        "wt_trough_structure_1h": -1,
        "wt_structure_1h": 1,
        "wt_wave_phase_1h": -1,
        "wt_signal_1h": 1,
        "ha_1h": -1,
    }
    decoded = decode_live_indicator_labels(source)
    assert decoded == {
        "wt_cross_1h": "BEAR",
        "wt_divergence_1h": "HIDDEN_BULL",
        "wt_momentum_state_1h": "IMPULSE_UP",
        "wt_peak_structure_1h": "HH",
        "wt_trough_structure_1h": "LL",
        "wt_structure_1h": "HH",
        "wt_wave_phase_1h": "CONTRACTING",
        "wt_signal_1h": "BUY",
        "ha_1h": "red",
    }
    assert source["wt_structure_1h"] == 1


def test_existing_live_labels_are_preserved_and_bad_codes_fail_closed():
    source = {
        "wt_cross_15m": "BULL",
        "wt_divergence_15m": None,
        "wt_structure_15m": "HL",
    }
    assert decode_live_indicator_labels(source) == source
    with pytest.raises(ValueError, match="unsupported live indicator enum"):
        decode_live_indicator_labels({"wt_cross_15m": 2})


def test_both_scalar_replay_producers_decode_the_built_indicator_dict():
    from pathlib import Path

    source = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("backtest_v12_engine.py")
        .read_text()
    )
    assert (
        source.count("decode_live_indicator_labels(store.build_indicator_dict(idx))")
        == 2
    )


def test_decoded_npz_enum_fields_equal_actual_live_indicator_labels():
    import pandas as pd

    from ez_indicators import wavetrend_intelligence

    rng = np.random.default_rng(20261031)
    fields = (
        "wt_cross",
        "wt_divergence",
        "wt_momentum_state",
        "wt_peak_structure",
        "wt_trough_structure",
        "wt_structure",
        "wt_wave_phase",
        "wt_signal",
    )
    for offset in range(80):
        size = 15 + offset
        wt1 = np.cumsum(rng.normal(0, 18, size))
        wt2 = np.cumsum(rng.normal(0, 13, size))
        close = 100 + np.cumsum(rng.normal(0, 2, size))
        high = close + np.abs(rng.normal(0, 1, size))
        low = close - np.abs(rng.normal(0, 1, size))
        arrays = wt_intelligence_arrays(wt1, wt2, close, high, low, "1h")
        row = {key: values[-1].item() for key, values in arrays.items()}
        decoded = decode_live_indicator_labels(row)
        live = wavetrend_intelligence(
            pd.Series(wt1),
            pd.Series(wt2),
            pd.Series(close),
            pd.Series(high),
            pd.Series(low),
            "1h",
        )
        for family in fields:
            key = f"{family}_1h"
            assert decoded[key] == live[key], (offset, key, decoded[key], live[key])
        assert decoded["wt_cross_rising_1h"] is bool(live["wt_cross_rising_1h"])
        assert decoded["wt_extreme_1h"] is bool(live["wt_extreme_1h"])
