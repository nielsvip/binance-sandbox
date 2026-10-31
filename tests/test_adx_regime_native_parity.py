import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.adx_regime_gate import block_mask

def _native_gate():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "ez_positions_quick.py").read_text())
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate")
    gate = next(node for node in ast.walk(rate) if isinstance(node, ast.If) and ast.unparse(node.test) == "getattr(config, 'ADX_REGIME_FILTER_ENABLED', False) and (not is_exit)")
    function = ast.parse("def native(config, ind, is_long, is_exit=False):\n    pass\n").body[0]
    function.body = [gate, ast.Return(ast.Constant(None))]
    namespace = {"safe_fetch_float": lambda value, default: default if value is None else float(value)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "native_adx_branch", "exec"), namespace)
    return namespace["native"]

def _safe(npz, key, n, default):
    return np.asarray(npz.get(key, np.full(n, default)), dtype=float)

@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("focus_tf", ["15m", "1h", "3m"])
def test_mask_matches_actual_native_branch_at_boundaries(is_long, focus_tf):
    native = _native_gate()
    pairs = [(adx, k) for adx in (19.9, 20.0, 20.1, np.nan) for k in (19.9, 20.0, 50.0, 80.0, 80.1, np.nan)]
    cfg = SimpleNamespace(ADX_REGIME_FILTER_ENABLED=True, REGIME_DETECTION_ENABLED=False, ADX_TF="4h", ADX_RANGING_THRESHOLD=20.0, TF_FOCUS=focus_tf)
    npz = {"adx_4h": np.array([p[0] for p in pairs]), f"stoch_k_{focus_tf}": np.array([p[1] for p in pairs])}
    actual = block_mask(npz, len(pairs), is_long, cfg, _safe)
    expected = [native(cfg, {"adx_4h": adx, f"k_{focus_tf}": k}, is_long) is not None for adx, k in pairs]
    np.testing.assert_array_equal(actual, expected)

def test_missing_focus_is_neutral_and_explicit_focus_wins_alias():
    cfg = SimpleNamespace(ADX_REGIME_FILTER_ENABLED=True, TF_FOCUS="15m")
    assert block_mask({"adx_1h": [10.0]}, 1, True, cfg, _safe).tolist() == [True]
    npz = {"adx_1h": [10.0], "k_15m": [50.0], "stoch_k_15m": [10.0]}
    assert block_mask(npz, 1, True, cfg, _safe).tolist() == [True]

@pytest.mark.parametrize("enabled,regime", [(False, False), (True, True)])
def test_disabled_or_superseded_gate_does_not_block(enabled, regime):
    cfg = SimpleNamespace(ADX_REGIME_FILTER_ENABLED=enabled, REGIME_DETECTION_ENABLED=regime)
    assert not block_mask({"adx_1h": [10.0]}, 1, True, cfg, _safe).any()
