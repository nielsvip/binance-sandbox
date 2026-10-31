import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "tests")

import v12_quick_engine as V
from test_stocks_symgate_reentry_vec import _synth_npz


def _base_npz():
    npz = _synth_npz()
    n = len(npz["close"])
    npz["close"] = np.full(n, 100.0)
    if "close_15m" in npz:
        npz["close_15m"] = np.full(n, 100.0)
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"wt1_{tf}"] = np.zeros(n)
        npz[f"wt2_{tf}"] = np.zeros(n)
    npz["stoch_k_15m"] = np.full(n, 50.0)
    npz["dc_low_15m"] = np.zeros(n)
    npz["dc_low_15m_prev"] = np.zeros(n)
    npz["dc_high_15m"] = np.zeros(n)
    npz["dc_high_15m_prev"] = np.zeros(n)
    npz["low_15m"] = np.full(n, 100.0)
    npz["low_15m_prev"] = np.full(n, 100.0)
    npz["high_15m"] = np.full(n, 100.0)
    npz["high_15m_prev"] = np.full(n, 100.0)
    return npz


def _cfg():
    cfg = V.QuickConfig()
    cfg.apply_tradier_defaults()
    cfg.MODE = "tradier"
    cfg.STOCKS_LIVE_ENTRY_STACK_ENABLED = False
    cfg.STRUCTURAL_EXIT_GATE_ENABLED = False
    cfg.VEC_EXIT_SIG_IS_TRIGGER = True
    cfg.SATOSHIT_EXIT_PARTIAL_PCT = 0.0
    for name in (
        "ABLATION_DISABLE_ENTRY_TECHNICAL",
        "ABLATION_DISABLE_ENTRY_REVERSAL",
        "ABLATION_DISABLE_QUICK_ENTRY",
        "ABLATION_DISABLE_ENTRY_LEADERBOARD",
        "ABLATION_DISABLE_ENTRY_RANKING",
    ):
        setattr(cfg, name, True)
    for name in (
        "PROFIT_TARGET_ENABLED",
        "STOP_LOSS_ENABLED",
        "WT_DC_EXIT_ENABLED",
        "STOCKS_WTDC_SCORER_EXIT_ENABLED",
        "MULTI_TF_EXIT_ENABLED_TRADIER",
        "MULTI_TF_EXIT_ENABLED",
        "PARABOLIC_PROTECTION_ENABLED",
        "WT_DIV_EXIT_ENABLED",
        "WT_ACCEL_EXIT_ENABLED",
        "WT_MOMENTUM_EXIT_ENABLED",
        "STDEV_BB_RZ_EXIT_ENABLED",
        "STDEV_REJECT_EXIT_ENABLED",
        "WT_15M_LH_WAIT_EXIT_ENABLED",
        "WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED",
        "EXIT_K5M_BOUNCE_ENABLED",
        "RULE_B_5M_EXIT_ENABLED",
        "WT_CROSSUNDER_FINAL_ENABLED",
    ):
        setattr(cfg, name, False)
    cfg.NOLOSS_MIN_PROFIT_PCT_TRADIER = 3.0
    object.__setattr__(cfg, "_template_layer", "STOCKS_LONG")
    return cfg


def _run(npz, switch, enabled):
    cfg = _cfg()
    setattr(cfg, switch, enabled)
    return V.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=True)


def _run_after_augment_reduce(npz, switch, enabled):
    cfg = _cfg()
    cfg.AUGMENT_AT_LOSS_ENABLED = True
    setattr(cfg, switch, enabled)
    return V.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=True)


def _reason_events(result, prefix):
    return [
        row
        for row in result.get("ledger", [])
        if row.get("type") == "CLOSE" and str(row.get("reason", "")).startswith(prefix)
    ]


def test_wt_crossunder_switch_changes_simulate_one_close_ledger():
    npz = _base_npz()
    fire_bar = 201
    npz["close"][fire_bar] = 104.0
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 104.0
    npz["wt1_15m"][fire_bar] = -1.0
    npz["wt1_1h"][fire_bar] = -1.0
    off = _run(npz, "WT_CROSSUNDER_FINAL_ENABLED", False)
    on = _run(npz, "WT_CROSSUNDER_FINAL_ENABLED", True)
    assert _reason_events(off, "WT_CROSSUNDER_FINAL") == []
    fires = _reason_events(on, "WT_CROSSUNDER_FINAL")
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "WT_CROSSUNDER_FINAL_5m_15m_1hTrue_4hFalse_DFalse_g4.00%_hold1005m_MANDATORY_REENTRY"


def test_rule_b_switch_changes_simulate_one_close_ledger():
    npz = _base_npz()
    fire_bar = 201
    npz["close"][fire_bar] = 104.0
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 104.0
    npz["low_15m"][fire_bar] = 103.5
    npz["low_15m_prev"][fire_bar] = 104.0
    npz["high_15m"][fire_bar] = 104.5
    npz["high_15m_prev"][fire_bar] = 105.0
    off = _run(npz, "RULE_B_5M_EXIT_ENABLED", False)
    on = _run(npz, "RULE_B_5M_EXIT_ENABLED", True)
    assert _reason_events(off, "RULE_B_5M_") == []
    fires = _reason_events(on, "RULE_B_5M_")
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "RULE_B_5M_LL_LH_gain:4.00%"


