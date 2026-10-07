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
    # ensure the inner datetime import was renamed
    assert "from datetime import datetime as _dt_inner" in src, "inner datetime import must be renamed to _dt_inner to avoid shadowing"

def test_backtest_golden_rule_no_missing_import():
    src = Path("backtest_v12_engine.py").read_text()
    assert "_batch3_lifecycle_window" not in src, "backtest_v12_engine must not import missing _batch3_lifecycle_window"
    # should directly use _golden_store.arrays
    assert "_golden_arrays = _golden_store.arrays" in src

    src15 = Path("backtest_v15_engine.py").read_text()
    assert "_batch3_lifecycle_window" not in src15
