import ast
from pathlib import Path

import numpy as np
import pytest

import v12_quick_engine as v12
from vec_decisions.wt_winner_pullback_augment import final_gate, producer
from vec_decisions.aug_gain_gate import latest_reduction_price, passes as shared_gain_gate_passes


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
@pytest.mark.parametrize("switch_enabled", [False, True])
def test_simulate_one_books_hard_wt_augment_only_when_selected_ltf_and_15m_align(is_long, ltf_enabled, switch_enabled):
    n = 5
    prices = np.full(n, 105.0 if is_long else 93.0)
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
        "WT_WINNER_PULLBACK_AUGMENT_ENABLED": switch_enabled,
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
    if switch_enabled:
        assert augments
        assert augments[0]["bar"] == 1
        assert "WINNER_WT_AUGMENT" in augments[0]["reason"]
        assert len(augments) == 1
    else:
        assert not any("WINNER_WT_AUGMENT" in row.get("reason", "") or "BOUNCE_AUGMENT" in row.get("reason", "") for row in augments)


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


def test_wt_winner_augment_switch_is_crypto_only_even_if_set_true_for_tradier():
    n = 5
    prices = np.array([100.0, 96.0, 96.0, 96.0, 96.0])
    data = {
        "timestamps": np.arange(n, dtype=float) * 900.0 + 1700000000.0,
        "close": prices.copy(),
        "close_15m": prices.copy(),
        "open_15m": prices.copy(),
        "high_15m": prices.copy(),
        "low_15m": prices.copy(),
        "wt1_3m": np.array([0.0, -2.0, -2.0, -2.0, -2.0]),
        "wt2_3m": np.full(n, -1.0),
        "wt1_15m": np.array([0.0, -2.0, -2.0, -2.0, -2.0]),
        "wt2_15m": np.full(n, -1.0),
        "wt1_1h": np.full(n, -1.0),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.full(n, -1.0),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.full(n, -1.0),
        "wt2_D": np.zeros(n),
        "wt_trough_structure_15m": np.zeros(n),
        "wt_peak_structure_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 80.0),
        "dc_high_15m": np.full(n, 120.0),
        "dc_low_4h": np.full(n, 70.0),
        "dc_high_4h": np.full(n, 130.0),
    }

    def run(enabled):
        cfg = v12.QuickConfig()
        cfg.apply_tradier_defaults()
        cfg.MODE = "tradier"
        cfg.BASE_TF = "15m"
        cfg.START_POSITION_SIZE = 500.0
        cfg.COOLDOWN_BARS = 0
        cfg.MIN_HOLD_BARS = 0
        cfg.MIN_GAIN = 3.0
        cfg.USE_1M_3M_SIGNALS_ENABLED = True
        cfg.WT_WINNER_PULLBACK_AUGMENT_ENABLED = enabled
        cfg.HTF_DIRECTION_GATE_ENABLED = False
        cfg.HTF_GATE_APPLY_TO_AUGMENT = False
        cfg.STRUCTURAL_RANGE_SHIFT_EXIT = False
        cfg.LIVE_EXIT_CHAIN_ENABLED = False
        cfg.STOP_LOSS_ENABLED = False
        cfg.NOLOSS_ENABLED = False
        cfg.PROFIT_TARGET_ENABLED = False
        cfg.PEAK_GIVEBACK_PROTECTION_ENABLED = False
        cfg.AUGMENTED_DC_BREAK_ENABLED = False
        cfg.BOUNCE_AUGMENT_ENABLED = False
        cfg.PYRAMID_ENABLED = False
        cfg.WT_CROSS_EXIT_ENABLED = False
        cfg.ULTIMATE_DC_HARD_STOP_ENABLED = False
        cfg.PPL_ENABLED = False
        cfg.PARTIAL_PROFIT_LOCK_ENABLED = False
        cfg.CYCLE_TP_TIERED_ENABLED = False
        cfg.QUICK_CYCLE_TP_ENABLED = False
        cfg.HAIKU_WINNER_AUGMENT_ENABLED = False
        cfg._PARITY_PREFIX_EVAL = True
        return v12.simulate_one(data, "WT_STOCK_GUARD_TEST", False, cfg, force_initial_seed=True)

    enabled_ledger = run(True)["ledger"]
    disabled_ledger = run(False)["ledger"]
    assert enabled_ledger == disabled_ledger
    assert not any("WINNER_WT_AUGMENT" in str(row.get("reason", "")) or "BOUNCE_AUGMENT" in str(row.get("reason", "")) for row in enabled_ledger)


