import ast
from pathlib import Path

from vec_decisions.asr_channel_confluence_score import apply_channel_confluence_scalar


def test_actual_rate_channel_adapter_executes_the_native_local_values():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "ez_positions_quick.py").read_text())
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    imported = next(node for node in ast.walk(rate) if isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_channel_confluence_score")
    values_node = next(node for node in ast.walk(rate) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_asr_channel_values" for target in node.targets))
    score_node = next(node for node in ast.walk(rate) if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "apply_channel_confluence_scalar")
    namespace = {"score": 1.25, "is_long": True, "current_price": 110.0, "ind": {"dc_high_1m": 105, "dc_low_1m": 95}, "safe_fetch_float": lambda value, default: default if value is None else float(value)}
    namespace.update({"k_3m": 10.0, "d_3m": 20.0, "k_15m": 15.0, "k_1h": 20.0, "d_1h": 25.0, "k_4h": 50.0, "d_4h": 50.0,
                      "dc_basis_1h": 100.0, "dc_basis_15m": 99.0, "dc_basis_4h": 105.0, "sco1h": True, "scu1h": False, "sco15m": True, "scu15m": False,
                      "dc_high_3m": 108.0, "dc_low_3m": 90.0, "dc_high_15m": 111.0, "dc_low_15m": 90.0,
                      "dc_high_1h": 120.0, "dc_low_1h": 90.0, "dc_high_4h": 130.0, "dc_low_4h": 90.0})
    exec(compile(ast.Module(body=[imported, values_node, score_node], type_ignores=[]), "live-ASR-channel-call", "exec"), namespace)
    assert namespace["score"] == apply_channel_confluence_scalar(1.25, namespace["_asr_channel_values"], True)
