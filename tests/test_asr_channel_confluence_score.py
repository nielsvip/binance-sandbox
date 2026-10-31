import ast
import hashlib
from pathlib import Path

import numpy as np

from utils import safe_fetch_float
from vec_decisions.asr_channel_confluence_score import apply_channel_confluence_scalar, apply_channel_confluence_vec


def _reference_nodes():
    root = Path(__file__).resolve().parents[1]
    backups = sorted((root / "backups").glob("before_asr_late_score_aggregate_wire_ez_positions_quick_*.py"))
    source = next(path.read_text() for path in backups if hashlib.sha256(path.read_bytes()).hexdigest() == "ef661f182a7372ac6dc42269dc57be575d4f959a6adc63a8cd09fa6608395961")
    tree = ast.parse(source)
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    container = next(node for node in rate.body if isinstance(node, ast.If) and node.lineno == 7241)
    start = next(i for i, node in enumerate(container.body) if isinstance(node, ast.If) and node.lineno == 7979)
    end = next(i for i, node in enumerate(container.body[start:], start) if isinstance(node, ast.If) and "top_sent" in ast.unparse(node))
    return container.body[start:end]


def _rows():
    rows = []
    for i in range(16):
        row = {"close": 90.0 + i * 2, "k_3m": float((i * 13) % 101), "d_3m": float((i * 17) % 101),
               "k_15m": float((i * 19) % 101), "k_1h": float((i * 23) % 101), "d_1h": float((i * 7) % 101),
               "k_4h": float((i * 29) % 101), "d_4h": float((i * 11) % 101),
               "wt_cross_1h": "BULL" if i % 3 == 0 else "BEAR" if i % 3 == 1 else "NONE",
               "wt_cross_15m": "BULL" if i % 4 == 0 else "BEAR" if i % 4 == 1 else "NONE"}
        for tf in ("1m", "3m", "15m", "1h", "4h"):
            row[f"dc_basis_{tf}"] = 95.0 + i
            row[f"dc_low_{tf}"] = 85.0 + i
            row[f"dc_high_{tf}"] = 105.0 + i
        if i % 5:
            row["dc_high_1m"], row["dc_low_1m"] = 100.0, 90.0
        rows.append(row)
    return rows


def test_channel_confluence_matches_pre_edit_native_ast_and_numpy():
    nodes = _reference_nodes()
    rows = _rows()
    arrays = {key: np.asarray([row.get(key, 0.0) for row in rows]) for key in set().union(*(row.keys() for row in rows))}
    for is_long in (True, False):
        scores = np.linspace(-3.0, 7.0, len(rows))
        vector, masks = apply_channel_confluence_vec(arrays, len(rows), is_long, scores, arrays["close"], use_ltf=True)
        for index, row in enumerate(rows):
            values = {"score": scores[index], "is_long": is_long, "current_price": row["close"], "ind": row,
                      "safe_fetch_float": safe_fetch_float, "reasons": [],
                      "sco1h": row["wt_cross_1h"] == "BULL", "scu1h": row["wt_cross_1h"] == "BEAR",
                      "sco15m": row["wt_cross_15m"] == "BULL", "scu15m": row["wt_cross_15m"] == "BEAR"}
            for key, value in row.items():
                values[key] = value
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "pre-edit-ASR-channel", "exec"), values)
            scalar_values = dict(row)
            scalar_values["current_price"] = row["close"]
            scalar_values.setdefault("dc_high_1m", 0.0)
            scalar_values.setdefault("dc_low_1m", 0.0)
            scalar_values.update({"sco1h": row["wt_cross_1h"] == "BULL", "scu1h": row["wt_cross_1h"] == "BEAR",
                                  "sco15m": row["wt_cross_15m"] == "BULL", "scu15m": row["wt_cross_15m"] == "BEAR"})
            actual = apply_channel_confluence_scalar(scores[index], scalar_values, is_long)
            assert actual == values["score"] == vector[index]
    assert masks["DC_1M_BREAKOUT_IF_PRESENT"].dtype == np.bool_


def test_channel_confluence_missing_one_minute_channel_does_not_invent_breakout_bonus():
    row = {"close": 1000.0}
    for tf in ("3m", "15m", "1h", "4h"):
        row[f"dc_low_{tf}"], row[f"dc_high_{tf}"] = 0.0, 0.0
        row[f"dc_basis_{tf}"] = 0.0
    arrays = {key: np.asarray([value]) for key, value in row.items()}
    vector, masks = apply_channel_confluence_vec(arrays, 1, True)
    assert not masks["DC_1M_BREAKOUT_IF_PRESENT"][0]
    assert vector[0] == 10.5
