import ast
import random
import textwrap
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from vec_decisions.asr_portfolio_ratio_score import portfolio_ratio_array, portfolio_ratio_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_portfolio_ratio_score_ez_positions_quick_20261031T064548Z_1793429148585179000.py"


def _source():
    lines = ORIGINAL.read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line == '            _rs_min = getattr(config, "LS_RATIO_MIN", 0.40)')
    end = next(index for index in range(start, len(lines)) if "# Shared rank-conviction score adapter" in lines[index])
    return textwrap.dedent("\n".join(lines[start:end]))


def _native(ratio, side, minimum, maximum):
    scope = {
        "_rs_ratio": ratio,
        "is_long": side,
        "score": 0,
        "reasons": [],
        "config": SimpleNamespace(LS_RATIO_MIN=minimum, LS_RATIO_MAX=maximum),
    }
    exec(compile(_source(), str(ORIGINAL), "exec"), {}, scope)
    return scope["score"], scope["reasons"]


def test_exact_native_ratio_branch_matches_scalar_boundaries_and_nan():
    rng = random.Random(410607)
    for side in (True, False):
        for ratio in [float("nan"), 0.39, 0.40, 0.41, 2.49, 2.50, 2.51] + [rng.uniform(0.1, 3.0) for _ in range(1000)]:
            delta, reasons = portfolio_ratio_scalar(ratio, 0.40, 2.50, side)
            assert (delta, reasons) == _native(ratio, side, 0.40, 2.50)


def test_numpy_ratio_contribution_matches_native_and_honors_eligibility():
    rng = random.Random(410608)
    ratios = np.asarray([float("nan"), 0.39, 0.40, 0.41, 2.49, 2.50, 2.51] + [rng.uniform(0.1, 3.0) for _ in range(500)])
    eligible = np.asarray([bool(index % 2) for index in range(ratios.size)])
    for side in (True, False):
        scores, masks = portfolio_ratio_array(ratios, eligible, 0.40, 2.50, side)
        expected = [portfolio_ratio_scalar(value, 0.40, 2.50, side)[0] if eligible[index] else 0.0 for index, value in enumerate(ratios)]
        assert np.array_equal(scores, np.asarray(expected))


def test_native_rate_helper_is_inside_account_service_guard():
    source = (ROOT / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_asr_portfolio_ratio_scalar")
    guard = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and node.lineno < call.lineno < node.end_lineno and "tracker_manager" in ast.unparse(node.test) and "positions_service" in ast.unparse(node.test))
    assert guard.lineno < call.lineno < guard.end_lineno
