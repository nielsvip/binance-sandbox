"""2026-10-24 BABA_SHORT 19:58Z DAYTRADE_TARGET dc_4h_low close + evaluate_stop NameError.

1. evaluate_stop crashed with NameError: position_key on every cycle (only that
   bare name in the whole 4000-line function) — everything below it, including
   all stop legs, never evaluated. Pin: no unbound position_key loads remain.
2. DAYTRADE_TARGET has no ENABLE knob (verified against SWITCH_BIBLE: only
   DAYTRADE_DC_TARGET_TF / _BUFFER_PCT / TRADIER_DC_DAYTRADE_ENABLED exist), so
   per USER order the TARGET leg is hard-killed at all three live sites while
   STOPS stay live. Pin the kill markers in source.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _unbound_loads(path, func, name):
    tree = ast.parse((ROOT / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            bound = {a.arg for a in node.args.args} | {"self"}
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    bound.add(n.id)
            return sorted(
                {n.lineno for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id == name and name not in bound}
            )
    raise AssertionError(f"{func} not found in {path}")


def test_evaluate_stop_has_no_bare_position_key():
    assert _unbound_loads("tradier_manage.py", "evaluate_stop", "position_key") == []


def test_daytrade_target_killed_tradier_grey_twin():
    src = (ROOT / "tradier_manage.py").read_text()
    assert len(re.findall(r"_gx_tgt\s*=\s*\[\]", src)) >= 1


def test_daytrade_target_killed_tradier_parity_loop():
    src = (ROOT / "tradier_manage.py").read_text()
    assert len(re.findall(r"_dt_tgt_specs\s*=\s*\[\]", src)) >= 1


def test_daytrade_target_killed_crypto_grey_twin():
    src = (ROOT / "ez_manage.py").read_text()
    assert len(re.findall(r"_gx_tgt\s*=\s*\[\]", src)) >= 1


def test_daytrade_stop_specs_still_resolved():
    for path in ["tradier_manage.py", "ez_manage.py"]:
        src = (ROOT / path).read_text()
        assert "resolve_daytrade_dc" in src
