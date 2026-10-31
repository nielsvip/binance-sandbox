from types import SimpleNamespace
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "tests")

import v12_quick_engine as V
from test_stocks_symgate_reentry_vec import _synth_npz
from vec_decisions.tradier_missing_rule_a_retest import (
    rule_a_retest_live,
    rule_a_retest_mask,
)


def _safe(npz, key, n, default=0.0):
    value = npz.get(key)
    if value is None or len(value) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(value, dtype=float)


def _fixture(is_long):
    if is_long:
        return {
            "close": np.array([101.0, 101.0, 101.0]),
            "wt1_D": np.array([2.0, 2.0, 2.0]),
            "wt2_D": np.array([1.0, 1.0, 1.0]),
            "wt1_W": np.array([2.0, 2.0, 2.0]),
            "wt2_W": np.array([1.0, 1.0, 1.0]),
            "dc_basis_D": np.array([100.0, 100.0, 100.0]),
            "atr_D": np.array([5.0, 5.0, 5.0]),
            "wt1_15m": np.array([2.0, 2.0, 2.0]),
            "wt2_15m": np.array([1.0, 1.0, 1.0]),
            "wt1_1h": np.array([2.0, 2.0, 2.0]),
            "wt2_1h": np.array([1.0, 1.0, 1.0]),
            "stoch_k_15m": np.array([20.0, 40.0, 30.0]),
            "stoch_d_15m": np.array([10.0, 35.0, 32.0]),
        }
    return {
        "close": np.array([99.0, 99.0, 99.0]),
        "wt1_D": np.array([1.0, 1.0, 1.0]),
        "wt2_D": np.array([2.0, 2.0, 2.0]),
        "wt1_W": np.array([1.0, 1.0, 1.0]),
        "wt2_W": np.array([2.0, 2.0, 2.0]),
        "dc_basis_D": np.array([100.0, 100.0, 100.0]),
        "atr_D": np.array([5.0, 5.0, 5.0]),
        "wt1_15m": np.array([1.0, 1.0, 1.0]),
        "wt2_15m": np.array([2.0, 2.0, 2.0]),
        "wt1_1h": np.array([1.0, 1.0, 1.0]),
        "wt2_1h": np.array([2.0, 2.0, 2.0]),
        "stoch_k_15m": np.array([80.0, 60.0, 70.0]),
        "stoch_d_15m": np.array([90.0, 65.0, 68.0]),
    }


def test_disabled_master_is_zero():
    cfg = SimpleNamespace(BREAKOUT_RETEST_ARMED_ENABLED=False)
    result = rule_a_retest_mask(_fixture(True), 3, True, cfg, _safe)
    assert result.tolist() == [False, False, False]


def test_long_and_short_fire_on_15m_stochastic_turn_only():
    cfg = SimpleNamespace(
        BREAKOUT_RETEST_ARMED_ENABLED=True,
        BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT=0.30,
    )
    assert rule_a_retest_mask(_fixture(True), 3, True, cfg, _safe).tolist() == [False, True, False]
    assert rule_a_retest_mask(_fixture(False), 3, False, cfg, _safe).tolist() == [False, True, False]


def test_mask_prefers_real_previous_stochastic_array():
    cfg = SimpleNamespace(
        BREAKOUT_RETEST_ARMED_ENABLED=True,
        BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT=0.30,
    )
    data = _fixture(True)
    data["stoch_k_15m_prev"] = np.array([20.0, 40.0, 20.0])
    assert rule_a_retest_mask(data, 3, True, cfg, _safe).tolist() == [False, False, False]


def test_data_and_strict_retest_band_fail_closed():
    cfg = SimpleNamespace(
        BREAKOUT_RETEST_ARMED_ENABLED=True,
        BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT=0.20,
    )
    data = _fixture(True)
    assert rule_a_retest_mask(data, 3, True, cfg, _safe).tolist() == [False, False, False]
    data["atr_D"] = np.zeros(3)
    assert rule_a_retest_mask(data, 3, True, cfg, _safe).tolist() == [False, False, False]


def test_live_adapter_uses_same_shared_core_on_15m_fields():
    cfg = SimpleNamespace(
        BREAKOUT_RETEST_ARMED_ENABLED=True,
        BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT=0.30,
    )
    npz = _fixture(True)
    indicators = {key: value[1] for key, value in npz.items()}
    indicators["stoch_k_15m_prev"] = npz["stoch_k_15m"][0]
    fire, reason = rule_a_retest_live(cfg, indicators, npz["close"][1], True)
    assert fire is True
    assert reason.startswith("RULE_A_RETEST_LONG_")


def test_live_adapter_preserves_a_valid_zero_stochastic_value():
    cfg = SimpleNamespace(
        BREAKOUT_RETEST_ARMED_ENABLED=True,
        BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT=0.30,
    )
    indicators = {
        "wt1_D": 1.0,
        "wt2_D": 2.0,
        "wt1_W": 1.0,
        "wt2_W": 2.0,
        "dc_basis_D": 100.0,
        "atr_D": 5.0,
        "wt1_15m": 1.0,
        "wt2_15m": 2.0,
        "wt1_1h": 1.0,
        "wt2_1h": 2.0,
        "stoch_k_15m_prev": 80.0,
        "stoch_k_15m": 0.0,
        "stoch_d_15m": 5.0,
    }
    fire, reason = rule_a_retest_live(cfg, indicators, 99.0, False)
    assert fire is True
    assert reason.startswith("RULE_A_RETEST_SHORT_")


def test_rule_a_retest_switch_changes_simulate_one_open_ledger():
    npz = _synth_npz()
    n = len(npz["close"])
    fire_bar = 201
    npz["wt1_W"] = np.full(n, 5.0)
    npz["wt2_W"] = np.full(n, -5.0)
    npz["dc_basis_D"] = np.asarray(npz["close"], dtype=float) - 1.0
    npz["atr_D"] = np.full(n, 5.0)
    npz["stoch_k_15m"] = np.full(n, 55.0)
    npz["stoch_d_15m"] = np.full(n, 50.0)
    npz["stoch_k_15m"][fire_bar - 1] = 20.0
    npz["stoch_k_15m"][fire_bar] = 40.0
    npz["stoch_d_15m"][fire_bar] = 35.0

    def run(enabled):
        cfg = V.QuickConfig()
        cfg.apply_tradier_defaults()
        cfg.MODE = "tradier"
        cfg.STOCKS_LIVE_ENTRY_STACK_ENABLED = False
        cfg.BREAKOUT_RETEST_ARMED_ENABLED = enabled
        cfg.ABLATION_DISABLE_ENTRY_TECHNICAL = True
        cfg.ABLATION_DISABLE_ENTRY_REVERSAL = True
        cfg.ABLATION_DISABLE_QUICK_ENTRY = True
        cfg.ABLATION_DISABLE_ENTRY_LEADERBOARD = True
        cfg.ABLATION_DISABLE_ENTRY_RANKING = True
        return V.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=False)

    off = run(False)
    on = run(True)
    off_opens = [
        event
        for event in off.get("ledger", [])
        if event.get("type") == "OPEN" and event.get("reason", "").startswith("RULE_A_RETEST")
    ]
    on_opens = [
        event
        for event in on.get("ledger", [])
        if event.get("type") == "OPEN" and event.get("reason", "").startswith("RULE_A_RETEST")
    ]
    assert off_opens == []
    assert len(on_opens) == 1
    assert on_opens[0]["bar"] == fire_bar
    assert on_opens[0]["reason"] == "RULE_A_RETEST_LONG"
