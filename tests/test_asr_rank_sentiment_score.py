import ast
import random
import textwrap
from pathlib import Path

import numpy as np

from vec_decisions.asr_rank_sentiment_score import apply_rank_sentiment_scalar, apply_rank_sentiment_vec


_BACKUP = Path(__file__).resolve().parents[1] / "backups/before_asr_rank_sentiment_native_ez_positions_quick_20261031T061103Z_1793427063262465000.py"


def _legacy_native_block():
    source = _BACKUP.read_text()
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if "# 2026-05-08: REMOVED final_ai_score" in line)
    end = next(i for i, line in enumerate(lines[start:], start) if 'logger.info(f" in rate4 {symbol} {score} ")' in line)
    return textwrap.dedent("\n".join(lines[start:end]))


def _legacy_score(values):
    env = {**values, "ind": ({"0ranking_points": values["rank_points"]} if values["ind"] else {}), "safe_fetch_float": lambda value, default=0.0: default if value is None else float(value)}
    exec(_legacy_native_block(), env)
    return env["score"]


def test_scalar_matches_exact_pre_edit_native_block_random_and_boundaries():
    rng = random.Random(20261031)
    points = [0, 5, 10, 20, 25, 30, 40, 50, 80, 90, 95, 100]
    sentiments = ["", "NEUTRAL", "EXTREME_BULLISH", "EXTREME_BEARISH", "BEARISH", "BULLISH", "MIXED_BEARISH"]
    for _ in range(500):
        values = {"score": rng.uniform(-10, 40), "is_long": bool(rng.randrange(2)),
                  "top_sent": bool(rng.randrange(2)), "bot_sent": bool(rng.randrange(2)),
                  "ind": {"0ranking_points": rng.choice(points)} if rng.randrange(2) else {}, "rank_points": 0,
                  "history_score_mod": rng.choice([-2.0, 0.0, 1.0]), "local_sent": rng.choice([-60, -50, -30, -25, 0, 25, 50, 60]),
                  "global_sent": rng.choice([-60, -50, -30, -20, 0, 20, 30, 50, 60]), "sentc": rng.choice(sentiments)}
        values["rank_points"] = values["ind"].get("0ranking_points", 0)
        legacy = _legacy_score(values)
        actual = apply_rank_sentiment_scalar(values["score"], values["is_long"], values["top_sent"], values["bot_sent"], values["rank_points"],
                                             values["history_score_mod"], bool(values["ind"]), values["local_sent"], values["global_sent"], values["sentc"])
        assert actual == legacy


def test_array_matches_scalar_with_missing_indicator_and_boundary_inputs():
    n = 200
    rng = np.random.default_rng(44)
    sides = np.arange(n) % 2 == 0
    top = rng.integers(0, 2, n).astype(bool)
    bottom = rng.integers(0, 2, n).astype(bool)
    rank = rng.choice([0, 5, 10, 20, 25, 30, 40, 50, 80, 90, 95, 100], n)
    history = rng.choice([-2.0, 0.0, 1.0], n)
    has = rng.integers(0, 2, n).astype(bool)
    local = rng.choice([-60, -50, -30, -25, 0, 25, 50, 60], n)
    global_ = rng.choice([-60, -50, -30, -20, 0, 20, 30, 50, 60], n)
    sentiment = rng.choice(["", "NEUTRAL", "EXTREME_BULLISH", "EXTREME_BEARISH", "BEARISH", "BULLISH", "MIXED_BEARISH"], n)
    initial = rng.normal(10, 15, n)
    for side in (True, False):
        expected = np.array([apply_rank_sentiment_scalar(initial[i], side, top[i], bottom[i], rank[i], history[i], has[i], local[i], global_[i], sentiment[i]) for i in range(n)])
        actual = apply_rank_sentiment_vec(initial, side, top, bottom, rank, history, has, local, global_, sentiment)
        np.testing.assert_array_equal(actual, expected)


def test_actual_native_rate_hook_executes_shared_component_with_live_bindings():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "ez_positions_quick.py").read_text())
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate")
    hook = next(node for node in ast.walk(rate) if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "apply_rank_sentiment_scalar")
    imported = next(node for node in ast.walk(rate) if isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_rank_sentiment_score")
    env = {"score": 17.25, "is_long": False, "top_sent": True, "bot_sent": False,
           "ind": {"0ranking_points": 5.0}, "history_score_mod": -1.0, "local_sent": -60.0,
           "global_sent": 10.0, "sentc": "EXTREME_BEARISH", "safe_fetch_float": lambda value, default=0.0: default if value is None else float(value)}
    exec(compile(ast.Module(body=[imported, hook], type_ignores=[]), "native-ASR-rank-hook", "exec"), env)
    expected = apply_rank_sentiment_scalar(17.25, False, True, False, 5.0, -1.0, True, -60.0, 10.0, "EXTREME_BEARISH")
    assert env["score"] == expected
