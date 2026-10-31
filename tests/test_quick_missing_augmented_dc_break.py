"""Paired 15m AUGMENTED_DC_BREAK source and v12 ledger tests."""
import numpy as np
import pytest

import v12_quick_engine as v12
from vec_decisions import live_exit_chain as lec
from vec_decisions.quick_missing_augmented_dc_break import (
    augmented_dc_break_fires,
    augmented_dc_break_pos_min_qty,
    augmented_dc_break_reduce_qty,
)


def _arrays():
    n = 100
    close = np.full(n, 99.3, dtype=float)
    close[0] = 99.0
    dc_low_15m = np.full(n, 99.0)
    dc_low_15m[0] = 80.0
    dc_low_15m[1] = 100.0
    return {
        "timestamps": np.arange(n, dtype=float) * 900.0 + 1700000000.0,
        "close": close,
        "close_15m": close,
        "open_15m": close,
        "high_15m": close,
        "low_15m": close,
        "dc_high_15m": np.full(n, 110.0),
        "dc_low_15m": dc_low_15m,
        "dc_high_15m_prev": np.full(n, 110.0),
        "dc_low_15m_prev": dc_low_15m,
        "dc_high_4h": np.full(n, 150.0),
        "dc_low_4h": np.full(n, 50.0),
        "dc_high_4h_prev": np.full(n, 150.0),
        "dc_low_4h_prev": np.full(n, 50.0),
    }


def _config(enabled):
    cfg = v12.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG")
    values = {
        "MODE": "crypto",
        "BASE_TF": "15m",
        "START_POSITION_SIZE": 500.0,
        "MIN_POSITION_SIZE": 1.0,
        "NOLOSS_MIN_PROFIT_PCT": 0.5,
        "COOLDOWN_BARS": 0,
        "MIN_HOLD_BARS": 0,
        "AUGMENTED_DC_BREAK_ENABLED": enabled,
        "LIVE_EXIT_CHAIN_ENABLED": True,
        "ABLATION_DISABLE_QUICK_EXIT": False,
        "HEDGE_MODE": False,
        "CYCLE_TP_TIERED_ENABLED": False,
        "PARTIAL_PROFIT_LOCK_ENABLED": False,
        "NOLOSS_ENABLED": False,
        "STOP_LOSS_ENABLED": False,
        "PROFIT_TARGET_ENABLED": False,
        "ALL_TF_AGAINST_CLOSE_ENABLED": False,
        "ALL_TF_AGAINST_FORCE_CLOSE_ENABLED": False,
        "TECHNICAL_DC_STOP_TF": "OFF",
        "TECHNICAL_DC_TARGET_TF": "OFF",
        "DAYTRADE_DC_STOP_TF": "OFF",
        "DAYTRADE_DC_TARGET_TF": "OFF",
        "DC_DAYTRADE_ENABLED": False,
        "TRADIER_DC_DAYTRADE_ENABLED": False,
        "WT_CROSS_EXIT_ENABLED": False,
        "PEAK_GIVEBACK_PROTECTION_ENABLED": False,
        "BREAKEVEN_DC_LOW4_ENABLED": False,
        "KEY_LEVEL_CRASH_ENABLED": False,
        "MOMENTUM_TP_ENABLED": False,
        "BREAKEVEN_GAIN_EROSION_ENABLED": False,
        "ULTIMATE_DC_HARD_STOP_ENABLED": False,
    }
    for key, value in values.items():
        setattr(cfg, key, value)
    return cfg


def test_augmented_dc_break_boundaries_and_source_reduce_bands():
    assert augmented_dc_break_fires(5.0, 1.0, 99.0, 100.0, 0.1001, True, False, True, False)
    assert not augmented_dc_break_fires(1.2, 1.0, 99.0, 100.0, 2.0, True, False, True, False)
    assert not augmented_dc_break_fires(5.0, 1.0, 100.0, 100.0, 2.0, True, False, True, False)
    assert not augmented_dc_break_fires(5.0, 1.0, 99.0, 100.0, 2.0, True, True, True, False)
    assert not augmented_dc_break_fires(5.0, 1.0, 99.0, 100.0, 2.0, True, False, True, True)
    assert augmented_dc_break_reduce_qty(5.0, 1.0, 0.4, 0.5, 0.15, 0.25, 0.5) == 4.1
    assert augmented_dc_break_reduce_qty(5.0, 1.0, 0.8, 0.5, 0.15, 0.25, 0.5) == 1.25
    assert augmented_dc_break_reduce_qty(5.0, 1.0, 2.0, 0.5, 0.15, 0.25, 0.5) == 2.5


def test_live_exit_chain_requires_supported_pair_and_uses_symbol_minimum():
    cfg = _config(True)
    npz = _arrays()
    n = len(npz["close"])
    safe = lambda data, key, size, default: np.asarray(
        data.get(key, np.full(size, default)), dtype=float
    )
    lec.validate_supported_inputs(npz, n, cfg, True)
    prepared = lec.prepare(npz, n, True, cfg, npz["close"], safe, symbol="1000PEPEUSDC")
    min_qty = augmented_dc_break_pos_min_qty("1000PEPEUSDC", 99.3, 1.0)
    assert min_qty >= 2.0 / 99.3
    hit = lec.step(
        prepared,
        cfg,
        1,
        99.3,
        {
            "gain": (99.3 / 99.0 - 1.0) * 100.0,
            "peak": 0.31,
            "age_min": 15.0,
            "entry_price": 99.0,
            "position_qty": 5.0,
        },
    )
    assert hit == ("AUGMENTED_DC_BREAK_REDUCE_TO_MIN_0.30%", False)
    cfg.HEDGE_MODE = True
    with pytest.raises(ValueError, match="active_hedges tracker state"):
        lec.validate_supported_inputs(npz, n, cfg, True)


def test_simulate_one_books_augmented_dc_break_only_when_switch_is_on():
    npz = _arrays()
    on = v12.simulate_one(
        npz, "1000PEPEUSDC", True, _config(True), force_initial_seed=True
    )
    off = v12.simulate_one(
        npz, "1000PEPEUSDC", True, _config(False), force_initial_seed=True
    )
    on_rows = [
        row for row in on["ledger"]
        if str(row.get("reason", "")).startswith("AUGMENTED_DC_BREAK_REDUCE_TO_MIN_")
    ]
    off_rows = [
        row for row in off["ledger"]
        if str(row.get("reason", "")).startswith("AUGMENTED_DC_BREAK_REDUCE_TO_MIN_")
    ]
    opens = [row for row in on["ledger"] if row.get("type") == "OPEN"]
    assert len(opens) == 1
    assert len(on_rows) == 1 and on_rows[0]["bar"] == 1
    assert not off_rows
    pos_min = augmented_dc_break_pos_min_qty("1000PEPEUSDC", 99.3, 1.0)
    expected = augmented_dc_break_reduce_qty(
        opens[0]["qty"], pos_min, 0.3, 0.5, 0.15, 0.25, 0.5
    )
    assert np.isclose(on_rows[0]["qty"], expected)
