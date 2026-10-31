import sys

import numpy as np
import pytest

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



def test_active_stdev_reject_reports_old_npz_as_unsupported():
    npz = _base_npz()
    cfg = _cfg()
    cfg.STDEV_REJECT_EXIT_ENABLED = True
    with pytest.raises(ValueError, match="STDEV_REJECT_EXIT_ENABLED unsupported"):
        V.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=True)

def test_stdev_reject_stock_switch_changes_simulate_one_ledger():
    npz = _base_npz()
    n = len(npz["close"])
    ts = np.asarray(npz["timestamps"], dtype=np.int64)
    fire_target = int(np.datetime64("2026-10-19T19:45:00").astype("datetime64[s]").astype(np.int64))
    fire_bar = int(np.searchsorted(ts, fire_target))
    assert fire_bar < n and ts[fire_bar] == fire_target
    npz["close"][fire_bar] = 103.0
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 103.0
    npz["bb_pct_b_D"] = np.full(n, 0.5)
    npz["_completed_bb_pct_b_D"] = np.full(n, 0.9)
    npz["_completed_bb_pct_b_D_prev"] = np.full(n, 0.9)
    npz["wt_velocity_1h"] = np.zeros(n)
    monday_open = int(np.searchsorted(ts, int(np.datetime64("2026-10-19T13:30:00").astype("datetime64[s]").astype(np.int64))))
    npz["_completed_bb_pct_b_D"][monday_open:] = 0.6
    npz["wt_velocity_1h"][fire_bar] = -1.0
    off = _run(npz, "STDEV_REJECT_EXIT_ENABLED", False)
    on = _run(npz, "STDEV_REJECT_EXIT_ENABLED", True)
    assert _reason_events(off, "STDEV_REJECT_EXIT_") == []
    fires = _reason_events(on, "STDEV_REJECT_EXIT_")
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "STDEV_REJECT_EXIT_D_pctb=0.600_g=3.00%"


def test_stdev_bb_rz_exit_reaches_simulate_one_ledger_only_while_scorer_holds():
    npz = _base_npz()
    fire_bar = 201
    npz["bb_pct_b_D"] = np.full(len(npz["close"]), 0.8)
    npz["bb_pct_b_D_prev"] = np.full(len(npz["close"]), 0.8)
    npz["bb_pct_b_D"][fire_bar] = 0.9
    npz["bb_pct_b_D_prev"][fire_bar] = 1.1
    npz["close"][fire_bar] = 104.0
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 104.0
    npz["wt_velocity_1h"] = np.zeros(len(npz["close"]))
    npz["wt_velocity_1h"][fire_bar] = -1.0
    npz["age_5m"] = np.zeros(len(npz["close"]))

    off_cfg = _cfg()
    off_cfg.STDEV_BB_RZ_EXIT_ENABLED = False
    off_cfg.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    off_cfg.WT_DC_EXIT_ENABLED = True
    off = V.simulate_one(npz, "SYNTH", True, off_cfg, force_initial_seed=True)
    assert _reason_events(off, "STDEV_BB_RZ_EXIT") == []

    on_cfg = _cfg()
    on_cfg.STDEV_BB_RZ_EXIT_ENABLED = True
    on_cfg.STDEV_BB_RZ_EXIT_TF = "D"
    on_cfg.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    on_cfg.WT_DC_EXIT_ENABLED = True
    on_cfg.WT_DC_EXIT_THRESHOLD = 30.0
    on = V.simulate_one(npz, "SYNTH", True, on_cfg, force_initial_seed=True)
    fires = _reason_events(on, "STDEV_BB_RZ_EXIT")
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "STDEV_BB_RZ_EXIT_D_pctb=0.900_g=4.00%"

    scorer_exit_cfg = _cfg()
    scorer_exit_cfg.STDEV_BB_RZ_EXIT_ENABLED = True
    scorer_exit_cfg.STDEV_BB_RZ_EXIT_TF = "D"
    scorer_exit_cfg.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    scorer_exit_cfg.WT_DC_EXIT_ENABLED = True
    scorer_exit_cfg.WT_DC_EXIT_THRESHOLD = -1.0
    scorer_exit = V.simulate_one(
        npz, "SYNTH", True, scorer_exit_cfg, force_initial_seed=True
    )
    assert _reason_events(scorer_exit, "STDEV_BB_RZ_EXIT") == []


