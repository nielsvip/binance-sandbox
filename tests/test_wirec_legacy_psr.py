import numpy as np
import ast
from pathlib import Path

from vec_decisions.wirec_legacy_psr import Prepared, fires_all, fires_fulldc
from vec_decisions.asr_input_adapter import paired_npz_view


class Config:
    LEGACY_REENTRY_PSR_FULL_DC = True
    LEGACY_REENTRY_PSR_QUICK_RECOVERY = False
    LEGACY_REENTRY_PSR_K_DC_CROSSOVER = False
    LEGACY_REENTRY_PSR_DC_BOUNCE = False
    LEGACY_PROC_SINGLE_REENTRY = False
    REENTRY2_QUICK_RECOVERY_ENABLED = False
    REENTRY2_STOCH_CROSS_ENABLED = False
    USE_1M_3M_SIGNALS_ENABLED = False


def _arrays(use_ltf=False):
    return {
        "close_15m": np.array([10.0, 10.1, 10.2]),
        "dc_basis_15m": np.array([10.0, 10.0, 10.0]),
        "dc_basis_crossover_15m": np.array([0, 1, 0]),
        "dc_basis_crossunder_15m": np.array([0, 0, 0]),
        "k_15m": np.array([50.0, 51.0, 52.0]),
        "d_15m": np.array([55.0, 55.0, 55.0]),
        "k_3m": np.array([2.0, 2.0, 2.0]),
        "d_3m": np.array([3.0, 3.0, 3.0]),
        "dc_basis_crossover_3m": np.array([0, 0, 0]),
        "dc_basis_crossunder_3m": np.array([0, 0, 0]),
    }


def test_ltf_off_full_dc_uses_paired_15m_crossover_event_and_values():
    cfg = Config()
    prepared = Prepared(_arrays(), 3, True, cfg)
    assert prepared.k3 == [50.0, 51.0, 52.0]
    assert prepared.d3 == [55.0, 55.0, 55.0]
    assert prepared.dc_basis_cross_up3 == [0.0, 1.0, 0.0]
    assert fires_fulldc(prepared, 1, False, cfg) == (True, 1.0)
    assert fires_all(prepared, 1, 10.1, 9.9, 75.0, False, cfg) == (True, 1.0, False)


def test_ltf_on_uses_actual_3m_event_and_does_not_fall_back_to_15m():
    cfg = Config()
    cfg.USE_1M_3M_SIGNALS_ENABLED = True
    arrays = _arrays()
    arrays["dc_basis_crossover_3m"] = np.array([0, 1, 0])
    prepared = Prepared(arrays, 3, True, cfg)
    assert prepared.k3 == [2.0, 2.0, 2.0]
    assert fires_fulldc(prepared, 1, False, cfg) == (True, 1.0)
    del arrays["dc_basis_crossover_3m"]
    prepared_missing = Prepared(arrays, 3, True, cfg)
    assert fires_fulldc(prepared_missing, 1, False, cfg) == (False, 1.0)


def test_disabled_parent_switch_allows_only_flat_positive_exit_guarantee():
    cfg = Config()
    prepared = Prepared(_arrays(), 3, True, cfg)
    assert fires_all(prepared, 1, 10.1, 0.0, 75.0, False, cfg) == (False, 1.0, False)
    assert fires_all(prepared, 1, 10.1, 9.9, 75.0, False, cfg) == (True, 1.0, False)


