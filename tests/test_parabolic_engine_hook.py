"""Actual simulate_one PARABOLIC_EXIT ledger reachability in the paired 15m lane."""

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.parabolic_delta import TF_FIELDS, WT_DELTA_FIELDS
from vec_decisions.parabolic_delta import DC_DELTA_FIELDS, DC_POSITION_FIELDS, WT_STATE_FIELDS


def _npz(is_long, n=110):
    close = np.full(n, 100.0)
    close[12:] = 121.0 if is_long else 79.0
    timestamps = np.arange(1_700_000_000, 1_700_000_000 + n * 900, 900)
    arrays = {
        "close": close, "close_15m": close, "timestamps": timestamps, "timestamp_15m": timestamps,
        "k_15m": np.full(n, 50.0), "k_15m_prev": np.full(n, 50.0),
        "dc_high4_15m": np.full(n, 120.0), "dc_low4_15m": np.full(n, 80.0),
        "low_15m": np.full(n, 99.0), "low_15m_prev": np.full(n, 100.0),
        "high_15m": np.full(n, 101.0), "high_15m_prev": np.full(n, 100.0),
        "dc_low_4h": np.full(n, 50.0), "dc_high_4h": np.full(n, 150.0),
        "dc_low_15m": np.full(n, 50.0), "dc_high_15m": np.full(n, 150.0),
        "open_3m": close, "high_3m": close, "low_3m": close,
        "bb_upper_1h": np.full(n, 150.0), "bb_lower_1h": np.full(n, 50.0),
        "stoch_k_1h": np.full(n, 50.0),
    }
    arrays["k_15m"][12] = 95.0 if is_long else 5.0
    if is_long:
        arrays["low_15m"][12] = 98.0
        arrays["low_15m_prev"][12] = 100.0
    else:
        arrays["high_15m"][12] = 102.0
        arrays["high_15m_prev"][12] = 10.0
    for tf_index, tf in enumerate(("15m", "1h", "4h", "D")):
        for field_index, field in enumerate(TF_FIELDS):
            current = np.full(n, 20.0 + tf_index + field_index)
            if field in WT_DELTA_FIELDS:
                direction = 1.0 if is_long else -1.0
                current = np.arange(n, dtype=float) * direction + 20.0 + tf_index + field_index
                previous = current - direction
                previous[12] = current[12] - direction * 100.0
                arrays[f"{field}_prev_{tf}"] = previous
            elif field.startswith("dc_") and field in ("dc_basis", "dc_high", "dc_low"):
                arrays[f"{field}_{tf}_prev"] = current.copy()
            arrays[f"{field}_{tf}"] = current
    for tf in ("15m", "1h", "4h", "D"):
        arrays[f"wt1_{tf}"] = np.zeros(n)
        arrays[f"wt2_{tf}"] = np.zeros(n)
    arrays["dc_low_15m"] = np.full(n, 50.0)
    arrays["dc_high_15m"] = np.full(n, 150.0)
    arrays["dc_low_4h"] = np.full(n, 50.0)
    arrays["dc_high_4h"] = np.full(n, 150.0)
    return arrays


def _run(is_long, enabled):
    import v12_quick_engine as vq

    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "15m"
    cfg.START_POSITION_SIZE = 500.0
    cfg.COOLDOWN_BARS = 1000
    cfg.COMMISSION_BUFFER_PCT = 0.08
    cfg.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.04
    cfg.STRUCTURAL_EXIT_GATE_ENABLED = False
    for key in vars(cfg):
        if key.endswith("_ENABLED") and any(token in key for token in ("EXIT", "TP", "STOP", "PROFIT", "SATOSHIT")):
            setattr(cfg, key, False)
    cfg.LIVE_EXIT_CHAIN_ENABLED = True
    cfg.PARABOLIC_EXIT_ENABLED = enabled
    cfg.LIVE_5m_trading_ENABLED = False
    cfg.AUGMENTED_DC_BREAK_ENABLED = False
    cfg.VEC_EXIT_SIG_IS_TRIGGER = False
    cfg.ABLATION_DISABLE_REENTRY = True
    cfg._DECISION_START_TS = 0.0
    return vq.simulate_one(_npz(is_long), "PARABOLIC_TEST", is_long, cfg, force_initial_seed=True)


@pytest.mark.parametrize("is_long", [True, False])
def test_actual_simulate_one_books_parabolic_when_enabled_and_not_when_disabled(is_long):
    import v12_quick_engine as vq

    engine_source = Path(vq.__file__).read_text()
    assert engine_source.index("_parabolic_exit_state = {}") < engine_source.index("_bar_pos_snapshot = None")
    assert '"parabolic_state": _parabolic_exit_state.setdefault((str(sym).upper(), bool(is_long)), {})' in engine_source
    enabled = _run(is_long, True)
    disabled = _run(is_long, False)
    expected = f"PARABOLIC_EXIT_{'LONG' if is_long else 'SHORT'}"
    enabled_rows = [row for row in enabled["ledger"] if expected in str(row.get("reason", ""))]
    disabled_rows = [row for row in disabled["ledger"] if "PARABOLIC_EXIT" in str(row.get("reason", ""))]
    assert enabled_rows
    assert not disabled_rows


