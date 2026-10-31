import ast
from pathlib import Path

from vec_decisions.asr_dc_position_score import apply_dc_position_score_scalar


def test_actual_rate_dc_adapter_snippet_reads_the_ten_local_channel_values():
    source = Path(__file__).resolve().parents[1] / "ez_positions_quick.py"
    tree = ast.parse(source.read_text())
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    imported = next(node for node in ast.walk(rate) if isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_dc_position_score")
    call_assignment = next(node for node in ast.walk(rate) if isinstance(node, ast.Assign) and "apply_dc_position_score_scalar" in ast.unparse(node))
    values_assignment = next(node for node in ast.walk(rate) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_dc_score_ind" for target in node.targets))
    adapter_nodes = [imported, values_assignment, call_assignment]
    namespace = {"score": 1.0, "reasons": [], "current_price": 115.0, "is_long": True}
    for index, tf in enumerate(("3m", "15m", "1h", "4h", "D")):
        namespace[f"dc_low_{tf}"] = 100.0
        namespace[f"dc_high_{tf}"] = 110.0 + index
    exec(compile(ast.Module(body=adapter_nodes, type_ignores=[]), "live-ASR-DC-call", "exec"), namespace)
    expected, reasons = apply_dc_position_score_scalar(
        1.0, [],
        {f"dc_{edge}_{tf}": namespace[f"dc_{edge}_{tf}"] for tf in ("3m", "15m", "1h", "4h", "D") for edge in ("low", "high")},
        115.0, True,
    )
    assert namespace["score"] == expected
    assert namespace["reasons"] == reasons
