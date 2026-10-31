import ast
import random
import textwrap
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from vec_decisions.asr_mts_entry_gate import mts_entry_gate_array, mts_entry_gate_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_mts_entry_gate_ez_positions_quick_20261031T065124Z_1793429484990006000.py"


def _source():
    lines = ORIGINAL.read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line == '            _mts_gate_tradier = bool(getattr(config, "MTS_GATE_ENABLED_TRADIER", False))')
    end = next(index for index in range(start, len(lines)) if "from vec_decisions.asr_mts_mi_score import" in lines[index])
    return textwrap.dedent("\n".join(lines[start:end]))


def _config(rng):
    return SimpleNamespace(
        MTS_BOTTOM_MIN_TRADIER=rng.choice([0.0, 5.0, 10.0]),
        MTS_ENTRY_QUALITY_MIN_TRADIER=rng.choice([0.0, 3.0, 5.0]),
        MTS_BOTTOM_MIN=rng.choice([5.0, 10.0, 15.0]),
        MTS_ENTRY_QUALITY_MIN=rng.choice([0.0, 5.0, 10.0]),
        MTS_BOTTOM_MIN_SHORT=rng.choice([0.0, 5.0, 10.0]),
        MTS_ENTRY_QUALITY_MIN_SHORT=rng.choice([0.0, 3.0, 5.0]),
    )


def _native(bottom, quality, side, tradier, generic, cfg):
    scope = {"_mts_bottom": bottom, "_mts_eq": quality, "is_long": side, "config": cfg, "account_key": None}
    code = "def native():\n" + textwrap.indent(_source(), "    ")
    exec(compile(code, str(ORIGINAL), "exec"), scope, scope)
    result = scope["native"]()
    return result[2] if result else None


def test_exact_original_mts_gate_thresholds_and_precedence():
    rng = random.Random(609221)
    for side in (True, False):
        for tradier in (False, True):
            for generic in (False, True):
                for _ in range(400):
                    cfg = _config(rng)
                    bottom = rng.choice([0.0, 4.9, 5.0, 9.9, 10.0, 25.0])
                    quality = rng.choice([0.0, 2.9, 3.0, 4.9, 5.0, 10.0])
                    expected = _native(bottom, quality, side, tradier, generic, cfg)
                    actual = mts_entry_gate_scalar(bottom, quality, side, tradier, generic, cfg)
                    assert actual == expected


def test_numpy_mts_gate_matches_scalar_and_switch_precedence():
    rng = random.Random(609222)
    bottom = np.asarray([rng.uniform(0, 30) for _ in range(700)])
    quality = np.asarray([rng.uniform(0, 30) for _ in range(700)])
    for side in (True, False):
        for tradier in (False, True):
            for generic in (False, True):
                cfg = _config(rng)
                blocked, masks = mts_entry_gate_array(bottom, quality, side, tradier, generic, cfg)
                expected = np.asarray([mts_entry_gate_scalar(b, q, side, tradier, generic, cfg) is not None for b, q in zip(bottom, quality)])
                assert np.array_equal(blocked, expected)


def test_native_rate_returns_exact_wait_reason_from_shared_mts_gate():
    source = (ROOT / "ez_positions_quick.py").read_text()
    tree = ast.parse(source)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_asr_mts_entry_gate_scalar")
    returns = [node for node in ast.walk(tree) if isinstance(node, ast.Return) and node.lineno > call.lineno and "_asr_mts_wait_reason" in ast.unparse(node.value)]
    assert len(returns) == 1
    assert ast.unparse(returns[0].value) == "(0, 'WAIT', _asr_mts_wait_reason)"
