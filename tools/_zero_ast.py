"""AST helpers for tools/v15_zero_audit.py: which config names are READ in REACHABLE vectorized code."""
import ast, collections, glob, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _early_return(fn: ast.AST) -> bool:
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant) and isinstance(s.value.value, str))]
    for i, s in enumerate(body):
        if isinstance(s, ast.Return) and i < len(body) - 1:
            return True
    return False


def scan(paths):
    """{name: {'live': [(file,line,func)], 'dead': [(file,line,func)]}} for every identifier/str/attr appearing in code."""
    out = collections.defaultdict(lambda: {"live": [], "dead": [], "stub": []})
    for p in paths:
        try:
            tree = ast.parse(pathlib.Path(p).read_text())
        except SyntaxError:
            continue
        def _is_stub_stmt(st):
            # `_ = getattr(...)` / `_ = 1  # X` / `if getattr(cfg, X): _ = 1` — the audit-defeating no-op reads (BIBLE §19)
            if isinstance(st, ast.Assign) and all(isinstance(t, ast.Name) and t.id == "_" for t in st.targets):
                return True
            if isinstance(st, ast.If) and st.body and all(isinstance(b, ast.Assign) and all(isinstance(t, ast.Name) and t.id == "_" for t in b.targets) for b in st.body) and not st.orelse:
                return True
            return False

        def visit(node, fname, dead, stub=False):
            for ch in ast.iter_child_nodes(node):
                if not isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(ch, ast.stmt) and _is_stub_stmt(ch):
                    visit(ch, fname, dead, True)
                    continue
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(ch, ch.name, dead or _early_return(ch) or ("batch" in ch.name.lower() and "wiring" in ch.name.lower()), False)
                    continue
                name = None
                if isinstance(ch, ast.Attribute):
                    name = ch.attr
                elif isinstance(ch, ast.Constant) and isinstance(ch.value, str) and ch.value.isupper():
                    name = ch.value
                elif isinstance(ch, ast.Name) and ch.id.isupper():
                    name = ch.id
                if name and len(name) > 3:
                    out[name]["dead" if dead else ("stub" if stub else "live")].append((str(p), getattr(ch, "lineno", 0), fname))
                visit(ch, fname, dead, stub)
        visit(tree, "<module>", False)
    return out
