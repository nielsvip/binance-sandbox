import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.asr_score_tail import finish_array, finish_scalar

def _original_tail():
    root = Path(__file__).resolve().parents[1]
    expected = "21e9652e4e3f35e3048cadb8b4be9013a4680a05dcd72828696dae7a3f632d60"
    source = next(path for path in (root / "backups").glob("before_asr_final_score_tail_ez_positions_quick_*.py") if hashlib.sha256(path.read_bytes()).hexdigest() == expected)
    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for index, statement in enumerate(body):
            if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_sg_breakout" for target in statement.targets):
                function = ast.parse("def original(config, score, current_price, is_long, dc_high_1h, dc_high_3m, dc_low_1h, dc_low_3m, k_1m, k_3m, true_lag, rec):\n    reasons = []\n").body[0]
                function.body.extend(body[index:])
                scope = {}
                exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(source), "exec"), scope)
                return scope["original"]
    raise AssertionError("Original native final score branch not found")

@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("bypass", [True, False])
def test_scalar_and_numpy_preserve_original_native_order_and_thresholds(is_long, bypass):
    original = _original_tail()
    cfg = SimpleNamespace(OVERBOUGHT_SCORE_GUT_BREAKOUT_BYPASS=bypass)
    rng = np.random.default_rng(40)
    scores = np.concatenate((np.array([-5, 0, 17.49, 17.5, 18.5, 23.49, 23.5, 24.5, 28.49, 28.5, 29.5, 60.5, 100]), rng.uniform(-100, 500, 400)))
    n = len(scores)
    price = rng.uniform(90, 110, n)
    channels = [rng.choice([0.0, 99.0, 101.0], n) for _ in range(4)]
    k1 = rng.choice([29.9, 30.0, 70.0, 70.1, np.nan], n)
    k3 = rng.choice([19.9, 20.0, 80.0, 80.1, np.nan], n)
    lag = rng.choice([9.9, 10.0, 10.1, np.nan], n)
    expected = []
    for index in range(n):
        args = (cfg, scores[index], price[index], is_long, *(values[index] for values in channels), k1[index], k3[index], lag[index], "KEEP_PREVIOUS")
        value = original(*args)[:2]
        assert finish_scalar(*args) == value
        expected.append(value)
    actual_score, actual_rec = finish_array(cfg, scores, price, is_long, *channels, k1, k3, lag, "KEEP_PREVIOUS")
    np.testing.assert_array_equal(actual_score, [row[0] for row in expected])
    np.testing.assert_array_equal(actual_rec, [row[1] for row in expected])

@pytest.mark.parametrize("value,error", [(np.nan, ValueError), (np.inf, OverflowError), (-np.inf, OverflowError)])
def test_nonfinite_score_is_not_silently_promoted_or_zeroed(value, error):
    args = (SimpleNamespace(), value, 100.0, True, 0.0, 0.0, 0.0, 0.0, 50.0, 50.0, 0.0, "WAIT")
    for function in (_original_tail(), finish_scalar, finish_array):
        with pytest.raises(error):
            function(*args)

def test_native_rate_uses_shared_tail():
    source = (Path(__file__).resolve().parents[1] / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    assert any(isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_score_tail" for node in ast.walk(tree))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_finish_asr_score" for node in ast.walk(tree))
