import ast
from pathlib import Path
from types import SimpleNamespace

from vec_decisions.asr_late_score import apply_asr_late_score_scalar


def test_actual_rate_late_adapter_executes_with_native_locals():
    source = Path(__file__).resolve().parents[1] / "ez_positions_quick.py"
    tree = ast.parse(source.read_text())
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    imported = next(node for node in ast.walk(rate) if isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_late_score")
    values_assignment = next(node for node in ast.walk(rate) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_dc_score_ind" for target in node.targets))
    call_assignment = next(node for node in ast.walk(rate) if isinstance(node, ast.Assign) and "apply_asr_late_score_scalar" in ast.unparse(node))
    namespace = {"score": 15.0, "reasons": [], "ind": {"rsi_2_1h": 50}, "current_price": 115.0, "is_long": True, "config": SimpleNamespace(), "gain": 3.5, "is_exit": False, "k_3m": 10.0, "d_3m": 20.0, "k_3m_prev": 15.0, "k_15m": 50.0}
    for index, tf in enumerate(("3m", "15m", "1h", "4h", "D")):
        namespace[f"dc_low_{tf}"] = 100.0
        namespace[f"dc_high_{tf}"] = 110.0 + index
    exec(compile(ast.Module(body=[imported, values_assignment, call_assignment], type_ignores=[]), "live-ASR-late-call", "exec"), namespace)
    expected, reasons = apply_asr_late_score_scalar(15.0, [], namespace["ind"], 115.0, True, namespace["config"], 3.5, namespace["_dc_score_ind"], 10.0, 20.0, 15.0, 50.0, False)
    assert namespace["score"] == expected
    assert namespace["reasons"] == reasons
