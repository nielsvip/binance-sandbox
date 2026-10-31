import ast
import random
import textwrap
from pathlib import Path

import numpy as np

from vec_decisions.asr_volume_setup_score import volume_setup_array, volume_setup_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_volume_setup_score_ez_positions_quick_20261031T063731Z_1793428651782646000.py"


def _source():
    lines = ORIGINAL.read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line == "            if rel_vol > 1.5:")
    end = next(index for index in range(start, len(lines)) if 'logger.info(f" in rate3 {position_key}' in lines[index])
    return textwrap.dedent("\n".join(lines[start:end]))


def _values(rng):
    return {
        "rel_vol": rng.choice([0.1, 0.49, 0.5, 1.0, 1.5, 1.6, 1.99, 2.0, 3.0]),
        "z_conviction": rng.choice([0.0, 20.0, 20.1, 23.0, 50.0, float("nan")]),
        "current_price": rng.choice([0.99, 1.0, 1.005, 1.01]),
        "dc_high_1h": rng.choice([0.99, 1.0, 1.01]),
        "dc_low_1h": rng.choice([0.99, 1.0, 1.01]),
        "trend_ok": rng.choice([False, True]),
        "pa_confirm": rng.choice([False, True]),
        "pr_confirm": rng.choice([False, True]),
        "k_1m_prev": rng.choice([0.0, 9.0, 10.0, 50.0, 90.0, 91.0, 100.0]),
        "k_3m": rng.choice([0.0, 24.0, 25.0, 50.0, 75.0, 76.0, 100.0]),
        "k_1m": rng.choice([0.0, 9.0, 10.0, 50.0, 90.0, 91.0, 100.0]),
    }


def _native_eval(values, is_long):
    indicators = {
        f"zconviction_augment_{side}": values["z_conviction"]
        for side in ("long", "short")
    }
    scope = dict(
        values,
        is_long=is_long,
        score=0.0,
        reasons=[],
        valid_setup=False,
        ind=indicators,
        safe_fetch_float=lambda value, default: default if value is None else float(value),
    )
    exec(compile(_source(), str(ORIGINAL), "exec"), {}, scope)
    return scope["score"], scope["reasons"], scope["valid_setup"]


def test_native_source_matches_shared_scalar_for_both_sides():
    rng = random.Random(722600)
    for side in (True, False):
        for _ in range(800):
            values = _values(rng)
            assert volume_setup_scalar(values, side) == _native_eval(values, side)


def test_numpy_score_and_setup_mask_match_native_scalar_per_bar():
    rng = random.Random(722601)
    for side in (True, False):
        rows = [_values(rng) for _ in range(800)]
        arrays = {
            key: np.asarray([row[key] for row in rows], dtype=bool if key in ("trend_ok", "pa_confirm", "pr_confirm") else float)
            for key in rows[0]
        }
        score, valid, masks = volume_setup_array(arrays, side)
        native = [_native_eval(row, side) for row in rows]
        assert np.array_equal(score, np.asarray([item[0] for item in native]))
        assert np.array_equal(valid, np.asarray([item[2] for item in native]))
        assert np.array_equal(masks["Trend+PA"], np.asarray([item[2] and "Trend+PA" in item[1] for item in native]))


def test_rate_hook_applies_score_then_valid_setup_before_rate3_log():
    source = (ROOT / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_asr_volume_setup_scalar"]
    assert len(calls) == 1
    lines = source.splitlines()
    window = lines[calls[0].lineno - 1:calls[0].lineno + 11]
    assert "score += _asr_volume_delta" in window
    assert "reasons.extend(_asr_volume_reasons)" in window
    assert "valid_setup = True" in window
    assert any('logger.info(f" in rate3 {position_key}' in line for line in lines[calls[0].lineno:calls[0].lineno + 10])
