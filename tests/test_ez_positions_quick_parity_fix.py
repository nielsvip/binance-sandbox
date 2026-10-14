"""Regression for parity fixes: datetime shadowing and indicators alias."""
import ast
from pathlib import Path

def test_process_single_exit_no_datetime_store():
    src = Path("ez_positions_quick.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "process_single_exit":
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and n.id == "datetime" and isinstance(n.ctx, ast.Store):
                    raise AssertionError(f"datetime is still stored locally at line {n.lineno} — shadowing top-level import, will cause UnboundLocalError at earlier use")
            break
    else:
        raise AssertionError("process_single_exit not found")

def test_rate_has_indicators_alias():
    src = Path("ez_positions_quick.py").read_text()
    # rate's param is ind, but body must alias indicators = ind before first use of indicators
    assert "def rate(" in src and "indicators = ind" in src, "rate must alias indicators = ind to avoid NameError for indicators.get"
    # rate must never bind `datetime` locally (an inner `from datetime import datetime` shadowed the module import -> UnboundLocalError).
    # 2026-10-14: checked on the AST — the renamed import is multi-line (`from datetime import (\n datetime as _dt_inner,`).
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "rate":
            for n in ast.walk(node):
                assert not (isinstance(n, ast.Name) and n.id == "datetime" and isinstance(n.ctx, ast.Store)), f"datetime stored in rate at line {n.lineno}"
                if isinstance(n, ast.ImportFrom) and n.module == "datetime":
                    assert all(a.name != "datetime" or a.asname not in (None, "datetime") for a in n.names), f"rate shadows datetime at line {n.lineno}"
            break
    else:
        raise AssertionError("rate not found")

def test_backtest_golden_rule_no_missing_import():
    src = Path("backtest_v12_engine.py").read_text()
    assert "_batch3_lifecycle_window" not in src, "backtest_v12_engine must not import missing _batch3_lifecycle_window"
    # should directly use _golden_store.arrays
    assert "_golden_arrays = _golden_store.arrays" in src

    src15 = Path("backtest_v15_engine.py").read_text()
    assert "_batch3_lifecycle_window" not in src15
