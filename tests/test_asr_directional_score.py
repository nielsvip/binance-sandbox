import ast
import random
import textwrap
from pathlib import Path

import numpy as np

from vec_decisions.asr_directional_score import directional_score_array, directional_score_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_directional_score_votes_ez_positions_quick_20261031T063015Z_1793428215782316000.py"


def _native_source():
    lines = ORIGINAL.read_text().splitlines()
    rate = next(index for index, line in enumerate(lines) if "in rate2 {position_key}" in line)
    start = next(index for index in range(rate, len(lines)) if lines[index] == "            if is_long and k_1h > d_1h:")
    end = next(index for index in range(start, len(lines)) if lines[index] == "            # === WAVETREND INTELLIGENCE SCORING ===")
    return textwrap.dedent("\n".join(lines[start:end]))


def _native_eval(values, is_long):
    scope = dict(values, is_long=is_long, score=0.0)
    exec(compile(_native_source(), str(ORIGINAL), "exec"), {}, scope)
    return scope["score"]


def _values(rng):
    result = {"current_price": rng.choice([90.0, 100.0, 110.0, float("nan")])}
    result["k_1h"] = rng.choice([0.0, 50.0, 100.0, float("nan")])
    result["d_1h"] = rng.choice([0.0, 50.0, 100.0, float("nan")])
    for tf in ("15m", "1h", "4h"):
        result[f"dc_basis_{tf}"] = rng.choice([90.0, 100.0, 110.0, float("nan")])
    for tf in ("1m", "15m", "1h"):
        result[f"sma_200_{tf}"] = rng.choice([90.0, 100.0, 110.0, float("nan")])
    result["ha_15m"] = rng.choice(["green", "red", "neutral"])
    result["ha_1h"] = rng.choice(["green", "red", "neutral"])
    result["wt_score_15m"] = rng.choice([-50.0, -20.0, 0.0, 20.0, 50.0, float("nan")])
    result["wt_score_1h"] = rng.choice([-20.0, 0.0, 20.0, float("nan")])
    return result


def test_native_original_directional_votes_match_scalar():
    rng = random.Random(863002)
    for side in (True, False):
        for _ in range(500):
            values = _values(rng)
            expected = _native_eval(values, side)
            actual = directional_score_scalar(values, side)
            assert actual == expected


def test_numpy_directional_votes_match_each_native_scalar_bar():
    rng = random.Random(30051)
    for side in (True, False):
        rows = [_values(rng) for _ in range(181)]
        arrays = {key: np.asarray([row[key] for row in rows]) for key in rows[0]}
        actual = directional_score_array(arrays, side)
        expected = np.asarray([_native_eval(row, side) for row in rows])
        assert np.array_equal(actual, expected, equal_nan=True)


def test_rate_calls_directional_core_before_wt_divergence():
    source = (ROOT / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_asr_directional_score_scalar"]
    assert len(calls) == 1
    lines = source.splitlines()
    assert lines[calls[0].lineno] == "            # === WAVETREND INTELLIGENCE SCORING ==="
