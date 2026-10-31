import ast
import hashlib
from pathlib import Path

import numpy as np

from vec_decisions.asr_score_core import apply_gain_quality_bonus_scalar, apply_gain_quality_bonus_vec


def _native_reference():
    root = Path(__file__).resolve().parents[1]
    matches = sorted((root / "backups").glob("before_asr_research_score_wire_ez_positions_quick_*.py"))
    source = next(path.read_text() for path in matches if hashlib.sha256(path.read_bytes()).hexdigest() == "fc283e675dc88d15f654873e0b751213b6268c8ce4e4874538a6d6e6a7685fe4")
    tree = ast.parse(source)
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    outer = next(node for node in rate.body if isinstance(node, ast.If) and node.lineno == 7241)
    return [node for node in outer.body if node.lineno in (8242, 8244)]


def test_gain_bonus_matches_pre_edit_native_and_second_gate_observes_first_bonus():
    nodes = _native_reference()
    assert len(nodes) == 2
    scores = np.array([8.0, 8.01, 10.0, 10.01, 11.0, 22.01, np.nan])
    gains = np.array([2.0, 2.0, 2.0, 2.0, 3.0, 3.01, 4.0])
    vector, masks = apply_gain_quality_bonus_vec(scores, gains)
    for score, gain, expected in zip(scores, gains, vector):
        env = {"score": float(score), "gain": float(gain)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "pre-edit-ASR-gain", "exec"), env)
        assert env["score"] == expected or (np.isnan(env["score"]) and np.isnan(expected))
        actual, _ = apply_gain_quality_bonus_scalar(score, gain)
        assert actual == expected or (np.isnan(actual) and np.isnan(expected))
    assert masks["GAIN_GT3_SCORE_GT10"].tolist() == [False, False, False, False, False, True, False]
    assert masks["GAIN_GT1_5_SCORE_GT8_AFTER_FIRST"].tolist() == [False, True, True, True, True, True, False]


def test_native_rate_uses_shared_gain_bonus_adapter_in_ordered_position():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    assert any(isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_score_core" for node in ast.walk(rate))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "apply_gain_quality_bonus_scalar" for node in ast.walk(rate))