def test_full_dc_matches_source_derived_native_predicate_under_paired_policy():
    tree = ast.parse(Path("ez_manage.py").read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_single_reentry_evaluation")
    assignments = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"stoch_crossover_3m", "dc_basis_crossover_3m", "full_reentry_stoch_above_dc"}:
                    assignments[target.id] = compile(ast.Expression(node.value), "<native reentry predicate>", "eval")
    assert set(assignments) == {"stoch_crossover_3m", "dc_basis_crossover_3m", "full_reentry_stoch_above_dc"}

    cfg = Config()
    prepared = Prepared(_arrays(), 3, True, cfg)
    i = 1
    paired = paired_npz_view(_arrays(), False)
    if "k_3m_prev" not in paired:
        paired["k_3m_prev"] = np.concatenate((paired["k_3m"][:1], paired["k_3m"][:-1]))
    if "d_3m_prev" not in paired:
        paired["d_3m_prev"] = np.concatenate((paired["d_3m"][:1], paired["d_3m"][:-1]))
    native_scope = {
        "is_long": True,
        "k_3m": float(paired["k_3m"][i]),
        "d_3m": float(paired["d_3m"][i]),
        "k_3m_prev": float(paired["k_3m_prev"][i]),
        "d_3m_prev": float(paired["d_3m_prev"][i]),
        "i": {key: value[i] for key, value in paired.items()},
        "current_price": float(paired["close_15m"][i]),
        "dc_basis_15m": float(paired["dc_basis_15m"][i]),
    }
    native_scope["stoch_crossover_3m"] = eval(assignments["stoch_crossover_3m"], {}, native_scope)
    native_scope["dc_basis_crossover_3m"] = eval(assignments["dc_basis_crossover_3m"], {}, native_scope)
    native_scope["full_reentry_stoch_above_dc"] = eval(assignments["full_reentry_stoch_above_dc"], {}, native_scope)
    native_fire = bool(native_scope["full_reentry_stoch_above_dc"] or native_scope["dc_basis_crossover_3m"])
    vector_fire, _ = fires_fulldc(prepared, i, False, cfg)
    assert native_fire is True
    assert vector_fire is native_fire


def test_simulate_one_books_full_dc_after_real_close_when_parent_switch_is_off():
    from tests.test_quick_srs_paired_15m import _cfg, _npz
    from v12_quick_engine import simulate_one

    data = _npz()
    n = len(data["timestamps"])
    close = np.full(n, 100.0)
    close[:3] = [100.0, 100.3, 100.2]
    for key in ("close", "close_15m", "high_15m", "low_15m", "open_15m"):
        data[key] = close.copy()
    for tf in ("15m", "1h", "4h", "D"):
        data[f"k_{tf}"] = np.full(n, 50.0)
        data[f"d_{tf}"] = np.full(n, 55.0)
        data[f"k_{tf}_prev"] = np.full(n, 50.0)
        data[f"d_{tf}_prev"] = np.full(n, 55.0)
    data["dc_basis_15m"] = np.full(n, 100.0)
    data["dc_basis_crossover_15m"] = np.zeros(n)
    data["dc_basis_crossover_15m"][2] = 1

    cfg = _cfg()
    for key, value in {
        "PROFIT_TARGET_ENABLED": True,
        "PROFIT_TARGET_PCT": 0.1,
        "REENTRY2_STOCH_CROSS_ENABLED": False,
        "LEGACY_REENTRY_PSR_FULL_DC": True,
        "LEGACY_REENTRY_PSR_QUICK_RECOVERY": False,
        "LEGACY_REENTRY_PSR_DC_BOUNCE": False,
        "LEGACY_PROC_SINGLE_REENTRY": False,
        "REENTRY_MANDATORY": False,
        "ABLATION_DISABLE_QUICK_ENTRY": True,
        "CYCLE_TP_TIERED_ENABLED": False,
        "START_POSITION_SIZE": 500.0,
        "COOLDOWN_BARS": 0,
        "USE_1M_3M_SIGNALS_ENABLED": False,
    }.items():
        setattr(cfg, key, value)

    result = simulate_one(data, "WIREC_SIM", True, cfg, force_initial_seed=True)
    orders = [row for row in result["ledger"] if row.get("type") in {"OPEN", "CLOSE", "REDUCE"}]
    assert [(row.get("bar"), row.get("type")) for row in orders[:3]] == [
        (0, "OPEN"), (None, "CLOSE"), (2, "OPEN")
    ]
    assert orders[2]["reason"] == "LEGACY_PSR_REENTRY"
