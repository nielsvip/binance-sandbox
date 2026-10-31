import numpy as np

from vec_decisions.ez_missing_hold_bars_exit import (
    optimal_hold_bars_exit_live_adapter,
    optimal_hold_bars_exit_mask,
)


class Config:
    OPTIMAL_HOLD_BARS_EXIT_ENABLED = True
    TF_FOCUS = "3m"
    GAIN_THRESHOLD_LOW = 0.3
    NOLOSS_MIN_PROFIT_PCT = 0.5
    OPTIMAL_HOLD_BARS_3M = 21


def _live_source(cfg, gain_pct, now_ts, opened_at, last_augmentation, active, skip):
    if skip or not active:
        return False
    if gain_pct <= max(cfg.GAIN_THRESHOLD_LOW, cfg.NOLOSS_MIN_PROFIT_PCT):
        return False
    if cfg.TF_FOCUS != "3m":
        return False
    anchor = opened_at or last_augmentation
    return bool(anchor and now_ts - anchor > cfg.OPTIMAL_HOLD_BARS_3M * 180)


def test_scalar_adapter_matches_live_source_and_open_time_precedence():
    cfg = Config()
    cases = [
        (0.6, 5000.0, 1000.0, 4700.0, True, False),
        (0.5, 5000.0, 1000.0, 4700.0, True, False),
        (0.6, 5000.0, 0.0, 4700.0, True, False),
        (0.6, 5000.0, 1000.0, 4700.0, True, True),
        (0.6, 5000.0, 1000.0, 4700.0, False, False),
    ]
    for gain, now_ts, opened_at, last_aug, active, skip in cases:
        assert optimal_hold_bars_exit_live_adapter(cfg, gain, now_ts, opened_at, last_aug, active, skip) == _live_source(
            cfg, gain, now_ts, opened_at, last_aug, active, skip
        )
    assert optimal_hold_bars_exit_live_adapter(cfg, 0.6, 5000.0, 1000.0, 4700.0, True, False)
    assert not optimal_hold_bars_exit_live_adapter(cfg, 0.6, 5000.0, 0.0, 4700.0, True, False)


def test_vector_adapter_uses_real_bar_timestamps_and_strict_age_boundary():
    cfg = Config()
    npz = {"timestamps": np.array([1000.0, 4779.0, 4780.0, 5000.0])}
    actual = optimal_hold_bars_exit_mask(
        npz,
        4,
        cfg,
        np.full(4, 0.6),
        np.full(4, 1000.0),
        np.full(4, 4700.0),
        np.ones(4, dtype=bool),
        np.zeros(4, dtype=bool),
    )
    assert actual.tolist() == [False, False, False, True]
    cfg.OPTIMAL_HOLD_BARS_EXIT_ENABLED = False
    assert not optimal_hold_bars_exit_mask(
        npz, 4, cfg, np.full(4, 0.6), np.full(4, 1000.0), np.full(4, 4700.0), True, False
    ).any()
    assert not optimal_hold_bars_exit_mask(
        {}, 4, Config(), np.full(4, 0.6), np.full(4, 1000.0), np.full(4, 4700.0), True, False
    ).any()
