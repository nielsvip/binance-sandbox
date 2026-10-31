import ast
from pathlib import Path


def test_native_rate_wires_shared_score_prefix_only_for_entries():
    tree = ast.parse(Path("ez_positions_quick.py").read_text())
    rate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "rate"
        and node.lineno < 4000
    )
    calls = []

    class Visitor(ast.NodeVisitor):
        def visit_If(self, node):
            if isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not):
                if isinstance(node.test.operand, ast.Name) and node.test.operand.id == "is_exit":
                    for child in node.body:
                        if isinstance(child, ast.ImportFrom):
                            if child.module == "vec_decisions.asr_score_core":
                                self_outer.imported = True
                        if isinstance(child, ast.Assign):
                            if any(isinstance(t, ast.Name) and t.id == "score" for target in child.targets for t in ast.walk(target)):
                                calls.append(ast.unparse(child.value))
            self.generic_visit(node)

    self_outer = type("State", (), {"imported": False})()
    Visitor().visit(rate)
    assert self_outer.imported
    assert any("apply_score_prefix_scalar" in call for call in calls)