def test_k5m_bounce_switch_changes_simulate_one_close_ledger():
    npz = _base_npz()
    fire_bar = 201
    npz["close"][fire_bar] = 104.0
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 104.0
    npz["stoch_k_15m"][fire_bar] = 20.0
    npz["dc_low_15m_prev"][fire_bar] = 105.0
    off = _run(npz, "EXIT_K5M_BOUNCE_ENABLED", False)
    on = _run(npz, "EXIT_K5M_BOUNCE_ENABLED", True)
    assert _reason_events(off, "K5M_REAL_DROP_DC") == []
    fires = _reason_events(on, "K5M_REAL_DROP_DC")
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "K5M_REAL_DROP_DC_k5:20.0<dc5:105.00_gain:4.00%"


def test_rule_b_gain_uses_open_cost_basis_after_augment_and_partial_reduce():
    npz = _base_npz()
    npz["low"] = np.full(len(npz["close"]), 100.0)
    augment_bar = 201
    fire_bar = 202
    npz["close"][augment_bar] = 99.8
    npz["low"][augment_bar] = 99.8
    if "close_15m" in npz:
        npz["close_15m"][augment_bar] = 99.8
    npz["low_15m_prev"][augment_bar - 1] = 98.0
    npz["low_15m_prev"][augment_bar] = 99.0
    npz["dc_low_15m_prev"][augment_bar - 1] = 94.0
    npz["dc_low_15m_prev"][augment_bar] = 95.0
    npz["close"][fire_bar] = 103.5
    npz["low"][fire_bar] = 103.25
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 103.5
    npz["low_15m"][fire_bar] = 103.25
    npz["low_15m_prev"][fire_bar] = 104.0
    npz["high_15m"][fire_bar] = 103.75
    npz["high_15m_prev"][fire_bar] = 104.5
    off = _run_after_augment_reduce(npz, "RULE_B_5M_EXIT_ENABLED", False)
    on = _run_after_augment_reduce(npz, "RULE_B_5M_EXIT_ENABLED", True)
    off_ledger = off.get("ledger", [])
    on_ledger = on.get("ledger", [])
    augments = [row for row in on_ledger if row.get("type") == "AUGMENT"]
    reduces = [row for row in on_ledger if row.get("type") == "REDUCE"]
    assert any(row.get("bar") == augment_bar for row in augments)
    assert any(row.get("bar") == fire_bar for row in reduces)
    assert _reason_events(off, "RULE_B_5M_") == []
    fires = _reason_events(on, "RULE_B_5M_")
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "RULE_B_5M_LL_LH_gain:3.60%"


def test_k5m_gain_uses_open_cost_basis_after_augment_and_partial_reduce():
    npz = _base_npz()
    npz["low"] = np.full(len(npz["close"]), 100.0)
    augment_bar = 201
    fire_bar = 202
    npz["close"][augment_bar] = 99.8
    npz["low"][augment_bar] = 99.8
    if "close_15m" in npz:
        npz["close_15m"][augment_bar] = 99.8
    npz["low_15m_prev"][augment_bar - 1] = 98.0
    npz["low_15m_prev"][augment_bar] = 99.0
    npz["dc_low_15m_prev"][augment_bar - 1] = 94.0
    npz["dc_low_15m_prev"][augment_bar] = 95.0
    npz["close"][fire_bar] = 103.5
    npz["low"][fire_bar] = 103.25
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 103.5
    npz["stoch_k_15m"][fire_bar] = 20.0
    npz["dc_low_15m_prev"][fire_bar] = 104.5
    off = _run_after_augment_reduce(npz, "EXIT_K5M_BOUNCE_ENABLED", False)
    on = _run_after_augment_reduce(npz, "EXIT_K5M_BOUNCE_ENABLED", True)
    on_ledger = on.get("ledger", [])
    augments = [row for row in on_ledger if row.get("type") == "AUGMENT"]
    reduces = [row for row in on_ledger if row.get("type") == "REDUCE"]
    assert any(row.get("bar") == augment_bar for row in augments)
    assert any(row.get("bar") == fire_bar for row in reduces)
    assert _reason_events(off, "K5M_REAL_DROP_DC") == []
    fires = _reason_events(on, "K5M_REAL_DROP_DC")
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "K5M_REAL_DROP_DC_k5:20.0<dc5:104.50_gain:3.60%"


def test_stdev_reject_stock_switch_changes_simulate_one_ledger():
    npz = _base_npz()
    fire_bar = 202
    npz["close"][fire_bar] = 103.0
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 103.0
    n = len(npz["close"])
    npz["bb_pct_b_D"] = np.full(n, 0.5)
    npz["timestamp_D"] = np.ones(n)
    npz["wt_velocity_1h"] = np.zeros(n)
    npz["bb_pct_b_D"][:200] = 0.9
    npz["bb_pct_b_D"][200:fire_bar] = 0.6
    npz["timestamp_D"][200:] = 2.0
    npz["bb_pct_b_D"][fire_bar:] = 0.6
    npz["timestamp_D"][fire_bar:] = 3.0
    npz["wt_velocity_1h"][fire_bar] = -1.0
    off = _run(npz, "STDEV_REJECT_EXIT_ENABLED", False)
    on = _run(npz, "STDEV_REJECT_EXIT_ENABLED", True)
    assert _reason_events(off, "STDEV_REJECT_EXIT_") == []
    fires = _reason_events(on, "STDEV_REJECT_EXIT_")
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "STDEV_REJECT_EXIT_D_pctb=0.600_g=3.00%"
