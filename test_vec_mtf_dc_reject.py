from types import SimpleNamespace

import numpy as np

from vec_paths.mtf_dc_reject import (
    build_direct_dc_reject_arrays,
    direct_dc_reject_step,
)


def _config(**updates):
    values = {
        "MTF_ARMED_ENTRY_ENABLED": False,
        "MTF_EXIT_USE_COMPOUND": True,
        "MTF_DC_REJECT_EXIT_ENABLED": True,
        "MTF_DC_REJECT_EXIT_TF": "1h",
        "MTF_DC_REJECT_EXIT_LOOKBACK": 5,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _short_arrays():
    timestamps = 1_700_000_000 + np.arange(4) * 300
    return {
        "timestamps": timestamps,
        "dc_low_1h": np.full(4, 90.0),
    }


def test_direct_dc_fires_for_an_ordinary_short_position():
    arrays = build_direct_dc_reject_arrays(
        _short_arrays(), 4, False, _config()
    )
    assert arrays["enabled"]

    outside_ts, fire = direct_dc_reject_step(
        arrays, 1, price=89.0, outside_ts=0.0, is_long=False
    )
    assert outside_ts == arrays["timestamps"][1]
    assert not fire

    outside_ts, fire = direct_dc_reject_step(
        arrays,
        2,
        price=91.0,
        outside_ts=outside_ts,
        is_long=False,
    )
    assert outside_ts == 0.0
    assert fire


def test_direct_dc_cannot_fire_on_the_same_entry_tick():
    arrays = build_direct_dc_reject_arrays(
        _short_arrays(), 4, False, _config()
    )
    outside_ts, fire = direct_dc_reject_step(
        arrays, 0, price=89.0, outside_ts=0.0, is_long=False
    )
    assert outside_ts == arrays["timestamps"][0]
    assert not fire


def test_direct_dc_bundle_data_fails_closed_on_future_parent_source():
    npz = _short_arrays()
    npz["_completed_source_ts_1h"] = np.asarray(
        npz["timestamps"], dtype=np.float64
    )
    npz["_completed_source_ts_1h"][2] = npz["timestamps"][2] + 1
    arrays = build_direct_dc_reject_arrays(npz, 4, False, _config())
    assert not arrays["enabled"]
    assert "future_completed_source:_completed_source_ts_1h" in arrays[
        "failures"
    ]


def test_direct_dc_bundle_data_fails_closed_on_missing_band():
    arrays = build_direct_dc_reject_arrays(
        {"timestamps": _short_arrays()["timestamps"]},
        4,
        False,
        _config(),
    )
    assert not arrays["enabled"]
    assert "dc_band_missing:dc_low_1h" in arrays["failures"]


def test_sweep_route_closes_seeded_ordinary_position_on_later_tick():
    import v12_wide_engine as vec
    from tools.c4_vector_bundle_screen import floor_config

    n = 60
    timestamps = 1_700_000_000 + np.arange(n) * 300
    close = np.full(n, 91.0, dtype=np.float32)
    close[0] = 100.0
    close[1] = 89.0
    npz = {
        "timestamps": timestamps,
        "close": close,
        "dc_low_1h": np.full(n, 90.0, dtype=np.float32),
        "dc_high_1h": np.full(n, 110.0, dtype=np.float32),
    }
    config = floor_config(vec)
    config.MTF_ARMED_ENTRY_ENABLED = False
    config.MTF_EXIT_USE_COMPOUND = True
    config.MTF_DC_REJECT_EXIT_ENABLED = True
    config.MTF_DC_REJECT_EXIT_TF = "1h"
    config.MTF_DC_REJECT_EXIT_LOOKBACK = 5
    config.MTF_ATR_TRAIL_ENABLED = False
    config.MTF_BB_REJECT_EXIT_ENABLED = False
    config.MTF_GR_EXIT_GATE_ENABLED = False
    config.MTF_WT_CROSS_EXIT_ENABLED = False

    events, _, _ = vec.simulate_one_symbol(
        "TTD",
        "SHORT",
        "tradier",
        config,
        _npz_cache=(npz, timestamps),
        seed_position_at_start=True,
    )
    closes = [event for event in events if event.type == "CLOSE"]
    assert len(closes) == 1
    assert closes[0].reason.startswith("MTF_DC_REJECT_1h")
    assert closes[0].ts == timestamps[2]


def test_vector_companion_does_not_change_exact_fingerprint_file_set():
    from tools import persym_baseline_campaign as campaign

    assert "v8_vec_sweep.py" not in campaign.MATRIX_CONTRACT_FILES
    assert "vec_paths/mtf_dc_reject.py" not in campaign.MATRIX_CONTRACT_FILES
    assert (
        "tools/c4_vector_companion_inventory.py"
        not in campaign.MATRIX_CONTRACT_FILES
    )