def test_stdev_bb_rz_exit_obeys_native_scorer_hold_stale_and_cash_gates():
    npz = _base_npz()
    fire_bar = 201
    npz["bb_pct_b_D"] = np.full(len(npz["close"]), 0.8)
    npz["bb_pct_b_D_prev"] = np.full(len(npz["close"]), 0.8)
    npz["bb_pct_b_D"][fire_bar] = 0.9
    npz["bb_pct_b_D_prev"][fire_bar] = 1.1
    npz["close"][fire_bar] = 104.0
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 104.0
    npz["wt_velocity_1h"] = np.zeros(len(npz["close"]))
    npz["wt_velocity_1h"][fire_bar] = -1.0
    npz["age_5m"] = np.zeros(len(npz["close"]))
    cfg = _cfg()
    cfg.STDEV_BB_RZ_EXIT_ENABLED = True
    cfg.STDEV_BB_RZ_EXIT_TF = "D"
    cfg.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    cfg.WT_DC_EXIT_ENABLED = True
    cfg.WT_DC_EXIT_THRESHOLD = 30.0

    score_exit = V.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=True)
    assert _reason_events(score_exit, "STDEV_BB_RZ_EXIT")

    stale = {key: value.copy() if isinstance(value, np.ndarray) else value for key, value in npz.items()}
    stale["age_5m"][fire_bar] = 601.0
    stale_result = V.simulate_one(stale, "SYNTH", True, cfg, force_initial_seed=True)
    assert _reason_events(stale_result, "STDEV_BB_RZ_EXIT") == []

    cash = _cfg()
    cash.STDEV_BB_RZ_EXIT_ENABLED = True
    cash.STDEV_BB_RZ_EXIT_TF = "D"
    cash.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    cash.WT_DC_EXIT_ENABLED = True
    cash.WT_DC_EXIT_THRESHOLD = 30.0
    cash.SIM_ACCOUNT = "tra"
    cash_result = V.simulate_one(npz, "SYNTH", True, cash, force_initial_seed=True)
    assert _reason_events(cash_result, "STDEV_BB_RZ_EXIT") == []


def test_wt_min_tfs_uses_native_master_scorer_hold_and_precedes_stdev():
    npz = _base_npz()
    fire_bar = 201
    npz["timestamps"] = 1699972200.0 + np.arange(len(npz["close"])) * 900.0
    npz["close"][fire_bar] = 104.0
    if "close_15m" in npz:
        npz["close_15m"][fire_bar] = 104.0
    for tf in ("5m", "15m", "1h", "4h", "D"):
        npz[f"wt1_{tf}"] = np.zeros(len(npz["close"]))
        npz[f"wt2_{tf}"] = np.zeros(len(npz["close"]))
        npz[f"wt1_{tf}"][fire_bar] = 0.0
        npz[f"wt2_{tf}"][fire_bar] = 1.0
    npz["bb_pct_b_D"] = np.full(len(npz["close"]), 0.5)
    npz["bb_pct_b_D_prev"] = np.full(len(npz["close"]), 0.5)
    npz["bb_pct_b_D"][fire_bar] = 0.9
    npz["bb_pct_b_D_prev"][fire_bar] = 1.1
    npz["wt_velocity_1h"] = np.full(len(npz["close"]), -1.0)
    npz["age_5m"] = np.zeros(len(npz["close"]))

    off_cfg = _cfg()
    off_cfg.WT_DC_EXIT_ENABLED = True
    off_cfg.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    off_cfg.WT_DC_EXIT_THRESHOLD = 1000.0
    off_cfg.WT_EXIT_VETO_ENABLED_TRADIER = False
    off_cfg.WT_EXIT_MIN_TFS_TRADIER = 5
    off_cfg.SIM_ACCOUNT = "margin"
    off = V.simulate_one(npz, "WT_MIN_TFS_TEST", True, off_cfg, force_initial_seed=True)
    assert _reason_events(off, "WT_EXIT_TFS_VARFIX") == []

    on_cfg = _cfg()
    on_cfg.WT_DC_EXIT_ENABLED = True
    on_cfg.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    on_cfg.WT_DC_EXIT_THRESHOLD = 1000.0
    on_cfg.WT_EXIT_VETO_ENABLED_TRADIER = True
    on_cfg.WT_EXIT_TFS_TRADIER = "5m+15m+1h+4h+D"
    on_cfg.WT_EXIT_MIN_TFS_TRADIER = 5
    on_cfg.STDEV_BB_RZ_EXIT_ENABLED = True
    on_cfg.STDEV_BB_RZ_EXIT_TF = "D"
    on_cfg.SIM_ACCOUNT = "margin"
    on = V.simulate_one(npz, "WT_MIN_TFS_TEST", True, on_cfg, force_initial_seed=True)
    fires = _reason_events(on, "WT_EXIT_TFS_VARFIX")
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "WT_EXIT_TFS_VARFIX(5/5>=5:5m+15m+1h+4h+D)_g=4.00%"
    assert _reason_events(on, "STDEV_BB_RZ_EXIT") == []

    nonhold_cfg = _cfg()
    nonhold_cfg.WT_DC_EXIT_ENABLED = True
    nonhold_cfg.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    nonhold_cfg.WT_DC_EXIT_THRESHOLD = -1.0
    nonhold_cfg.WT_EXIT_VETO_ENABLED_TRADIER = True
    nonhold_cfg.WT_EXIT_MIN_TFS_TRADIER = 5
    nonhold_cfg.SIM_ACCOUNT = "margin"
    nonhold = V.simulate_one(npz, "WT_MIN_TFS_TEST", True, nonhold_cfg, force_initial_seed=True)
    assert _reason_events(nonhold, "WT_EXIT_TFS_VARFIX") == []

    cash_cfg = _cfg()
    cash_cfg.WT_DC_EXIT_ENABLED = True
    cash_cfg.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    cash_cfg.WT_DC_EXIT_THRESHOLD = 1000.0
    cash_cfg.WT_EXIT_VETO_ENABLED_TRADIER = True
    cash_cfg.WT_EXIT_MIN_TFS_TRADIER = 5
    cash_cfg.SIM_ACCOUNT = "tra"
    cash = V.simulate_one(npz, "WT_MIN_TFS_TEST", True, cash_cfg, force_initial_seed=True)
    assert _reason_events(cash, "WT_EXIT_TFS_VARFIX") == []

    stale = {key: value.copy() if isinstance(value, np.ndarray) else value for key, value in npz.items()}
    stale["age_5m"][fire_bar] = 601.0
    stale["close"][fire_bar] = 100.2
    if "close_15m" in stale:
        stale["close_15m"][fire_bar] = 100.2
    stale_cfg = _cfg()
    stale_cfg.WT_DC_EXIT_ENABLED = True
    stale_cfg.STOCKS_WTDC_SCORER_EXIT_ENABLED = True
    stale_cfg.WT_DC_EXIT_THRESHOLD = 1000.0
    stale_cfg.WT_EXIT_VETO_ENABLED_TRADIER = True
    stale_cfg.WT_EXIT_MIN_TFS_TRADIER = 5
    stale_cfg.SIM_ACCOUNT = "margin"
    stale_result = V.simulate_one(stale, "WT_MIN_TFS_TEST", True, stale_cfg, force_initial_seed=True)
    assert _reason_events(stale_result, "WT_EXIT_TFS_VARFIX") == []


