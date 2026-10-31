import ast
import random
import textwrap
from pathlib import Path

import numpy as np

from vec_decisions.asr_history_score import history_score_array, history_score_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_history_score_ez_positions_quick_20261031T064427Z_1793429067542544000.py"


def _source():
    lines = ORIGINAL.read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line == "            history_score_mod = 0.0")
    end = next(index for index in range(start, len(lines)) if "from vec_decisions.asr_channel_confluence_score import" in lines[index])
    return textwrap.dedent("\n".join(lines[start:end]))


def _safe(value, default):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _native_eval(tracker_data, is_exit):
    scope = {"tracker_data": tracker_data, "is_exit": is_exit, "safe_fetch_float": _safe, "reasons": []}
    exec(compile(_source(), str(ORIGINAL), "exec"), {}, scope)
    return scope["history_score_mod"], scope["reasons"]


def test_exact_native_history_modifier_matches_scalar():
    rng = random.Random(711400)
    records = [None, {}, {"win_rate_%": None, "total_trades": "bad", "total_realized_pnl_$": None}]
    records.extend(
        {
            "win_rate_%": rng.choice([0, 30, 31, 69, 70, 100, None]),
            "total_trades": rng.choice([0, 2, 3, 10, "bad"]),
            "total_realized_pnl_$": rng.choice([-100, -50, 0, 50, 100, None]),
        }
        for _ in range(500)
    )
    for record in records:
        for is_exit in (False, True):
            assert history_score_scalar(record, is_exit, _safe) == _native_eval(record, is_exit)


def test_array_history_modifier_matches_native_scalar_from_explicit_causal_inputs():
    rng = random.Random(711401)
    present = rng.choices([False, True], k=500)
    win = [rng.choice([0, 30, 31, 69, 70, 100]) for _ in present]
    trades = [rng.choice([0, 2, 3, 10]) for _ in present]
    pnl = [rng.choice([-100, -50, 0, 50, 100]) for _ in present]
    score, masks = history_score_array(present, win, trades, pnl)
    native = [
        _native_eval({"win_rate_%": w, "total_trades": t, "total_realized_pnl_$": p} if has else None, False)[0]
        for has, w, t, p in zip(present, win, trades, pnl)
    ]
    assert np.array_equal(score, np.asarray(native))
    assert np.array_equal(masks["HighWinRate"], np.asarray([has and t >= 3 and w >= 70 for has, w, t in zip(present, win, trades)]))


def test_native_rate_calls_shared_history_helper_before_channel_confluence():
    source = (ROOT / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    history = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_asr_history_score_scalar"]
    channel = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "apply_channel_confluence_scalar"]
    assert len(history) == 1 and len(channel) == 1
    assert history[0].lineno < channel[0].lineno
