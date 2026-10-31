import ast
from pathlib import Path
import pytest

@pytest.mark.parametrize("price, ratio, tag", [(99.79, 0.004, ""), (99.8, 0.002, "_NEAR_DC1H"), (100.1, 0.002, "_NEAR_DC1H")])
def test_actual_native_dc1h_caution_branch_uses_loaded_scalar(price, ratio, tag):
    tree = ast.parse(Path("ez_positions_quick.py").read_text())
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.If) and "dc_high_1h" in ast.unparse(node.test) and any(isinstance(child, ast.Constant) and child.value == "_NEAR_DC1H" for child in ast.walk(node))]
    node = min(nodes, key=lambda n: len(ast.unparse(n)))
    namespace = {"dc_high_1h": 100.0, "current_price": price, "aug_ratio": 0.004, "caution_tag": ""}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "actual_epq_augment_caution", "exec"), namespace)
    assert namespace["aug_ratio"] == pytest.approx(ratio)
    assert namespace["caution_tag"] == tag
