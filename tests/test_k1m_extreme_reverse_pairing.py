"""K1M reverse exit uses its native source or the paired 15m lane."""

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.check_exit_candidates_crypto__k1m_extreme_reverse import (
    _k1m_extreme_reverse_fires,
    k1m_exit_indicators,
)
from vec_decisions.live_exit_chain import k1m_exit_reason, prepare, step


def _native_k1m_branch(source):
    tree = ast.parse(source)
    branch = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test).startswith("not hard_exit_reason")
        and any(
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and "[K1M_EXTREME_REVERSE_LONG]" in child.value
            for child in ast.walk(node)
        )
    )
    fn = ast.FunctionDef(
        name="native_k1m_branch",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[
            ast.Assign(
                targets=[ast.Name(id="hard_exit_reason", ctx=ast.Store())],
                value=ast.Constant(value=None),
            ),
            copy.deepcopy(branch),
            ast.Return(value=ast.Name(id="hard_exit_reason", ctx=ast.Load())),
        ],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    return compile(module, "ez_positions_quick.py:native_k1m_branch", "exec")


def _run_native_k1m(is_long, indicators, *, gain=0.5, grace=False, trend_veto=False, ltf=False):
    source = (Path(__file__).resolve().parents[1] / "ez_positions_quick.py").read_text()
    code = _native_k1m_branch(source)
    logs = []
    namespace = {
        "hard_exit_reason": None,
        "is_hedge": False,
        "_in_grace_period": grace,
        "current_gain": gain,
        "_trend_veto_active": trend_veto,
        "_sw_get": lambda symbol, side, name, default=False: (
            True if name == "K1M_EXTREME_REVERSE_ENABLED" else
            ltf if name == "LIVE_5m_trading_ENABLED" else default
        ),
        "symbol": "TESTUSDC",
        "position_key": "TESTUSDC_LONG" if is_long else "TESTUSDC_SHORT",
        "position_side": "LONG" if is_long else "SHORT",
        "config": SimpleNamespace(LIVE_5m_trading_ENABLED=ltf),
        "indicators": indicators,
        "is_long": is_long,
        "safe_fetch_float": lambda value, default=0.0: (
            float(value) if value is not None and np.isfinite(float(value)) else default
        ),
        "logger": SimpleNamespace(critical=lambda message: logs.append(message)),
    }
    exec(code, namespace)
    return namespace["native_k1m_branch"](), logs


@pytest.mark.parametrize(
    "is_long,indicators,prefix",
    [
        (True, {"k_15m": 95.0, "k_15m_prev": 96.0}, "K1M_EXTREME_REVERSE_LONG"),
        (False, {"k_15m": 5.0, "k_15m_prev": 4.0}, "K1M_EXTREME_REVERSE_SHORT"),
    ],
)
def test_actual_epq_k1m_branch_pairs_15m_and_keeps_original_logs(is_long, indicators, prefix):
    reason, logs = _run_native_k1m(is_long, indicators)
    assert reason.startswith(prefix)
    assert reason.endswith("_gain0.50%")
    assert len(logs) == 1 and prefix in logs[0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"gain": -0.01},
        {"grace": True},
        {"trend_veto": True},
    ],
)
def test_actual_epq_k1m_branch_obeys_gain_grace_and_trend_veto(kwargs):
    reason, logs = _run_native_k1m(
        True, {"k_15m": 95.0, "k_15m_prev": 96.0}, **kwargs
    )
    assert reason is None and logs == []


