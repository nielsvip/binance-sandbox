import ast
import random
import textwrap
from pathlib import Path

import numpy as np

from vec_decisions.asr_dc_structure_score import dc_structure_votes_array, dc_structure_votes_scalar


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_dc_structure_votes_ez_positions_quick_20261031T062918Z_1793428158252830000.py"


def _native_vote_source(is_long):
    lines = ORIGINAL.read_text().splitlines()
    anchor = next(index for index, line in enumerate(lines) if 'dc_low_15m_ant = safe_fetch_float(i.get("dc_low_15m_ant")' in line)
    if is_long:
        start = next(index for index in range(anchor, len(lines)) if lines[index] == "            if is_long:")
        stop = next(index for index in range(start + 1, len(lines)) if lines[index] == "                if (")
        while stop + 1 < len(lines) and "current_price >= dc_high_4h" not in lines[stop + 1]:
            stop += 1
        return textwrap.dedent("\n".join(lines[start + 1:stop]))
    start = next(index for index in range(anchor, len(lines)) if lines[index] == "            elif not is_long:")
    stop = next(index for index in range(start + 1, len(lines)) if "current_price <= dc_low_4h" in lines[index])
    while lines[stop] != "                if (":
        stop -= 1
    return textwrap.dedent("\n".join(lines[start + 1:stop]))


def _source_eval(values, is_long):
    score = 0.0
    reasons = []
    valid_setup = False
    scope = dict(values, is_long=is_long, score=score, reasons=reasons, valid_setup=valid_setup)
    exec(compile(_native_vote_source(is_long), str(ORIGINAL), "exec"), {}, scope)
    return scope["score"], scope["reasons"], scope["valid_setup"]


def _values(rng):
    result = {}
    for tf in ("3m", "15m", "1h", "4h"):
        low_ant = rng.uniform(-100, 100)
        high_ant = rng.uniform(-100, 100)
        result[f"dc_low_{tf}_ant"] = low_ant
        result[f"dc_high_{tf}_ant"] = high_ant
        result[f"dc_low_{tf}"] = low_ant + rng.choice((-2.0, -1.0, 0.0, 1.0, 2.0))
        result[f"dc_high_{tf}"] = high_ant + rng.choice((-2.0, -1.0, 0.0, 1.0, 2.0))
    return result


def test_native_original_dc_votes_match_shared_scalar_exactly():
    rng = random.Random(603151)
    for is_long in (True, False):
        for _ in range(500):
            values = _values(rng)
            expected = _source_eval(values, is_long)
            actual = dc_structure_votes_scalar(values, is_long)
            assert actual == expected


def test_numpy_votes_match_native_scalar_per_bar_and_side():
    rng = random.Random(814202)
    for is_long in (True, False):
        rows = [_values(rng) for _ in range(257)]
        arrays = {key: np.array([row[key] for row in rows], dtype=float) for key in rows[0]}
        score, valid = dc_structure_votes_array(arrays, is_long)
        expected = [dc_structure_votes_scalar(row, is_long) for row in rows]
        assert np.array_equal(score, np.array([item[0] for item in expected]))
        assert np.array_equal(valid, np.array([item[2] for item in expected]))


def test_native_rate_calls_shared_helper_and_applies_outputs_in_order():
    source = (ROOT / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_dc_structure_votes_scalar"]
    assert len(calls) == 1
    call_line = calls[0].lineno
    window = "\n".join(source.splitlines()[call_line - 3:call_line + 6])
    assert "score += _dc_vote_delta" in window
    assert "reasons.extend(_dc_vote_reasons)" in window
    assert "valid_setup = True" in window