def test_ast_extracted_native_parabolic_branch_caches_first_account_and_keeps_state_after_refusal():
    import ez_positions_quick as epq
    from wt_dc_delta import DEFAULT_CFG

    source = Path(epq.__file__).read_text()
    tree = ast.parse(source)
    candidates = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "PARABOLIC_EXIT_ENABLED" in ast.unparse(node)
        and "not hard_exit_reason" in ast.unparse(node)
    ]
    assert candidates
    branch = min(candidates, key=lambda node: (node.end_lineno - node.lineno, node.lineno))
    args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg=name) for name in (
            "position_key", "symbol", "position_side", "is_long", "is_hedge",
            "_in_grace_period", "indicators", "current_price", "data_manager", "config", "logger",
        )],
        kwonlyargs=[], kw_defaults=[], defaults=[], vararg=None, kwarg=None,
    )
    wrapper = ast.FunctionDef(
        name="run_native_parabolic",
        args=args,
        body=[
            ast.Assign(targets=[ast.Name(id="hard_exit_reason", ctx=ast.Store())], value=ast.Constant(value=None)),
            copy.deepcopy(branch),
            ast.Return(value=ast.Name(id="hard_exit_reason", ctx=ast.Load())),
        ],
        decorator_list=[],
    )
    namespace = {
        "safe_fetch_float": epq.safe_fetch_float,
        "_sw_get": lambda symbol, side, key, default: default,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), "epq_parabolic_ast", "exec"), namespace)

    class Log:
        def critical(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

    state_map = {}
    manager = SimpleNamespace(parabolic_exit_states=state_map, delta_tracker=None)
    config = SimpleNamespace(LIVE_5m_trading_ENABLED=False, DELTA_TF_WEIGHTS=dict(DEFAULT_CFG["tf_weights"]))
    run = namespace["run_native_parabolic"]

    def indicators(index, extreme=False, jump=0.0):
        row = {"timestamp_15m": 1_800_000_000 + index * 900}
        for tf_index, tf in enumerate(("15m", "1h", "4h", "D")):
            base = 10.0 + 3.0 * tf_index
            for field_index, field in enumerate(TF_FIELDS):
                value = base + index * (0.1 + field_index * 0.01)
                row[f"{field}_{tf}"] = value
                if field in DC_DELTA_FIELDS:
                    row[f"{field}_{tf}_prev"] = value - (0.1 + field_index * 0.01)
            for field_index, field in enumerate(WT_DELTA_FIELDS):
                row[f"{field}_prev_{tf}"] = row[f"{field}_{tf}"] - (0.1 + field_index * 0.03)
        row.update({
            "k_15m": 95.0 if extreme else 50.0,
            "dc_high4_15m": 100.0,
                "dc_low4_15m": 50.0,
            "low_15m": 9.0 if extreme else 10.0,
            "low_15m_prev": 10.0,
            "high_15m": 11.0,
            "high_15m_prev": 10.0,
        })
        for field in WT_DELTA_FIELDS:
            row[f"{field}_15m"] = row[f"{field}_15m"] + jump
        return row

    common = {"symbol": "TESTUSDC", "position_side": "LONG", "is_long": True,
              "is_hedge": False, "_in_grace_period": False, "current_price": 101.0,
              "data_manager": manager, "config": config, "logger": Log()}
    assert run(position_key="acct-a:TESTUSDC_LONG", indicators=indicators(0), **common) is None
    state = state_map[("TESTUSDC", True)]
    prior = copy.deepcopy(state)
    reason = run(position_key="acct-a:TESTUSDC_LONG", indicators=indicators(1, True, 100.0), **common)
    assert reason and reason.startswith("PARABOLIC_EXIT_LONG")
    state_after_fire = copy.deepcopy(state)
    # A second account sees the cached first same-bar update and does not roll back or fork it.
    same_bar = run(position_key="acct-b:TESTUSDC_LONG", indicators=indicators(1, True, 100.0), **common)
    assert same_bar == reason
    assert state == state_after_fire
    # The caller can refuse execution; no position snapshot owns this state, so the next bar advances it.
    next_bar = indicators(2, False, 0.0)
    run(position_key="acct-a:TESTUSDC_LONG", indicators=next_bar, **common)
    assert state["last_bar_timestamp"] == next_bar["timestamp_15m"]
    assert prior["last_bar_timestamp"] < state["last_bar_timestamp"]
