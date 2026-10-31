import ast
import random
import textwrap
from pathlib import Path

import numpy as np

from vec_decisions.asr_stoch_entry_score import stoch_entry_score_array, stoch_entry_score_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_stoch_entry_score_ez_positions_quick_20261031T065016Z_1793429416180824000.py"


def _source():
    lines = ORIGINAL.read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line_matches_stoch(line))
    end = next(index for index in range(start, len(lines)) if "# MTS GATE — side-aware:" in lines[index])
    return textwrap.dedent("\n".join(lines[start:end]))


def line_matches_stoch(line):
    return line == "        # LONG: WT bullish on scalp + not deeply overbought, OR deep oversold reversal"


def _values(rng):
    return {
        "wt_bullish_scalp": rng.choice([False, True]),
        "wt_score_scalp": rng.choice([-40.0, -30.0, -20.0, -10.0, 0.0]),
        "wt_velocity_scalp": rng.choice([-1.1, -1.0, -0.9, 0.0, 0.9, 1.0, 1.1]),
        "wt_cross_bull_scalp": rng.choice([False, True]),
        "wt_momentum_state_1h": rng.choice(["", "EXHAUST_UP", "EXHAUST_DOWN"]),
        "k_15m": rng.choice([30.0, 32.0, 33.0, 70.0, 71.0, 80.0]),
        "k_15m_prev": rng.choice([30.0, 32.0, 33.0, 70.0, 71.0, 80.0]),
        "k_3m": rng.choice([20.0, 50.0, 80.0]),
        "d_3m": rng.choice([20.0, 50.0, 80.0]),
    }


def _native(values, is_long, is_exit):
    scope = dict(values, is_long=is_long, is_exit=is_exit, score=0.0, reasons=[])
    scope["_wt_mom_1h"] = values["wt_momentum_state_1h"]
    exec(compile(_source(), str(ORIGINAL), "exec"), {}, scope)
    return scope["score"], scope["reasons"], scope["is_long_stoch_ok"], scope["is_short_stoch_ok"]


def test_exact_native_stochastic_validity_and_penalty():
    rng = random.Random(581223)
    for side in (True, False):
        for is_exit in (False, True):
            for _ in range(600):
                values = _values(rng)
                assert stoch_entry_score_scalar(values, side, is_exit) == _native(values, side, is_exit)


def test_numpy_stochastic_masks_and_score_match_native_scalar_bars():
    rng = random.Random(581224)
    for side in (True, False):
        for is_exit in (False, True):
            rows = [_values(rng) for _ in range(300)]
            arrays = {key: np.asarray([row[key] for row in rows], dtype=bool if key in ("wt_bullish_scalp", "wt_cross_bull_scalp") else object if key == "wt_momentum_state_1h" else float) for key in rows[0]}
            score, long_ok, short_ok = stoch_entry_score_array(arrays, side, is_exit)
            native = [_native(row, side, is_exit) for row in rows]
            assert np.array_equal(score, np.asarray([item[0] for item in native]))
            assert np.array_equal(long_ok, np.asarray([item[2] for item in native]))
            assert np.array_equal(short_ok, np.asarray([item[3] for item in native]))


def test_native_rate_hook_precedes_mts_gate_and_preserves_shared_flags():
    source = (ROOT / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_asr_stoch_entry_score_scalar")
    lines = source.splitlines()
    mts_gate = next(index for index, line in enumerate(lines, start=1) if "_mts_gate_tradier = bool" in line)
    assert call.lineno < mts_gate
    assert "is_long_stoch_ok" in lines[call.lineno - 1]
