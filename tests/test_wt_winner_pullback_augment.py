import numpy as np
import pytest

import v12_quick_engine as v12
from vec_decisions.wt_winner_pullback_augment import final_gate, producer


@pytest.mark.parametrize(
    "is_long,aligned_wt,opposed_wt",
    [(True, (2.0, 1.0), (0.0, 1.0)), (False, (-2.0, -1.0), (0.0, -1.0))],
)
def test_winner_producer_and_native_final_gate_predicates(is_long, aligned_wt, opposed_wt):
    w1, w2 = aligned_wt
    fire, family = producer(is_long, 3.0, 3.0, 3.0, w1, w2, w1, w2, 0.0, 0.0)
    assert fire and family == "WINNER_WT_AUGMENT"
    assert final_gate(is_long, 3.0, 3.0, w1, w2, w1, w2)
    o1, o2 = opposed_wt
    assert not producer(is_long, 3.0, 3.0, 3.0, o1, o2, w1, w2, 0.0, 0.0)[0]


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("ltf_enabled", [False, True])
def test_simulate_one_books_hard_wt_augment_only_when_selected_ltf_and_15m_align(is_long, ltf_enabled):
    n = 5
    prices = np.full(n, 104.0 if is_long else 96.0)
    prices[0] = 100.0
    data = {
        "timestamps": np.arange(n, dtype=float) * 900.0 + 1700000000.0,
        "close": prices.copy(),
        "close_15m": prices.copy(),
        "open_15m": prices.copy(),
        "high_15m": prices.copy(),
        "low_15m": prices.copy(),
        "wt1_3m": np.full(n, 0.0 if is_long else 0.0),
        "wt2_3m": np.full(n, 1.0 if is_long else -1.0),
        "wt1_15m": np.full(n, 0.0 if is_long else 0.0),
        "wt2_15m": np.full(n, 1.0 if is_long else -1.0),
        "wt1_1h": np.full(n, 1.0 if is_long else -1.0),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.full(n, 1.0 if is_long else -1.0),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.full(n, 1.0 if is_long else -1.0),
        "wt2_D": np.zeros(n),
        "wt_trough_structure_15m": np.zeros(n),
        "wt_peak_structure_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 80.0),
        "dc_high_15m": np.full(n, 120.0),
        "dc_low_4h": np.full(n, 70.0),
        "dc_high_4h": np.full(n, 130.0),
    }
    cfg = v12.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG" if is_long else "CRYPTO_SHORT")
    for key, value in {
        "MODE": "crypto",
        "BASE_TF": "15m",
        "START_POSITION_SIZE": 500.0,
        "COOLDOWN_BARS": 0,
        "MIN_HOLD_BARS": 0,
        "MIN_GAIN": 3.0,
        "USE_1M_3M_SIGNALS_ENABLED": ltf_enabled,
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
        "HAIKU_WINNER_AUGMENT_ENABLED": False,
    }.items():
        setattr(cfg, key, value)
    cfg._PARITY_PREFIX_EVAL = True
    data["wt1_3m"][1] = 2.0 if is_long else -2.0
    data["wt2_3m"][1] = 1.0 if is_long else -1.0
    data["wt1_15m"][1] = 2.0 if is_long else -2.0
    data["wt2_15m"][1] = 1.0 if is_long else -1.0
    result = v12.simulate_one(data, "WT_HARD_AUG_TEST", is_long, cfg, force_initial_seed=True)
    augments = [row for row in result["ledger"] if row.get("type") == "AUGMENT"]
    assert augments
    assert augments[0]["bar"] == 1
    assert "WINNER_WT_AUGMENT" in augments[0]["reason"]
    assert len(augments) == 1


@pytest.mark.parametrize("is_long", [True, False])
def test_simulate_one_blocks_when_selected_ltf_is_opposed(is_long):
    n = 4
    prices = np.full(n, 104.0 if is_long else 96.0)
    prices[0] = 100.0
    data = {
        "timestamps": np.arange(n, dtype=float) * 900.0 + 1700000000.0,
        "close": prices.copy(),
        "close_15m": prices.copy(),
        "open_15m": prices.copy(),
        "high_15m": prices.copy(),
        "low_15m": prices.copy(),
        "wt1_3m": np.full(n, 0.0 if is_long else 0.0),
        "wt2_3m": np.full(n, 1.0 if is_long else -1.0),
        "wt1_15m": np.full(n, 2.0 if is_long else -2.0),
        "wt2_15m": np.full(n, 1.0 if is_long else -1.0),
        "dc_low_15m": np.full(n, 80.0),
        "dc_high_15m": np.full(n, 120.0),
        "dc_low_4h": np.full(n, 70.0),
        "dc_high_4h": np.full(n, 130.0),
    }
    cfg = v12.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG" if is_long else "CRYPTO_SHORT")
    for key, value in {
        "MODE": "crypto", "BASE_TF": "15m", "START_POSITION_SIZE": 500.0,
        "COOLDOWN_BARS": 0, "MIN_HOLD_BARS": 0, "MIN_GAIN": 3.0,
        "USE_1M_3M_SIGNALS_ENABLED": True, "STRUCTURAL_RANGE_SHIFT_EXIT": False,
        "HTF_DIRECTION_GATE_ENABLED": False, "HTF_GATE_APPLY_TO_AUGMENT": False,
        "LIVE_EXIT_CHAIN_ENABLED": False, "STOP_LOSS_ENABLED": False,
        "NOLOSS_ENABLED": False, "PROFIT_TARGET_ENABLED": False,
        "PEAK_GIVEBACK_PROTECTION_ENABLED": False, "AUGMENTED_DC_BREAK_ENABLED": False,
        "BOUNCE_AUGMENT_ENABLED": False, "PYRAMID_ENABLED": False,
        "WT_CROSS_EXIT_ENABLED": False, "ULTIMATE_DC_HARD_STOP_ENABLED": False,
        "PPL_ENABLED": False, "PARTIAL_PROFIT_LOCK_ENABLED": False,
        "CYCLE_TP_TIERED_ENABLED": False, "QUICK_CYCLE_TP_ENABLED": False,
        "HAIKU_WINNER_AUGMENT_ENABLED": False,
    }.items():
        setattr(cfg, key, value)
    cfg._PARITY_PREFIX_EVAL = True
    result = v12.simulate_one(data, "WT_HARD_AUG_BLOCK_TEST", is_long, cfg, force_initial_seed=True)
    assert not any(row.get("type") == "AUGMENT" for row in result["ledger"])