def test_simulate_one_books_bounce_family_after_peak_pullback():
    n = 4
    prices = np.array([100.0, 103.0, 102.2, 102.2])
    data = {
        "timestamps": np.arange(n, dtype=float) * 900.0 + 1700000000.0,
        "close": prices.copy(), "close_15m": prices.copy(),
        "open_15m": prices.copy(), "high_15m": prices.copy(), "low_15m": prices.copy(),
        "wt1_3m": np.array([0.0, 0.0, 2.0, 2.0]),
        "wt2_3m": np.array([1.0, 1.0, 1.0, 1.0]),
        "wt1_15m": np.array([0.0, 0.0, 2.0, 2.0]),
        "wt2_15m": np.array([1.0, 1.0, 1.0, 1.0]),
        "wt1_1h": np.ones(n), "wt2_1h": np.zeros(n),
        "wt1_4h": np.ones(n), "wt2_4h": np.zeros(n),
        "wt1_D": np.ones(n), "wt2_D": np.zeros(n),
        "wt_trough_structure_15m": np.zeros(n), "wt_peak_structure_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 80.0), "dc_high_15m": np.full(n, 120.0),
        "dc_low_4h": np.full(n, 70.0), "dc_high_4h": np.full(n, 130.0),
    }
    cfg = v12.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG")
    cfg.MODE = "crypto"
    cfg.BASE_TF = "15m"
    cfg.START_POSITION_SIZE = 500.0
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.MIN_GAIN = 3.0
    cfg.USE_1M_3M_SIGNALS_ENABLED = True
    cfg.WT_WINNER_PULLBACK_AUGMENT_ENABLED = True
    cfg.HTF_DIRECTION_GATE_ENABLED = False
    cfg.HTF_GATE_APPLY_TO_AUGMENT = False
    cfg.STRUCTURAL_RANGE_SHIFT_EXIT = False
    cfg.LIVE_EXIT_CHAIN_ENABLED = False
    cfg.STOP_LOSS_ENABLED = False
    cfg.NOLOSS_ENABLED = False
    cfg.PROFIT_TARGET_ENABLED = False
    cfg.PEAK_GIVEBACK_PROTECTION_ENABLED = False
    cfg.AUGMENTED_DC_BREAK_ENABLED = False
    cfg.BOUNCE_AUGMENT_ENABLED = False
    cfg.PYRAMID_ENABLED = False
    cfg.WT_CROSS_EXIT_ENABLED = False
    cfg.ULTIMATE_DC_HARD_STOP_ENABLED = False
    cfg.PPL_ENABLED = False
    cfg.PARTIAL_PROFIT_LOCK_ENABLED = False
    cfg.CYCLE_TP_TIERED_ENABLED = False
    cfg.QUICK_CYCLE_TP_ENABLED = False
    cfg.HAIKU_WINNER_AUGMENT_ENABLED = False
    cfg._PARITY_PREFIX_EVAL = True
    result = v12.simulate_one(data, "WT_BOUNCE_AUG_TEST", True, cfg, force_initial_seed=True)
    bounces = [row for row in result["ledger"] if row.get("type") == "AUGMENT" and "BOUNCE_AUGMENT" in row.get("reason", "")]
    assert not bounces
    assert producer(True, 2.2, 3.0, 3.0, 2.0, 1.0, 2.0, 1.0, 0.0, 0.0) == (True, "BOUNCE_AUGMENT")
    assert not shared_gain_gate_passes(cfg, True, 102.2, 2.2, 500.0, 0.0, 1.0, 0.0, 0.0, 0.0)