def _cfg(**overrides):
    fields = {
        "MODE": "crypto",
        "K1M_EXTREME_REVERSE_ENABLED": True,
        "LIVE_5m_trading_ENABLED": False,
        "TREND_REGIME_VETO_ENABLED": True,
        "REENTRY_GRACE_MINUTES": 30.0,
        "AUGMENTED_DC_BREAK_ENABLED": False,
        "PARABOLIC_EXIT_ENABLED": False,
        "STRUCTURAL_RANGE_SHIFT_EXIT": False,
        "KEY_LEVEL_CRASH_ENABLED": False,
        "BREAKEVEN_GAIN_EROSION_ENABLED": False,
        "BREAKEVEN_DC_LOW4_ENABLED": False,
        "WT_CROSS_EXIT_ENABLED": False,
        "STDEV_REJECT_EXIT_ENABLED": False,
        "PEAK_GIVEBACK_PROTECTION_ENABLED": False,
        "AUGMENTED_DC_BREAK_REDUCE_TO_MIN_ENABLED": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _prepared(is_long, k_now, k_prev, cfg=None):
    cfg = cfg or _cfg()
    n = len(k_now)
    npz = {
        "k_15m": np.asarray(k_now, dtype=float),
        "k_15m_prev": np.asarray(k_prev, dtype=float),
    }

    def safe(data, key, length, default=0.0):
        if key in data:
            return np.asarray(data[key], dtype=float)
        return np.full(length, default, dtype=float)

    return prepare(npz, n, is_long, cfg, np.full(n, 100.0), safe), cfg


@pytest.mark.parametrize(
    "is_long,k_now,k_prev,expected",
    [(True, 95.0, 96.0, True), (False, 5.0, 4.0, True)],
)
def test_native_mapping_and_vector_step_fire_on_paired_15m(is_long, k_now, k_prev, expected):
    indicators = {"k_15m": k_now, "k_15m_prev": k_prev, "k_1m": 5.0, "k_1m_prev": 4.0}
    paired = k1m_exit_indicators(indicators, ltf_enabled=False)
    native_now = float(paired["stoch_k_1m"])
    native_prev = float(paired["k_1m_prev"])
    assert _k1m_extreme_reverse_fires(native_now, native_prev, is_long, 90.0, 10.0) is expected

    P, cfg = _prepared(is_long, [k_now], [k_prev])
    result = step(
        P,
        cfg,
        0,
        100.0,
        {"gain": 0.5, "peak": 0.5, "age_min": 31.0, "entry_price": 100.0},
    )
    assert result is not None
    reason, full_close = result
    side = "LONG" if is_long else "SHORT"
    assert reason.startswith(f"K1M_EXTREME_REVERSE_{side}_k1m=")
    assert reason.endswith("_gain0.50%")
    assert full_close is False


def test_ltf_on_keeps_real_k1m_values_and_does_not_use_15m_pair():
    values = k1m_exit_indicators(
        {"k_1m": 95.0, "k_1m_prev": 96.0, "k_15m": 50.0, "k_15m_prev": 50.0},
        ltf_enabled=True,
    )
    assert values == {"stoch_k_1m": 95.0, "k_1m_prev": 96.0}


@pytest.mark.parametrize(
    "state",
    [
        {"gain": -0.01, "age_min": 31.0},
        {"gain": 0.5, "age_min": 2.99},
        {"gain": np.nan, "age_min": 31.0},
        {"gain": 0.5, "age_min": 31.0, "is_hedge": True},
    ],
)
def test_gain_grace_nan_and_hedge_state_refuse_k1m(state):
    P, cfg = _prepared(True, [95.0], [96.0])
    base = {"peak": 1.0, "entry_price": 100.0, "reentered": False}
    base.update(state)
    assert k1m_exit_reason(P, cfg, 0, base) is None


def test_trend_regime_veto_matches_live_three_timeframe_all_aligned_rule():
    P, cfg = _prepared(True, [95.0], [96.0])
    P["trend_veto"][0] = True
    assert k1m_exit_reason(P, cfg, 0, {"gain": 0.5, "age_min": 31.0}) is None
    P["trend_veto"][0] = False
    assert k1m_exit_reason(P, cfg, 0, {"gain": 0.5, "age_min": 31.0}) is not None


@pytest.mark.parametrize("is_long", [True, False])
def test_zero_nan_or_equal_predecessor_does_not_fire(is_long):
    now = 95.0 if is_long else 5.0
    P, cfg = _prepared(is_long, [now, now, now], [0.0, np.nan, now])
    zero_prev = k1m_exit_reason(P, cfg, 0, {"gain": 0.5, "age_min": 31.0})
    if is_long:
        assert zero_prev is None
    else:
        assert zero_prev.startswith("K1M_EXTREME_REVERSE_SHORT")
    assert k1m_exit_reason(P, cfg, 1, {"gain": 0.5, "age_min": 31.0}) is None
    assert k1m_exit_reason(P, cfg, 2, {"gain": 0.5, "age_min": 31.0}) is None


def test_active_ltf_lane_requires_real_one_minute_npz_fields():
    cfg = _cfg(LIVE_5m_trading_ENABLED=True)
    n = 1

    def safe(data, key, length, default=0.0):
        return np.asarray(data.get(key, np.full(length, default)), dtype=float)

    with pytest.raises(ValueError, match="stoch_k_1m"):
        from vec_decisions.live_exit_chain import validate_supported_inputs

        validate_supported_inputs({"k_15m": np.array([95.0]), "k_15m_prev": np.array([96.0])}, n, cfg, True)
