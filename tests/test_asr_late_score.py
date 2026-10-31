import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from vec_decisions.asr_late_score import apply_asr_late_score_scalar, apply_asr_late_score_vec


def _cfg():
    return SimpleNamespace(
        EMA_PULLBACK_ENABLED=True, EMA_PULLBACK_TF="15m", EMA_PULLBACK_SCORE_BONUS=35,
        BB_RSI_STOCH_SCALP_ENABLED=True, BB_RSI_STOCH_SCALP_SCORE=25,
        MACD_ZERO_CROSS_ENABLED=True, MACD_ZERO_CROSS_TF="1h", MACD_ZERO_CROSS_SCORE=15,
        RSI2_MEAN_REVERSION_ENABLED=True, RSI2_THRESHOLD_LONG=15, RSI2_THRESHOLD_SHORT=85, RSI2_SCORE_BONUS=20,
        HA_WICK_QUALITY_ENABLED=True, HA_WICK_QUALITY_TF="1h", HA_WICK_QUALITY_SCORE=15,
        BB_BREAKOUT_ENABLED=True, BB_BREAKOUT_TF="1h", BB_BREAKOUT_SCORE=20,
        TRIPLE_CONF_ENABLED=True, TRIPLE_CONF_TF="1h", TRIPLE_CONF_RSI_LONG=30, TRIPLE_CONF_RSI_SHORT=70,
        TRIPLE_CONF_STOCH_LONG=20, TRIPLE_CONF_STOCH_SHORT=80, TRIPLE_CONF_SCORE=30,
        EMA200_STOCHRSI_ENABLED=True, EMA200_STOCHRSI_TF="1h", EMA200_STOCHRSI_K_LONG=20,
        EMA200_STOCHRSI_K_SHORT=80, EMA200_STOCHRSI_BODY_MULT=1.05, EMA200_STOCHRSI_SCORE=25,
        RSI_MACD_EMA_ENABLED=True, RSI_MACD_EMA_TF="1h", RSI_MACD_EMA_RSI_LONG=35,
        RSI_MACD_EMA_RSI_SHORT=65, RSI_MACD_EMA_SCORE=25,
        DC_BREAKOUT_ENTRY_ENABLED=True, DC_BREAKOUT_TF_EXPANDED="1h", DC_BREAKOUT_SCORE=15,
    )


def _reference_nodes():
    root = Path(__file__).resolve().parents[1]
    backups = sorted((root / "backups").glob("before_asr_dc_position_score_wire_ez_positions_quick_*.py"))
    source = next(path.read_text() for path in backups if hashlib.sha256(path.read_bytes()).hexdigest() == "2583a5f3a4f0041f52cf00fcbeed00e1979f5c3c9b78c5aa85821ddd23d1c3cf")
    tree = ast.parse(source)
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    container = next(node for node in rate.body if isinstance(node, ast.If) and node.lineno == 7241)
    start = next(i for i, node in enumerate(container.body) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "knife_penalty" for target in node.targets))
    end = next(i for i, node in enumerate(container.body[start:], start) if isinstance(node, ast.If) and "MACD_EXIT_ENABLED" in ast.unparse(node))
    return container.body[start:end]


def _row(is_long, knife=False, low=False, high=False):
    row = {"close": 101.0 if is_long else 99.0, "k_3m": 10.0 if is_long and knife else (90.0 if not is_long and knife else 50.0),
           "d_3m": 20.0 if is_long else 80.0, "k_3m_prev": 15.0 if is_long else 85.0,
           "k_15m": 50.0, "rsi_2_1h": 50.0}
    for j, tf in enumerate(("3m", "15m", "1h", "4h", "D")):
        row[f"dc_low_{tf}"] = 90.0
        row[f"dc_high_{tf}"] = (120.0 if low else 92.0 if high else 110.0) + j
    return row


def test_late_aggregate_matches_ordered_pre_edit_native_ast_and_numpy():
    nodes = _reference_nodes()
    rows = [_row(is_long, knife=bool(i & 1), low=bool(i & 2), high=bool(i & 4)) for is_long in (True, False) for i in range(8)]
    cfg = _cfg()
    scores = np.linspace(5, 15, len(rows))
    gains = np.array([0, 1.6, 2.0, 3.0, 3.01, 4.0, 5.0, 2.5] * 2)
    for is_long in (True, False):
        selected = rows[:8] if is_long else rows[8:]
        offset = 0 if is_long else 8
        arrays = {key: np.asarray([row.get(key, 0.0) for row in selected]) for key in set().union(*(row.keys() for row in selected))}
        vector, _ = apply_asr_late_score_vec(arrays, len(selected), is_long, cfg, gains[offset:offset+8], scores[offset:offset+8], current_price=arrays["close"])
        for local, row in enumerate(selected):
            values = {"score": scores[offset+local], "reasons": [], "ind": row, "current_price": row["close"], "is_long": is_long,
                      "gain": gains[offset+local], "config": cfg, "is_exit": False}
            for tf in ("3m", "15m", "1h", "4h", "D"):
                values[f"dc_low_{tf}"] = row.get(f"dc_low_{tf}", 0.0)
                values[f"dc_high_{tf}"] = row.get(f"dc_high_{tf}", 0.0)
            for key in ("k_3m", "d_3m", "k_3m_prev", "k_15m"):
                values[key] = row.get(key, 50.0)
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "pre-edit-ASR-late", "exec"), values)
            actual, reasons = apply_asr_late_score_scalar(
                scores[offset+local], [], row, row["close"], is_long, cfg, gains[offset+local],
                {key: value for key, value in row.items() if key.startswith("dc_")},
                values["k_3m"], values["d_3m"], values["k_3m_prev"], values["k_15m"], False,
            )
            assert actual == values["score"] == vector[local]
            assert reasons == values["reasons"]


def test_late_aggregate_native_wiring_calls_shared_ordered_adapter():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "ez_positions_quick.py").read_text())
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    assert any(isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_late_score" for node in ast.walk(rate))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "apply_asr_late_score_scalar" for node in ast.walk(rate))
