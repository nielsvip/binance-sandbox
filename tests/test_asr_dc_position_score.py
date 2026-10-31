import ast
import hashlib
from pathlib import Path

import numpy as np

from vec_decisions.asr_dc_position_score import apply_dc_position_score_scalar, apply_dc_position_score_vec


def _reference_nodes():
    root = Path(__file__).resolve().parents[1]
    backups = sorted((root / "backups").glob("before_asr_gain_quality_bonus_wire_ez_positions_quick_*.py"))
    source = next(path.read_text() for path in backups if hashlib.sha256(path.read_bytes()).hexdigest() == "e80332fdfe76157df0e3fd44a1a75089138e583eb32e21ad9ec049d88e4e326d")
    tree = ast.parse(source)
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    container = next(node for node in rate.body if isinstance(node, ast.If) and node.lineno == 7241)
    start = next(i for i, node in enumerate(container.body) if isinstance(node, ast.FunctionDef) and node.name == "_dc_pos")
    end = next(i for i, node in enumerate(container.body[start:], start) if isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_research_score")
    return container.body[start:end]


def _arrays(rows):
    keys = set().union(*(row.keys() for row in rows))
    return {key: np.asarray([row.get(key, 0.0) for row in rows]) for key in keys}


def test_dc_position_score_matches_native_ast_reason_order_both_sides():
    nodes = _reference_nodes()
    rows = []
    for i, value in enumerate((0.0, 0.15, 0.30, 0.66, 0.81, 1.0)):
        row = {}
        for j, tf in enumerate(("3m", "15m", "1h", "4h", "D")):
            low = 100 + j * 2
            high = low + 20
            row[f"dc_low_{tf}"] = low
            row[f"dc_high_{tf}"] = low + (value * 20)
        rows.append(row)
    rows.extend((
        {f"dc_{kind}_{tf}": (np.nan if tf == "3m" and kind == "high" else (0 if kind == "low" else 1)) for tf in ("3m", "15m", "1h", "4h", "D") for kind in ("low", "high")},
        {},
    ))
    for is_long in (True, False):
        vector, _ = apply_dc_position_score_vec(_arrays(rows), len(rows), is_long, score=np.full(len(rows), 1.25), current_price=np.full(len(rows), 110.0))
        for i, row in enumerate(rows):
            env = {"score": 1.25, "reasons": [], "ind": row, "current_price": 110.0, "is_long": is_long}
            for tf in ("3m", "15m", "1h", "4h", "D"):
                env[f"dc_low_{tf}"] = row.get(f"dc_low_{tf}", 0.0)
                env[f"dc_high_{tf}"] = row.get(f"dc_high_{tf}", 0.0)
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "pre-edit-ASR-DC", "exec"), env)
            actual, reasons = apply_dc_position_score_scalar(1.25, [], row, 110.0, is_long)
            assert actual == env["score"]
            assert reasons == env["reasons"]
            assert vector[i] == env["score"]


def test_dc_position_score_preserves_score_and_nan_clamp_behavior():
    row = {"dc_low_3m": 100.0, "dc_high_3m": 200.0, "dc_low_15m": 100.0, "dc_high_15m": np.nan}
    scalar, _ = apply_dc_position_score_scalar(0, [], row, 110, True)
    vector, _ = apply_dc_position_score_vec(_arrays([row]), 1, True, current_price=np.array([110]))
    assert vector[0] == scalar
