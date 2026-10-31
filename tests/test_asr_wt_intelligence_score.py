import ast
import random
import textwrap
from pathlib import Path

import numpy as np

from vec_decisions.asr_wt_intelligence_score import wt_intelligence_score_array, wt_intelligence_score_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_wt_intelligence_score_ez_positions_quick_20261031T063601Z_1793428561842531000.py"


def _source():
    lines = ORIGINAL.read_text().splitlines()
    rate = next(index for index, line in enumerate(lines) if "in rate2 {position_key}" in line)
    start = next(index for index in range(rate, len(lines)) if lines[index] == "            # === WAVETREND INTELLIGENCE SCORING ===")
    end = next(index for index in range(start, len(lines)) if lines[index] == "            if rel_vol > 1.5:")
    return textwrap.dedent("\n".join(lines[start:end]))


def _values(rng):
    return {
        "wt_divergence_1h": rng.choice([None, "BULL", "BEAR", "HIDDEN_BULL", "HIDDEN_BEAR"]),
        "wt_divergence_4h": rng.choice([None, "BULL", "BEAR", "HIDDEN_BULL", "HIDDEN_BEAR"]),
        "wt_momentum_state_1h": rng.choice(["", "EXHAUST_DOWN", "EXHAUST_UP", "IMPULSE_UP", "IMPULSE_DOWN"]),
        "wt_momentum_state_4h": rng.choice(["", "EXHAUST_DOWN", "EXHAUST_UP"]),
        "wt_cross_1h": rng.choice([None, "BULL", "BEAR"]),
        "wt_cross_rising_1h": rng.choice([None, True, False]),
        "wt_peak_structure_1h": rng.choice(["", "LH", "HH"]),
        "wt_trough_structure_1h": rng.choice(["", "HL", "LL"]),
        "wt_peak_structure_4h": rng.choice(["", "LH", "HH"]),
        "wt_trough_structure_4h": rng.choice(["", "HL", "LL"]),
        "wt_percentile_1h": rng.choice([0.0, 9.5, 10.0, 29.0, 30.0, 50.0, 70.0, 90.0, 91.0, 100.0]),
        "wt_percentile_4h": rng.choice([0.0, 29.0, 30.0, 50.0, 70.0, 71.0, 100.0]),
        "wt_extreme_1h": rng.choice([False, True]),
        "wt_trough_structure_D": rng.choice(["", "HL", "LL"]),
        "wt_peak_structure_D": rng.choice(["", "LH", "HH"]),
    }


def _native_eval(values, is_long):
    scope = {
        "is_long": is_long, "score": 0.0, "reasons": [],
        "_wt_div_1h": values["wt_divergence_1h"], "_wt_div_4h": values["wt_divergence_4h"],
        "_wt_mom_1h": values["wt_momentum_state_1h"], "_wt_cross_1h": values["wt_cross_1h"],
        "_wt_cross_rising_1h": values["wt_cross_rising_1h"],
        "_wt_peak_struct_1h": values["wt_peak_structure_1h"], "_wt_trough_struct_1h": values["wt_trough_structure_1h"],
        "_wt_peak_struct_4h": values["wt_peak_structure_4h"], "_wt_trough_struct_4h": values["wt_trough_structure_4h"],
        "_wt_pctile_1h": values["wt_percentile_1h"], "_wt_extreme_1h": values["wt_extreme_1h"],
        "ind": {
            "wt_percentile_4h": values["wt_percentile_4h"],
            "wt_divergence_4h": values["wt_divergence_4h"],
            "wt_trough_structure_4h": values["wt_trough_structure_4h"],
            "wt_peak_structure_4h": values["wt_peak_structure_4h"],
            "wt_trough_structure_D": values["wt_trough_structure_D"],
            "wt_peak_structure_D": values["wt_peak_structure_D"],
        },
        "safe_fetch_float": lambda value, default: default if value is None else float(value),
    }
    exec(compile(_source(), str(ORIGINAL), "exec"), {}, scope)
    return scope["score"], scope["reasons"]


def test_native_wt_intelligence_source_matches_shared_scalar():
    rng = random.Random(413220)
    for side in (True, False):
        for _ in range(500):
            values = _values(rng)
            native = _native_eval(values, side)
            shared = wt_intelligence_score_scalar(values, side)
            assert shared[:2] == native


def test_numpy_wt_intelligence_score_matches_each_scalar_bar():
    rng = random.Random(710102)
    for side in (True, False):
        rows = [_values(rng) for _ in range(202)]
        arrays = {key: np.asarray([row[key] for row in rows], dtype=object) for key in rows[0]}
        score, count = wt_intelligence_score_array(arrays, side)
        expected = [wt_intelligence_score_scalar(row, side) for row in rows]
        assert np.array_equal(score, np.asarray([item[0] for item in expected]))
        assert np.array_equal(count, np.asarray([item[2] for item in expected]))


def test_rate_hook_preserves_order_before_volume_bonus():
    source = (ROOT / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_wt_intelligence_score_scalar"]
    assert len(calls) == 1
    lines = source.splitlines()
    assert lines[calls[0].lineno + 2] == "            if rel_vol > 1.5:"