def test_native_wt_min_tfs_ast_order_is_master_then_stdev_within_scorer_hold():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("tradier_manage.py").read_text())
    evaluator = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "evaluate_stop"
    )
    hold_branch = next(
        node
        for node in ast.walk(evaluator)
        if isinstance(node, ast.If)
        and "_wtdc_exit_enabled" in ast.dump(node.test)
        and "_exit_score" in ast.dump(node.test)
        and "_exit_threshold" in ast.dump(node.test)
        and "Lt" in ast.dump(node.test)
    )
    hold_tests = [
        node
        for node in hold_branch.body
        if isinstance(node, ast.If)
    ]
    master_index = next(
        index
        for index, node in enumerate(hold_tests)
        if "WT_EXIT_VETO_ENABLED_TRADIER" in ast.dump(node.test)
    )
    stdev_index = next(
        index
        for index, node in enumerate(hold_tests)
        if "STDEV_BB_RZ_EXIT_ENABLED" in ast.dump(node.test)
    )
    assert master_index < stdev_index


def test_stock_wt_min_tfs_missing_scalars_follow_native_zero_fallback():
    from types import SimpleNamespace
    from vec_decisions.lane_vec_entryq2 import wt_exit_min_tfs_mask

    npz = {
        "wt1_5m": np.asarray([-1.0, -1.0]),
        "wt2_15m": np.asarray([1.0, 1.0]),
    }
    cfg = SimpleNamespace(
        WT_EXIT_TFS_TRADIER="5m+15m",
        WT_EXIT_MIN_TFS_TRADIER=2,
    )
    np.testing.assert_array_equal(
        wt_exit_min_tfs_mask(npz, 2, True, cfg),
        np.asarray([True, True]),
    )
    cfg.WT_EXIT_MIN_TFS_TRADIER = 0
    np.testing.assert_array_equal(
        wt_exit_min_tfs_mask(npz, 2, True, cfg),
        np.asarray([False, False]),
    )
    cfg.WT_EXIT_TFS_TRADIER = ""
    assert wt_exit_min_tfs_mask(npz, 2, True, cfg) is None
