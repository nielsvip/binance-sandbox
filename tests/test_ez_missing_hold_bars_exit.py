import numpy as np
import v12_quick_engine as V
from datetime import datetime, timezone

from vec_decisions.ez_missing_hold_bars_exit import (
    optimal_hold_bars_exit_live_adapter,
    optimal_hold_bars_exit_mask,
    satoshit_standard_exits_skipped,
    unrealized_position_gain_pct,
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
    return bool(anchor and (now_ts - anchor).total_seconds() > cfg.OPTIMAL_HOLD_BARS_3M * 180)


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
        now_dt = datetime.fromtimestamp(now_ts, timezone.utc)
        opened_dt = datetime.fromtimestamp(opened_at, timezone.utc) if opened_at else None
        last_aug_dt = datetime.fromtimestamp(last_aug, timezone.utc)
        assert optimal_hold_bars_exit_live_adapter(cfg, gain, now_dt, opened_dt, last_aug_dt, active, skip) == _live_source(
            cfg, gain, now_dt, opened_dt, last_aug_dt, active, skip
        )
    assert optimal_hold_bars_exit_live_adapter(cfg, 0.6, datetime.fromtimestamp(5000, timezone.utc), datetime.fromtimestamp(1000, timezone.utc), datetime.fromtimestamp(4700, timezone.utc), True, False)
    assert not optimal_hold_bars_exit_live_adapter(cfg, 0.6, 5000.0, 1000.0, 4700.0, True, False)


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


def test_hold_gain_after_augment_and_reduce_matches_native_average_entry():
    # Native augmentation moves entry_price to the fill-weighted average; reduction
    # realizes PnL but does not add it to Position.gain.
    quantity = 1.0
    average_entry = 100.0
    realized = 0.0
    augment_quantity = 1.0
    augment_price = 110.0
    average_entry = (
        quantity * average_entry + augment_quantity * augment_price
    ) / (quantity + augment_quantity)
    quantity += augment_quantity
    reduce_quantity = 1.0
    reduce_price = 115.0
    realized += (reduce_price - average_entry) * reduce_quantity
    quantity -= reduce_quantity
    current_price = 110.0
    native_gain = (current_price - average_entry) / average_entry * 100.0
    lifecycle_gain = (
        realized + (current_price - average_entry) * quantity
    ) / (average_entry * (quantity + reduce_quantity)) * 100.0
    assert quantity == 1.0
    assert unrealized_position_gain_pct(True, current_price, average_entry) == native_gain
    assert native_gain == 4.761904761904762
    assert lifecycle_gain != native_gain
    assert not optimal_hold_bars_exit_mask(
        {}, 4, Config(), np.full(4, 0.6), np.full(4, 1000.0), np.full(4, 4700.0), True, False
    ).any()


def test_satoshit_protection_matches_latest_live_reason_state():
    from types import SimpleNamespace

    from ez_satoshit import is_satoshit_trade

    cfg = V.QuickConfig()
    cases = [
        ("SATOSHIT_ENTRY", [], True, True),
        ("VECTOR_ENTRY", ["SATOSHIT_AUGMENT"], True, True),
        ("SATOSHIT_ENTRY", ["REGULAR_AUGMENT"], True, False),
        ("SATOSHIT_ENTRY", [], False, False),
    ]
    for entry_reason, aug_reasons, enabled, expected in cases:
        cfg.SATOSHIT_PROTECT_TRADES = enabled
        cfg.SATOSHIT_ENABLED = enabled
        actual = satoshit_standard_exits_skipped(cfg, entry_reason, aug_reasons)
        live = is_satoshit_trade(
            SimpleNamespace(
                augment_reason=(aug_reasons[-1] if aug_reasons else entry_reason),
                last_signal="OPEN",
            )
        ) and cfg.SATOSHIT_PROTECT_TRADES and cfg.SATOSHIT_ENABLED
        assert actual == live == expected


def test_hold_bars_switch_flips_simulate_one_close_ledger():
    n = 120
    close = np.empty(n)
    close[:8] = 100.0 * (1.005 ** np.arange(8))
    close[8:37] = close[7] * (0.999 ** np.arange(1, 30))
    close[37:] = close[36] * (1.006 ** np.arange(1, n - 36))
    npz = {
        "close": close,
        "close_15m": close,
        "timestamps": 1_700_000_000.0 + np.arange(n) * 900.0,
        "timestamp_15m": 1_700_000_000.0 + np.arange(n) * 900.0,
        "open_15m": close,
        "high_15m": close,
        "low_15m": close * 0.999,
        "volume_15m": np.ones(n),
        "wt1_15m": np.array([5.0] * 7 + [20.0] * (n - 7)),
        "wt2_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 1.0),
        "dc_high_15m": np.full(n, 1_000.0),
        "stoch_k_15m": np.full(n, 62.0),
        "stoch_d_15m": np.full(n, 58.0),
        "stoch_k_15m_prev": np.full(n, 60.0),
        "stoch_k_1h": np.full(n, 50.0),
        "stoch_k": np.full(n, 50.0),
        "wt1_1h": np.full(n, 5.0),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.full(n, 5.0),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.full(n, 5.0),
        "wt2_D": np.zeros(n),
        "wt_velocity_1h": np.zeros(n),
        "dc_low_4h": np.full(n, 10.0),
        "dc_high_4h": np.full(n, 102.0),
        "dc_high_4h_ant": np.full(n, 100.0),
    }

    def ledger(enabled):
        cfg = V.QuickConfig()
        cfg.OPTIMAL_HOLD_BARS_EXIT_ENABLED = enabled
        cfg.OPTIMAL_HOLD_BARS_3M = 1
        cfg.GAIN_THRESHOLD_LOW = 0.2
        cfg.NOLOSS_MIN_PROFIT_PCT = 0.2
        cfg.MIN_HOLD_BARS = 0
        cfg.ENTRY_ISOLATE_FAMILY = "B04"
        cfg.REENTRY_B04_DC_RETEST_ENABLED = True
        cfg.CYCLE_TP_ENABLED = False
        cfg.PARTIAL_PROFIT_LOCK_ENABLED = False
        cfg.AUGMENTED_DC_BREAK_ENABLED = False
        return V.simulate_one(npz, "HOLD_BARS", True, cfg, force_initial_seed=True)["ledger"]

    off = [row for row in ledger(False) if row.get("reason") == "HOLD_BARS_EXIT"]
    on = [row for row in ledger(True) if row.get("reason") == "HOLD_BARS_EXIT"]
    assert off == []
    assert len(on) == 1 and on[0].get("type") == "CLOSE"


def test_satoshit_protection_switch_suppresses_hold_in_source_tagged_ledger(monkeypatch):
    monkeypatch.setenv("V12_SETUP_MEMO", "0")
    n = 120
    close = np.empty(n)
    close[:8] = 100.0 * (1.005 ** np.arange(8))
    close[8:37] = close[7] * (0.999 ** np.arange(1, 30))
    close[37:] = close[36] * (1.006 ** np.arange(1, n - 36))
    npz = {
        "close": close,
        "close_15m": close,
        "timestamps": 1_700_000_000.0 + np.arange(n) * 900.0,
        "timestamp_15m": 1_700_000_000.0 + np.arange(n) * 900.0,
        "open_15m": close,
        "high_15m": close,
        "low_15m": close * 0.999,
        "volume_15m": np.ones(n),
        "wt1_15m": np.array([5.0] * 7 + [20.0] * (n - 7)),
        "wt2_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 10.0),
        "dc_high_15m": np.full(n, 200.0),
        "stoch_k_15m": np.full(n, 62.0),
        "stoch_d_15m": np.full(n, 58.0),
        "stoch_k_15m_prev": np.full(n, 60.0),
        "stoch_k_1h": np.full(n, 50.0),
        "stoch_k": np.full(n, 50.0),
        "wt1_1h": np.full(n, 5.0),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.full(n, 5.0),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.full(n, 5.0),
        "wt2_D": np.zeros(n),
        "wt_velocity_1h": np.zeros(n),
        "dc_low_4h": np.full(n, 10.0),
        "dc_high_4h": np.full(n, 102.0),
        "dc_high_4h_ant": np.full(n, 100.0),
    }
    original = V.compute_reentry_blocks

    def tagged_blocks(*args, **kwargs):
        blocks = original(*args, **kwargs)
        if "B04" in blocks:
            blocks["SATOSHIT_ENTRY"] = blocks.pop("B04")
        return blocks

    monkeypatch.setattr(V, "compute_reentry_blocks", tagged_blocks)

    def closes(protected):
        cfg = V.QuickConfig()
        cfg.OPTIMAL_HOLD_BARS_EXIT_ENABLED = True
        cfg.OPTIMAL_HOLD_BARS_3M = 1
        cfg.GAIN_THRESHOLD_LOW = 0.2
        cfg.NOLOSS_MIN_PROFIT_PCT = 0.2
        cfg.MIN_HOLD_BARS = 0
        cfg.ENTRY_ISOLATE_FAMILY = "SATOSHIT_ENTRY"
        cfg.REENTRY_B04_DC_RETEST_ENABLED = True
        cfg.SATOSHIT_PROTECT_TRADES = protected
        cfg.SATOSHIT_ENABLED = True
        cfg.CYCLE_TP_ENABLED = False
        cfg.CYCLE_TP_TIERED_ENABLED = False
        cfg.PARTIAL_PROFIT_LOCK_ENABLED = False
        cfg.AUGMENTED_DC_BREAK_ENABLED = False
        result = V.simulate_one(npz, "SATOSHIT_HOLD", True, cfg, force_initial_seed=False)
        opened = [row for row in result["ledger"] if row.get("type") == "OPEN"]
        assert opened and "SATOSHIT" in str(opened[0].get("reason", ""))
        return [row for row in result["ledger"] if row.get("reason") == "HOLD_BARS_EXIT"]

    assert closes(False)
    assert closes(True) == []
