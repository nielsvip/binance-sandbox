import ast
import random
import textwrap
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from vec_decisions.asr_mts_mi_score import mts_mi_score_array, mts_mi_score_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_mts_mi_score_ez_positions_quick_20261031T064927Z_1793429367103964000.py"


def _source():
    lines = ORIGINAL.read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line == "            # Unified multi-TF bonus/penalty from analyze_multi_tf_state")
    end = next(index for index in range(start, len(lines)) if line_contains_sma_gate(lines[index]))
    return textwrap.dedent("\n".join(lines[start:end]))


def line_contains_sma_gate(line):
    return "if not is_exit and sma_200_D > 0:" in line


def _config(rng, enabled):
    return SimpleNamespace(
        MTS_BOTTOM_STRONG_THRESHOLD=rng.choice([25.0, 40.0, 50.0]),
        MTS_BOTTOM_BONUS_THRESHOLD=rng.choice([10.0, 25.0, 30.0]),
        MTS_ENTRY_QUALITY_STRONG=rng.choice([25.0, 40.0, 50.0]),
        MTS_ENTRY_QUALITY_BONUS=rng.choice([10.0, 25.0, 30.0]),
        MI_ENTRY_ENABLED=enabled,
        MI_ENTRY_STRUCT_BONUS=rng.choice([5, 10, 15]),
        MI_ENTRY_EXHAUST_BONUS=rng.choice([4, 8, 12]),
    )


def _values(rng):
    return {
        "mts_bottom": rng.choice([0.0, 10.0, 24.0, 25.0, 30.0, 40.0, 50.0, 60.0]),
        "mts_entry_quality": rng.choice([0.0, 10.0, 24.0, 25.0, 30.0, 40.0, 50.0, 60.0]),
        "wt_trough_structure_1h": rng.choice(["", "HL", "LL"]),
        "wt_trough_structure_4h": rng.choice(["", "HL", "LL"]),
        "wt_peak_structure_1h": rng.choice(["", "LH", "HH"]),
        "wt_peak_structure_4h": rng.choice(["", "LH", "HH"]),
        "wt_momentum_state_1h": rng.choice(["", "EXHAUST_DOWN", "EXHAUST_UP"]),
        "wt_momentum_state_4h": rng.choice(["", "EXHAUST_DOWN", "EXHAUST_UP"]),
        "wt_divergence_1h": rng.choice([None, "BULL", "BEAR", "HIDDEN_BULL", "HIDDEN_BEAR"]),
    }


def _native(values, side, cfg):
    mapping = {
        "_mts_bottom": values["mts_bottom"], "_mts_eq": values["mts_entry_quality"],
        "_wt_trough_struct_1h": values["wt_trough_structure_1h"], "_wt_trough_struct_4h": values["wt_trough_structure_4h"],
        "_wt_peak_struct_1h": values["wt_peak_structure_1h"], "_wt_peak_struct_4h": values["wt_peak_structure_4h"],
        "_wt_mom_1h": values["wt_momentum_state_1h"], "_wt_mom_4h": values["wt_momentum_state_4h"],
        "_wt_div_1h": values["wt_divergence_1h"],
    }
    scope = dict(mapping, is_long=side, config=cfg, score=0, reasons=[])
    exec(compile(_source(), str(ORIGINAL), "exec"), {}, scope)
    return scope["score"], scope["reasons"]


def test_exact_native_mts_mi_score_block_matches_shared_scalar():
    rng = random.Random(905317)
    for side in (True, False):
        for enabled in (False, True):
            for _ in range(350):
                values = _values(rng)
                cfg = _config(rng, enabled)
                assert mts_mi_score_scalar(values, side, cfg) == _native(values, side, cfg)


def test_numpy_mts_mi_score_matches_native_scalar_bars():
    rng = random.Random(905318)
    for side in (True, False):
        for enabled in (False, True):
            rows = [_values(rng) for _ in range(300)]
            cfg = _config(rng, enabled)
            arrays = {key: np.asarray([row[key] for row in rows], dtype=object if key.startswith("wt_") else float) for key in rows[0]}
            scores, masks = mts_mi_score_array(arrays, side, cfg)
            expected = [_native(row, side, cfg)[0] for row in rows]
            assert np.array_equal(scores, np.asarray(expected))


def test_native_rate_invokes_mts_mi_core_before_sma200d_veto():
    source = (ROOT / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_asr_mts_mi_score_scalar")
    lines = source.splitlines()
    gate = next(index for index, line in enumerate(lines, start=1) if "if not is_exit and sma_200_D > 0:" in line)
    assert call.lineno < gate
