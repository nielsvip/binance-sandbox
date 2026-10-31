import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.clenow_entry_gate import pass_mask
from vec_decisions.twin_gates_sizing_a import clenow_blocks

def safe(data, key, n, default=0.0):
    return np.asarray(data[key], dtype=float) if key in data else np.full(n, default)

@pytest.mark.parametrize("side", [True, False])
@pytest.mark.parametrize("enabled,gate", [(False, False), (False, True), (True, False), (True, True)])
def test_crypto_actual_scalar_and_no_future_dependency(side, enabled, gate):
    cfg = SimpleNamespace(MODE="crypto", CLENOW_ENABLED=enabled, CLENOW_GATE_ENABLED=gate)
    price = np.array([100., 100., 100., 100., 100., 0., np.nan])
    data = {"sma_200_1h": np.array([0., 100., 90., 110., np.nan, 90., 90.])}
    result = pass_mask(data, len(price), side, cfg, price, safe)
    expected = [not clenow_blocks(lambda key, default: getattr(cfg, key, default), {key: values[index] for key, values in data.items()}, side, px)[0] for index, px in enumerate(price)]
    np.testing.assert_array_equal(result, expected)
    for end in range(1, len(price)):
        np.testing.assert_array_equal(result[:end], pass_mask({key: values[:end] for key, values in data.items()}, end, side, cfg, price[:end], safe))

@pytest.fixture(scope="module")
def native_stock_gate():
    tree = ast.parse(Path("tradier_manage.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_apply_research_only_live_gates")
    selected = [node for node in function.body if isinstance(node, ast.If) and isinstance(node.test, ast.Call) and isinstance(node.test.func, ast.Name) and node.test.func.id == "_cfg" and node.test.args and isinstance(node.test.args[0], ast.Constant) and node.test.args[0].value in ("CLENOW_ENABLED", "CLENOW_GATE_ENABLED")]
    assert len(selected) == 2
    function.body = ast.parse("is_long = side == 'LONG'").body + selected + ast.parse("return False, ''").body
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "native_clenow_stock", "exec"), namespace)
    return namespace

@pytest.mark.parametrize("side", [True, False])
@pytest.mark.parametrize("enabled,gate", [(False, False), (False, True), (True, False), (True, True)])
def test_stock_actual_native_fields_and_independent_score_gate(native_stock_gate, side, enabled, gate):
    cfg = SimpleNamespace(MODE="tradier", CLENOW_ENABLED=enabled, CLENOW_GATE_ENABLED=gate)
    data = {"current_price": np.array([100., 100., 100., 100., 0.]), "close_5m": np.full(5, 95.), "sma200_D": np.array([100., 110., 90., 0., 100.]), "sma_200_1h": np.full(5, 300.), "clenow_score": np.array([15., 14., 16., 0., 100.])}
    native_stock_gate["_cfg"] = lambda key, default, *args: getattr(cfg, key, default)
    result = pass_mask(data, 5, side, cfg, data["current_price"], safe)
    expected = [not native_stock_gate["_apply_research_only_live_gates"]("TRA", "TEST", "LONG" if side else "SHORT", {key: values[index] for key, values in data.items()}, True)[0] for index in range(5)]
    np.testing.assert_array_equal(result, expected)
