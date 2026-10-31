import numpy as np

import v12_quick_engine as v12


def _cfg():
    cfg = v12.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG")
    values = {
        "MODE": "crypto",
        "BASE_TF": "15m",
        "START_POSITION_SIZE": 500.0,
        "COOLDOWN_BARS": 0,
        "MIN_HOLD_BARS": 0,
        "MIN_GAIN": 3.0,
        "USE_1M_3M_SIGNALS_ENABLED": False,
        "AUGMENT_AT_LOSS_ENABLED": True,
        "AUG_GAIN_GATE_LIVE_TWIN_ENABLED": True,
        "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED": True,
        "AUGMENT_ONLY_WHEN_PROFITABLE": False,
        "AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER": False,
        "WT_WINNER_PULLBACK_AUGMENT_ENABLED": False,
        "HAIKU_WINNER_AUGMENT_ENABLED": False,
        "HTF_DIRECTION_GATE_ENABLED": False,
        "HTF_GATE_APPLY_TO_AUGMENT": False,
        "STRUCTURAL_RANGE_SHIFT_EXIT": False,
        "LIVE_EXIT_CHAIN_ENABLED": False,
        "STOP_LOSS_ENABLED": False,
        "NOLOSS_ENABLED": False,
        "PROFIT_TARGET_ENABLED": False,
        "PEAK_GIVEBACK_PROTECTION_ENABLED": False,
        "AUGMENTED_DC_BREAK_ENABLED": False,
        "BOUNCE_AUGMENT_ENABLED": False,
        "PYRAMID_ENABLED": False,
        "WT_CROSS_EXIT_ENABLED": False,
        "ULTIMATE_DC_HARD_STOP_ENABLED": False,
        "PPL_ENABLED": False,
        "PARTIAL_PROFIT_LOCK_ENABLED": False,
        "CYCLE_TP_TIERED_ENABLED": False,
        "QUICK_CYCLE_TP_ENABLED": False,
    }
    for key, value in values.items():
        setattr(cfg, key, value)
    cfg._PARITY_PREFIX_EVAL = True
    return cfg


def test_shared_gain_gate_blocks_at_loss_source_augment_in_ledger():
    prices = np.array([100.0, 99.0, 99.0, 99.0])
    data = {
        "timestamps": np.arange(4, dtype=float) * 900.0 + 1700000000.0,
        "close": prices.copy(),
        "close_15m": prices.copy(),
        "open_15m": prices.copy(),
        "high_15m": prices.copy(),
        "low_15m": prices.copy(),
        "low_15m_prev": np.array([80.0, 81.0, 82.0, 83.0]),
        "dc_low_15m_prev": np.array([70.0, 70.0, 71.0, 72.0]),
        "wt1_15m": np.full(4, 2.0),
        "wt2_15m": np.full(4, 1.0),
        "wt1_1h": np.full(4, 1.0),
        "wt2_1h": np.zeros(4),
        "wt_trough_structure_15m": np.zeros(4),
        "wt_peak_structure_15m": np.zeros(4),
        "dc_low_15m": np.full(4, 80.0),
        "dc_high_15m": np.full(4, 120.0),
        "dc_low_4h": np.full(4, 70.0),
        "dc_high_4h": np.full(4, 130.0),
    }
    result = v12.simulate_one(data, "AAL_GATE_PROBE", True, _cfg(), force_initial_seed=True)
    assert any(row.get("type") == "OPEN" and row.get("bar") == 0 for row in result["ledger"])
    assert not any(row.get("type") == "AUGMENT" and "AUGMENT_AT_LOSS" in row.get("reason", "") for row in result["ledger"])
