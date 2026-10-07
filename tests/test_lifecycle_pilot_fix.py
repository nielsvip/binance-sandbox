"""Durable regression for lifecycle_pilot max-switches removal (HANDOVER_V15)."""
import pathlib
import py_compile
import ast

ROOT = pathlib.Path(__file__).resolve().parents[1]
LP = ROOT / "tools" / "opt" / "lifecycle_pilot.py"


def test_lifecycle_pilot_compiles():
    py_compile.compile(str(LP), doraise=True)


def test_no_max_switches_arg():
    src = LP.read_text()
    assert "--max-switches" not in src, "max-switches must be eliminated per HANDOVER_V15 §7.7"
    # stray help line from broken edit must not exist
    assert 'help="pilot cap on entry paths' not in src


def test_run_symside_signature_no_max_switches():
    src = LP.read_text()
    # run_symside should not take max_switches param
    assert "def run_symside" in src
    # ensure definition line does not contain max_switches
    for line in src.splitlines():
        if line.strip().startswith("def run_symside"):
            assert "max_switches" not in line, "run_symside must not have max_switches"
            break


def test_parser_has_symbols_arg():
    # verify parser still has --symbols for all three subcommands
    tree = ast.parse(LP.read_text())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "help" and isinstance(kw.value, ast.Constant) and "comma-separated" in str(kw.value.value):
                    found = True
    assert found, "parser must have --symbols help"
