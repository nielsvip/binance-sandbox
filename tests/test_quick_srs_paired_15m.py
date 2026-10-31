"""STRUCTURAL_RANGE_SHIFT uses paired 15m WT semantics and native event precedence."""
import numpy as np

import v12_quick_engine as v12
from vec_decisions import live_exit_chain as lec
from vec_decisions.check_exit_candidates_crypto__structural_range_shift import (
    check_structural_range_shift,
)


def _npz(bb_recovery=False):
    n = 100
    close = np.full(n, 100.95, dtype=float)
    close[0] = 100.1
    close[1] = 100.9
    arrays = {
        "timestamps": np.arange(n, dtype=float) * 900.0 + 1700000000.0,
        "close": close,
        "close_15m": close,
        "open_15m": close,
        "high_15m": close,
        "low_15m": close,
        "dc_high_15m": np.full(n, 110.0),
        "dc_low_15m": np.full(n, 90.0),
        "dc_high_15m_prev": np.full(n, 110.0),
        "dc_low_15m_prev": np.full(n, 90.0),
        "dc_high_4h": np.full(n, 100.0),
        "dc_low_4h": np.full(n, 80.0),
        "dc_high_4h_prev": np.full(n, 100.0),
        "dc_low_4h_prev": np.full(n, 80.0),
    }
    for tf in ("15m", "1h", "4h", "D"):
        arrays[f"wt1_{tf}"] = np.full(n, -2.0)
        arrays[f"wt2_{tf}"] = np.zeros(n)
        arrays[f"k_{tf}"] = np.full(n, 50.0)
        arrays[f"k_{tf}_prev"] = np.full(n, 50.0)
        arrays[f"stoch_k_{tf}"] = np.full(n, 50.0)
        arrays[f"stoch_k_{tf}_prev"] = np.full(n, 50.0)
    arrays["k_1h"][1] = arrays["k_15m"][1] = 80.0
    arrays["k_1h_prev"][1] = arrays["k_15m_prev"][1] = 81.0
    if bb_recovery:
        arrays["bb_upper_1h"] = np.full(n, 101.0)
        arrays["bb_lower_1h"] = np.full(n, 99.0)
        arrays["k_1h"][1] = 80.0
    return arrays


def _cfg(bb_recovery=False):
    cfg = v12.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG")
    values = {
        "MODE": "crypto",
        "BASE_TF": "15m",
        "START_POSITION_SIZE": 500.0,
        "COOLDOWN_BARS": 0,
        "MIN_HOLD_BARS": 0,
        "STRUCTURAL_RANGE_SHIFT_EXIT": True,
        "STRUCTURAL_RANGE_SHIFT_TF": "dc_4h",
        "STRUCTURAL_RANGE_SHIFT_K_HIGH": 75.0,
        "STRUCTURAL_RANGE_SHIFT_K_LOW": 25.0,
        "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS": 100.0,
        "LIVE_EXIT_CHAIN_ENABLED": True,
        "ABLATION_DISABLE_QUICK_EXIT": False,
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
        "AUGMENTED_DC_BREAK_ENABLED": False,
        "KEY_LEVEL_CRASH_ENABLED": False,
        "MOMENTUM_TP_ENABLED": False,
        "BREAKEVEN_GAIN_EROSION_ENABLED": False,
        "LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK": True,
        "ULTIMATE_DC_HARD_STOP_ENABLED": False,
        "BB_RECOVERY_EXIT_ENABLED_TRADIER": bb_recovery,
    }
    for key, value in values.items():
        setattr(cfg, key, value)
    return cfg


def test_srs_scalar_pairs_source_3m_confirmation_to_real_15m_wt():
    cfg = _cfg()
    indicators = {
        "dc_high_4h": 100.0,
        "dc_low_4h": 80.0,
        "stoch_k_1h": 80.0,
        "stoch_k_1h_prev": 81.0,
        "stoch_k_15m": 80.0,
        "stoch_k_15m_prev": 81.0,
        "wt1_1h": -2.0,
        "wt2_1h": 0.0,
        "wt1_15m": -2.0,
        "wt2_15m": 0.0,
        "wt1_3m": 2.0,
        "wt2_3m": 0.0,
    }
    fires, reason = check_structural_range_shift(cfg, indicators, 100.5, True, 101.0)
    assert fires and reason.startswith("STRUCTURAL_RANGE_SHIFT_LONG_CASCADE_dc_4h")
    indicators["wt1_15m"], indicators["wt2_15m"] = 2.0, 0.0
    assert not check_structural_range_shift(cfg, indicators, 100.5, True, 101.0)[0]


def test_srs_only_books_one_partial_reduce_at_source_gain_band():
    result = v12.simulate_one(
        _npz(), "SRS_TEST", True, _cfg(), force_initial_seed=True
    )
    opens = [row for row in result["ledger"] if row.get("type") == "OPEN"]
    reductions = [
        row for row in result["ledger"]
        if row.get("type") == "REDUCE"
        and str(row.get("reason", "")).startswith("STRUCTURAL_RANGE_SHIFT_")
    ]
    assert len(opens) == 1
    assert len(reductions) == 1
    assert reductions[0]["bar"] == 1
    assert np.isclose(reductions[0]["qty"], opens[0]["qty"] * 0.25)


def test_bb_recovery_precedes_srs_and_emits_only_one_reduce_same_bar():
    result = v12.simulate_one(
        _npz(bb_recovery=True),
        "1000PEPEUSDC",
        True,
        _cfg(bb_recovery=True),
        force_initial_seed=True,
    )
    reductions = [row for row in result["ledger"] if row.get("type") == "REDUCE"]
    bar_one = [row for row in reductions if row.get("bar") == 1]
    assert len(bar_one) == 1
    assert bar_one[0]["reason"] == "BB_RECOVERY_EXIT_TRADIER_LONG"
    assert not str(bar_one[0]["reason"]).startswith("STRUCTURAL_RANGE_SHIFT_")


def test_live_exit_chain_preserves_non_full_close_reason_and_reducer_band():
    cfg = _cfg()
    npz = _npz()
    n = len(npz["close"])
    safe = lambda data, key, size, default: np.asarray(
        data.get(key, np.full(size, default)), dtype=float
    )
    prepared = lec.prepare(npz, n, True, cfg, npz["close"], safe)
    hit = lec.step(
        prepared,
        cfg,
        1,
        100.9,
        {"gain": 0.8, "peak": 0.8, "age_min": 15.0, "entry_price": 100.1},
    )
    assert hit == ("STRUCTURAL_RANGE_SHIFT_LONG_CASCADE_dc_4h", False)
    assert lec.reduce_fraction(cfg, 0.8) == 0.25
