import numpy as np

import v12_quick_engine as v12


def test_simulate_one_passes_weighted_position_basis_to_ppl_after_augment(monkeypatch):
    n = 5
    prices = np.array([100.0, 105.0, 105.0, 105.0, 105.0])
    timestamps = np.arange(n, dtype=float) * 900.0 + 1700000000.0
    data = {
        "timestamps": timestamps,
        "close": prices.copy(),
        "close_15m": prices.copy(),
        "open_15m": prices.copy(),
        "high_15m": prices.copy(),
        "low_15m": prices.copy(),
        "wt1_3m": np.array([0.0, 2.0, 2.0, 2.0, 2.0]),
        "wt2_3m": np.ones(n),
        "wt1_15m": np.array([0.0, 2.0, 2.0, 2.0, 2.0]),
        "wt2_15m": np.ones(n),
        "wt1_1h": np.ones(n),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.ones(n),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.ones(n),
        "wt2_D": np.zeros(n),
        "wt_trough_structure_15m": np.zeros(n),
        "wt_peak_structure_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 80.0),
        "dc_high_15m": np.full(n, 120.0),
        "dc_low_4h": np.full(n, 70.0),
        "dc_high_4h": np.full(n, 130.0),
    }
    cfg = v12.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG")
    for key, value in {
        "MODE": "crypto",
        "BASE_TF": "15m",
        "START_POSITION_SIZE": 500.0,
        "COOLDOWN_BARS": 0,
        "MIN_HOLD_BARS": 0,
        "MIN_GAIN": 3.0,
        "USE_1M_3M_SIGNALS_ENABLED": False,
        "WT_WINNER_PULLBACK_AUGMENT_ENABLED": True,
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
        "PARTIAL_PROFIT_LOCK_ENABLED": False,
        "CYCLE_TP_TIERED_ENABLED": False,
        "QUICK_CYCLE_TP_ENABLED": False,
        "HAIKU_WINNER_AUGMENT_ENABLED": False,
    }.items():
        setattr(cfg, key, value)
    cfg._PARITY_PREFIX_EVAL = True

    seen = []

    def record_ppl(config, is_tradier, is_long, px, entry_px, gain_pct, state):
        seen.append((float(px), float(entry_px)))
        return "", 0.0, "", state

    monkeypatch.setattr(v12.vec_decisions.reduce_profit_lock, "ppl_step", record_ppl)
    result = v12.simulate_one(data, "PPL_WEIGHTED_BASIS_TEST", True, cfg, force_initial_seed=True)
    augment = next(row for row in result["ledger"] if row.get("type") == "AUGMENT")
    assert augment["bar"] == 1
    bar_one_basis = next(entry for px, entry in seen if px == 105.0)
    assert 100.0 < bar_one_basis < 105.0
