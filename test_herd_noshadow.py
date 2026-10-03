"""Regression: main() in tools/v15_local_herd.py must not bind module-global names.

2026-10-03 outage: `import glob, json` inside main() made `json` function-local, so the
ACTIVE-ONLY block's `json.load` raised UnboundLocalError — swallowed by the inner
`except: continue` (silent 150-unfiltered) in the filter, fatal in the debug print
outside try (traceback -> herd death -> S1 idle). Any binding (assign/import/for/except)
of a module-global name inside main() recreates the trap for all earlier reads.
"""
import ast
from pathlib import Path

HERD = Path(__file__).resolve().parent / "tools" / "v15_local_herd.py"


def _module_imports(tree):
    names = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                names.add(a.asname or a.name)
    return names


def _direct_bound(fn):
    bound = set()
    for a in list(fn.args.args) + list(fn.args.kwonlyargs):
        bound.add(a.arg)
    if fn.args.vararg:
        bound.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        bound.add(fn.args.kwarg.arg)

    def visit(n):
        for ch in ast.iter_child_nodes(n):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                if not isinstance(ch, ast.Lambda):
                    bound.add(ch.name)
                for d in ch.decorator_list:
                    visit(d)
                continue  # nested bodies have their own scope
            if isinstance(ch, ast.Name) and isinstance(ch.ctx, (ast.Store, ast.Del)):
                bound.add(ch.id)
            elif isinstance(ch, ast.Import):
                for a in ch.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(ch, ast.ImportFrom):
                for a in ch.names:
                    bound.add(a.asname or a.name)
            elif isinstance(ch, ast.ExceptHandler) and ch.name:
                bound.add(ch.name)
            elif isinstance(ch, ast.NamedExpr) and isinstance(ch.target, ast.Name):
                bound.add(ch.target.id)
            visit(ch)

    visit(fn)
    return bound


def _shadow_violations(path):
    tree = ast.parse(Path(path).read_text())
    mains = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert len(mains) == 1, f"expected 1 main(), found {len(mains)}"
    return sorted(_module_imports(tree) & _direct_bound(mains[0]))


def test_main_binds_no_module_global():
    assert _shadow_violations(HERD) == []


def test_active_only_block_present():
    src = HERD.read_text()
    assert "V15_ACTIVE_ONLY" in src
    assert "_jsact" in src
