import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from utils import safe_fetch_float
from vec_decisions.asr_research_score import apply_research_score_scalar, apply_research_score_vec


def _reference_nodes():
    root = Path(__file__).resolve().parents[1]
    matches = sorted((root / "backups").glob("before_asr_research_score_wire_ez_positions_quick_*.py"))
    source = next(path.read_text() for path in matches if hashlib.sha256(path.read_bytes()).hexdigest() == "fc283e675dc88d15f654873e0b751213b6268c8ce4e4874538a6d6e6a7685fe4")
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert digest == "fc283e675dc88d15f654873e0b751213b6268c8ce4e4874538a6d6e6a7685fe4"
    tree = ast.parse(source)
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    container = next(node for node in rate.body if isinstance(node, ast.If) and node.lineno == 7241)
    start = next(i for i, node in enumerate(container.body) if isinstance(node, ast.If) and "EMA_PULLBACK_ENABLED" in ast.unparse(node))
    end = next(i for i, node in enumerate(container.body[start:], start) if isinstance(node, ast.If) and "MACD_EXIT_ENABLED" in ast.unparse(node))
    return [ast.fix_missing_locations(ast.If(test=ast.UnaryOp(op=ast.Not(), operand=ast.Name(id="is_exit", ctx=ast.Load())), body=container.body[start:end], orelse=[]))]


def test_live_rate_research_block_calls_shared_adapter_inside_entry_branch():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "ez_positions_quick.py").read_text())
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    calls = [node for node in ast.walk(rate) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "apply_asr_late_score_scalar"]
    assert len(calls) == 1
    assert any(isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_late_score" for node in ast.walk(rate))


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


def _case(is_long, is_exit=False):
    return {
        "ema_9_15m": 101 if is_long else 99, "ema_14_15m": 102 if is_long else 98,
        "ema_20_15m": 99 if is_long else 101, "k_15m": 10 if is_long else 90,
        "bb_pct_b_1h": .01 if is_long else .99, "rsi_1h": 20 if is_long else 80,
        "k_1h": 10 if is_long else 90, "macd_1h": -1 if is_long else 1,
        "macd_crossover_1h": is_long, "macd_crossunder_1h": not is_long,
        "sma_200_1h": 100, "rsi_2_1h": 10 if is_long else 90,
        "ha_streak_1h": 3 if is_long else -3, "adx_1h": 30,
        "stoch_crossover_1h": is_long, "stoch_crossunder_1h": not is_long,
        "candle_body_ratio_1h": 1.2, "ema_9_1h": 99 if is_long else 101,
        "dc_high_1h": 100, "dc_low_1h": 100,
    }


def test_ordered_research_score_matches_native_ast_and_array_both_sides():
    nodes = _reference_nodes()
    for is_long in (True, False):
        for is_exit in (False, True):
            indicators = _case(is_long, is_exit)
            env = dict(safe_fetch_float=safe_fetch_float, config=_cfg(), is_long=is_long, is_exit=is_exit,
                       ind=indicators, metrics={}, current_price=101 if is_long else 99, score=2.5, reasons=[])
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "pre-edit-ASR", "exec"), env)
            expected_score, expected_reasons = env["score"], env["reasons"]
            actual_score, actual_reasons = apply_research_score_scalar(2.5, [], indicators, env["current_price"], is_long, env["config"], is_exit)
            assert actual_score == expected_score
            assert actual_reasons == expected_reasons
            arrays = {key: np.asarray([value]) for key, value in indicators.items()}
            vector, _ = apply_research_score_vec(arrays, 1, is_long, env["config"], score=np.array([2.5]), current_price=np.array([env["current_price"]]), is_exit=is_exit)
            assert vector[0] == expected_score


def test_research_score_missing_and_nan_inputs_follow_scalar_defaults():
    cfg = _cfg()
    for is_long in (True, False):
        indicators = {"rsi_2_1h": np.nan, "macd_crossover_1h": "False", "ha_streak_1h": None}
        scalar, _ = apply_research_score_scalar(0, [], indicators, 100, is_long, cfg, False)
        vector, _ = apply_research_score_vec({key: np.asarray([value]) for key, value in indicators.items()}, 1, is_long, cfg, current_price=np.array([100]))
        assert vector[0] == scalar
