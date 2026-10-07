"""ENDGAME _t-shadow regression: _endgame_filter_cycle calls _t.time() (module
`import time as _t`) — any statement-level `_t` binding (loop var, assign) in
that scope makes EVERY call raise UnboundLocalError before doing anything
(fleet-wide dead ENDGAME 2026-10-07: "[ENDGAME-warn] outer cannot access
local variable '_t'"). Pure AST, no openpyxl, fast."""
import ast
import pathlib


def _func(path, name):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path}")


def test_endgame_no_t_shadow():
    pilot = pathlib.Path(__file__).resolve().parents[1] / "v15_pilot.py"
    fn = _func(pilot, "_endgame_filter_cycle")
    stores = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.comprehension):
            continue
        for child in ast.iter_child_nodes(node):
            pass
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == "_t":
            # comprehension targets are scoped, harmless — only flag statement bindings
            stores.add(node.lineno)
    # comprehension internals: walk them separately and forgive
    for node in ast.walk(fn):
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and n.id == "_t" and isinstance(n.ctx, ast.Store):
                    stores.discard(n.lineno)
    loads = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == "_t" and isinstance(n.ctx, ast.Load)]
    assert loads, "expected _t.time() uses in _endgame_filter_cycle"
    assert not stores, f"_t statement bindings shadow time alias at lines {sorted(stores)}"
