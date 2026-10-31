from types import SimpleNamespace

import numpy as np

from vec_decisions.ez_missing_dc_bb_d_break_reverse import (
    dc_bb_d_break_reverse_scan,
    dc_bb_d_break_reverse_state_step,
)


def _cfg(**kwargs):
    values = {
        "DC_BB_D_BREAK_REVERSE_ENABLED": True,
        "PARABOLIC_PROTECTION_ENABLED": True,
        "PARABOLIC_RSI_4H_MIN": 70.0,
        "PARABOLIC_RSI_1H_MIN": 65.0,
        "PARABOLIC_BB_PCT_B_4H_MIN": 0.70,
        "PARABOLIC_RSI_4H_MAX": 30.0,
        "PARABOLIC_RSI_1H_MAX": 35.0,
        "PARABOLIC_BB_PCT_B_4H_MAX": 0.10,
        "DC_BB_CROSSBACK_HYSTERESIS_PCT": 2.0,
        "BB_BREAKOUT_CONT_ENABLED": True,
        "BB_BREAKOUT_CONT_HOURS": 72.0,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def _npz(close, timestamps):
    n = len(close)
    return {
        "close": np.asarray(close, dtype=float),
        "timestamps": np.asarray(timestamps, dtype=float),
        "dc_high_D_prev": np.full(n, 100.0),
        "dc_low_D_prev": np.full(n, 90.0),
        "bb_upper_D": np.full(n, 110.0),
        "bb_lower_D": np.full(n, 80.0),
        "rsi_4h": np.full(n, 50.0),
        "rsi_1h": np.full(n, 50.0),
        "bb_pct_b_4h": np.full(n, 0.5),
    }


def test_state_scan_matches_scalar_walk_and_emits_both_directions_at_72h():
    t0 = 1_700_000_000.0
    timestamps = [t0, t0 + 71.999 * 3600, t0 + 72 * 3600,
                  t0 + 72.001 * 3600, t0 + 143.999 * 3600,
                  t0 + 144 * 3600]
    npz = _npz([101.0, 97.0, 97.0, 89.0, 93.0, 93.0], timestamps)
    cfg = _cfg()
    actual = dc_bb_d_break_reverse_scan(npz, len(timestamps), cfg)
    state = {}
    expected_target, expected_event, expected_band = [], [], []
    for i in range(len(timestamps)):
        target, event, band = dc_bb_d_break_reverse_state_step(
            state,
            npz["close"][i],
            npz["dc_high_D_prev"][i],
            npz["dc_low_D_prev"][i],
            npz["bb_upper_D"][i],
            npz["bb_lower_D"][i],
            timestamps[i],
            cfg.DC_BB_CROSSBACK_HYSTERESIS_PCT,
            cfg.BB_BREAKOUT_CONT_ENABLED,
            cfg.BB_BREAKOUT_CONT_HOURS,
        )
        expected_target.append(target)
        expected_event.append(event)
        expected_band.append(band if target else "")
    actual_target = np.where(actual["target_long"], 1, np.where(actual["target_short"], -1, 0))
    assert actual_target.tolist() == expected_target == [1, 0, -1, -1, 0, 1]
    assert actual["event"].tolist() == expected_event
    assert actual["band"].tolist() == expected_band
    assert actual["close_long"].tolist() == [False, False, True, True, False, False]
    assert actual["open_short"].tolist() == [False, False, True, True, False, False]
    assert actual["close_short"].tolist() == [True, False, False, False, False, True]
    assert actual["open_long"].tolist() == [True, False, False, False, False, True]


def test_parabolic_protection_vetoes_only_wrong_side_exit_and_reverse_open():
    t0 = 1_700_000_000.0
    down = _npz([89.0], [t0])
    down["rsi_4h"][:] = 75.0
    down["rsi_1h"][:] = 70.0
    down["bb_pct_b_4h"][:] = 0.8
    blocked = dc_bb_d_break_reverse_scan(down, 1, _cfg())
    assert blocked["target_short"].tolist() == [True]
    assert blocked["close_long"].tolist() == [False]
    assert blocked["open_short"].tolist() == [False]

    down["rsi_4h"][:] = 50.0
    down["rsi_1h"][:] = 50.0
    down["bb_pct_b_4h"][:] = 0.5
    allowed = dc_bb_d_break_reverse_scan(down, 1, _cfg())
    assert allowed["close_long"].tolist() == [True]
    assert allowed["open_short"].tolist() == [True]


def test_switch_off_and_missing_timestamps_fail_closed():
    npz = _npz([101.0, 97.0], [1_700_000_000.0, 1_700_100_000.0])
    disabled = dc_bb_d_break_reverse_scan(
        npz, 2, _cfg(DC_BB_D_BREAK_REVERSE_ENABLED=False)
    )
    assert not any(mask.any() for key, mask in disabled.items() if key not in ("event", "band"))
    missing_time = dc_bb_d_break_reverse_scan(
        {key: value for key, value in npz.items() if key != "timestamps"}, 2, _cfg()
    )
    assert not any(mask.any() for key, mask in missing_time.items() if key not in ("event", "band"))