def test_augment_gate_uses_latest_partial_reduction_or_closed_trade_price():
    trades = [{"type": "CLOSE", "ts": 10, "exit_price": 105.0}]
    events = [{"type": "REDUCE", "ts": 20, "price": 110.0}]
    assert latest_reduction_price(trades, events) == 110.0
    events.append({"type": "AUGMENT", "ts": 30, "price": 120.0})
    assert latest_reduction_price(trades, events) == 110.0
    trades.append({"type": "CLOSE", "ts": 40, "exit_price": 115.0})
    assert latest_reduction_price(trades, events) == 115.0


def test_simulate_one_wt_hard_source_is_not_limited_by_ladder_cooldown():
    n = 24
    prices = np.linspace(100.0, 125.0, n)
    data = {
        "timestamps": np.arange(n, dtype=float) * 900.0 + 1700000000.0,
        "close": prices.copy(), "close_15m": prices.copy(),
        "open_15m": prices.copy(), "high_15m": prices.copy(), "low_15m": prices.copy(),
        "wt1_3m": np.full(n, 2.0), "wt2_3m": np.ones(n),
        "wt1_15m": np.full(n, 2.0), "wt2_15m": np.ones(n),
        "wt1_1h": np.ones(n), "wt2_1h": np.zeros(n),
        "wt1_4h": np.ones(n), "wt2_4h": np.zeros(n),
        "wt1_D": np.ones(n), "wt2_D": np.zeros(n),
        "wt_trough_structure_15m": np.zeros(n), "wt_peak_structure_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 80.0), "dc_high_15m": np.full(n, 120.0),
        "dc_low_4h": np.full(n, 70.0), "dc_high_4h": np.full(n, 130.0),
    }
    cfg = v12.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG")
    cfg.MODE = "crypto"
    cfg.BASE_TF = "15m"
    cfg.START_POSITION_SIZE = 500.0
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.MIN_GAIN = 3.0
    cfg.USE_1M_3M_SIGNALS_ENABLED = True
    cfg.WT_WINNER_PULLBACK_AUGMENT_ENABLED = True
    cfg.HTF_DIRECTION_GATE_ENABLED = False
    cfg.HTF_GATE_APPLY_TO_AUGMENT = False
    cfg.STRUCTURAL_RANGE_SHIFT_EXIT = False
    cfg.LIVE_EXIT_CHAIN_ENABLED = False
    cfg.STOP_LOSS_ENABLED = False
    cfg.NOLOSS_ENABLED = False
    cfg.PROFIT_TARGET_ENABLED = False
    cfg.PEAK_GIVEBACK_PROTECTION_ENABLED = False
    cfg.AUGMENTED_DC_BREAK_ENABLED = False
    cfg.BOUNCE_AUGMENT_ENABLED = False
    cfg.PYRAMID_ENABLED = False
    cfg.WT_CROSS_EXIT_ENABLED = False
    cfg.ULTIMATE_DC_HARD_STOP_ENABLED = False
    cfg.PPL_ENABLED = False
    cfg.PARTIAL_PROFIT_LOCK_ENABLED = False
    cfg.CYCLE_TP_TIERED_ENABLED = False
    cfg.QUICK_CYCLE_TP_ENABLED = False
    cfg.HAIKU_WINNER_AUGMENT_ENABLED = False
    cfg._PARITY_PREFIX_EVAL = True
    result = v12.simulate_one(data, "WT_REPEAT_AUG_TEST", True, cfg, force_initial_seed=True)
    hard_wt = [row for row in result["ledger"] if row.get("type") == "AUGMENT" and "WINNER_WT_AUGMENT" in row.get("reason", "")]
    assert len(hard_wt) > 1
    assert all(b - a < 20 for a, b in zip([row["bar"] for row in hard_wt], [row["bar"] for row in hard_wt][1:]))


def test_native_epq_source_is_switch_gated_and_uses_shared_producer():
    source = Path("ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "check_exit_candidates_for_account"
    )
    segment = ast.get_source_segment(source, function)
    assert '"WT_WINNER_PULLBACK_AUGMENT_ENABLED"' in segment
    assert "if not hard_exit_reason and _wt_aug_enabled:" in segment
    assert "vec_decisions.wt_winner_pullback_augment" in segment
    assert "_wt_aug_producer(" in segment
