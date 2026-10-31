"""A 7D comparison arm can never endanger 30D trading (USER 2026-10-31).

"7D is just a comparison experiment make sure it can never put in danger any of the existing 30D trading". fin shares
the crypto trader process with men; this pins that every 30D account is planned first and that a 7D arm's exception is
contained to that arm instead of aborting the pass (which would skip the consumed file the bar guarantee reads and get
30D sides declared untradeable and closed).
"""
import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools" / "vector_direct_trader.py"
spec = importlib.util.spec_from_file_location("vdt_containment", SRC)
M = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = M
spec.loader.exec_module(M)


def _run_once():
    tree = ast.parse(SRC.read_text())
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_once")


def _account_loop():
    return next(n for n in ast.walk(_run_once()) if isinstance(n, ast.For)
                and isinstance(n.target, ast.Name) and n.target.id == "acct"
                and isinstance(n.iter, ast.Name) and n.iter.id == "ordered")


def test_the_arms_are_exactly_fin_and_trc():
    assert M.SEVENDAY_ARMS == frozenset({"fin", "trc"})
    assert not (M.SEVENDAY_ARMS & {"men", "ang", "flz", "inf", "tra", "trb"}), "no 30D account may ever be an arm"


def test_the_whole_account_body_is_one_try():
    loop = _account_loop()
    assert len(loop.body) == 1 and isinstance(loop.body[0], ast.Try), "every account's work must sit inside the try"


def test_a_30d_accounts_exception_still_propagates_exactly_as_before():
    handler = _account_loop().body[0].handlers[0]
    first = handler.body[0]
    assert isinstance(first, ast.If) and isinstance(first.body[0], ast.Raise), "non-arm failures must re-raise unchanged"
    test = ast.unparse(first.test)
    assert "SEVENDAY_ARMS" in test and "not in" in test


def test_a_7d_arms_exception_is_logged_and_alerted_never_raised():
    handler = _account_loop().body[0].handlers[0]
    body = ast.unparse(ast.Module(body=handler.body[1:], type_ignores=[]))
    assert "CRITICAL 7D arm" in body and "SEVENDAY_ARM_CONTAINED" in body
    assert not any(isinstance(n, ast.Raise) for n in ast.walk(ast.Module(body=handler.body[1:], type_ignores=[])))


def test_every_30d_account_is_planned_before_any_arm_even_when_an_arm_is_biggest():
    active = ["fin", "men", "ang", "trc", "flz", "inf"]
    notional = {"fin": 9e9, "trc": 8e9, "men": 5, "ang": 4, "flz": 3, "inf": 2}
    ordered = sorted(active, key=lambda a: -notional.get(a, 0.0))
    ordered = [a for a in ordered if a not in M.SEVENDAY_ARMS] + [a for a in ordered if a in M.SEVENDAY_ARMS]
    assert ordered[:4] == ["men", "ang", "flz", "inf"] and set(ordered[4:]) == {"fin", "trc"}


def test_the_loop_really_iterates_the_reordered_list():
    src = ast.unparse(_run_once())
    assert "ordered = [a for a in ordered if a not in SEVENDAY_ARMS] + [a for a in ordered if a in SEVENDAY_ARMS]" in src


def test_the_s1_live_build_carries_the_same_containment():
    """S1 was patched in place (its own build + this change only), so the same structure must hold there."""
    import subprocess
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "s1-pub",
                        "cat ~/binance/tools/vector_direct_trader.py"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout:
        import pytest
        pytest.skip("S1 not reachable from this test run")
    tree = ast.parse(r.stdout)
    run_once = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_once")
    loop = next(n for n in ast.walk(run_once) if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "ordered")
    assert isinstance(loop.body[0], ast.Try)
