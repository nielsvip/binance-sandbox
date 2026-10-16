#!/usr/bin/env python3
"""quickconfig_dedupe — one declaration per QuickConfig field, typed like live (USER 2026-10-16: "duplicate
contradictory fields in TEMPLATE_ or v12_quick_engine should be impossible").

Python dataclasses silently let a later `NAME: T = v` replace an earlier one, so 133 fields carried two
declarations and the later "auto-added TEMPLATE generic" line (often `float`) re-typed bool/str/TF switches.
That wrong annotation is what override coercion and published boards used.

Rules (behaviour-preserving at defaults):
  * keep the LAST declaration (the one Python actually used), delete every earlier one;
  * re-type the kept one from the live config (config.Config instance, else TradierConfig):
      live bool + value in {0,1,0.0,1.0}  -> bool(value)
      live int  + integral float          -> int(value)
      live float + int                    -> float(value)
      value is a str                      -> str
      otherwise                           -> type(value)  (value itself never changes meaning)
  * the same re-typing applies to single declarations whose annotation contradicts their own literal.
Usage: tools/quickconfig_dedupe.py [--apply]   (dry-run prints the plan; --apply rewrites v12_quick_engine.py)
"""
import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "v12_quick_engine.py"


def _decls(src):
    t = ast.parse(src)
    cls = [n for n in t.body if isinstance(n, ast.ClassDef) and n.name == "QuickConfig"][0]
    out = {}
    for n in cls.body:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
            out.setdefault(n.target.id, []).append(n)
    return out


def _lit(n):
    try:
        return True, ast.literal_eval(n.value)
    except Exception:
        return False, None


def _target_type(value, live):
    if isinstance(live, bool) and not isinstance(value, bool) and isinstance(value, (int, float)) and value in (0, 1):
        return bool, bool(value)
    if isinstance(live, int) and not isinstance(live, bool) and isinstance(value, float) and value.is_integer():
        return int, int(value)
    if isinstance(live, float) and isinstance(value, int) and not isinstance(value, bool):
        return float, float(value)
    return type(value), value


def plan(src):
    import config
    import config_tradier

    C = config.Config()
    T = config_tradier.TradierConfig()
    edits, report = [], []
    for k, ns in _decls(src).items():
        keep = ns[-1]
        for n in ns[:-1]:
            edits.append(("delete", n.lineno, n.end_lineno, None))
        ok, v = _lit(keep)
        ann = ast.unparse(keep.annotation)
        if "ClassVar" in ann:
            continue
        if not ok or isinstance(v, (list, dict, tuple, set)) or v is None:
            if len(ns) > 1:
                report.append((k, "dedup-only (non-literal/container kept)", ann, repr(v)[:40]))
            continue
        live = getattr(C, k, getattr(T, k, None))
        typ, nv = _target_type(v, live)
        new_ann = typ.__name__
        if new_ann != ann or repr(nv) != repr(v):
            edits.append(("retype", keep, new_ann, nv))
            report.append((k, f"{ann}={v!r} -> {new_ann}={nv!r}", f"live={live!r}"[:60], f"decls={len(ns)}"))
        elif len(ns) > 1:
            report.append((k, "dedup", ann, repr(v)[:40]))
    return edits, report


def apply(src, edits):
    lines = src.split("\n")
    # splice right-to-left so earlier offsets stay valid
    spans = []
    for e in edits:
        if e[0] == "delete":
            spans.append((e[1] - 1, 0, e[2] - 1, None, "delete"))
        else:
            n, new_ann, nv = e[1], e[2], e[3]
            spans.append((n.value.lineno - 1, n.value.col_offset, n.value.end_lineno - 1, (n.value.end_col_offset, repr(nv)), "value"))
            spans.append((n.annotation.lineno - 1, n.annotation.col_offset, n.annotation.end_lineno - 1, (n.annotation.end_col_offset, new_ann), "ann"))
    for sl, sc, el, payload, kind in sorted(spans, key=lambda s: (s[0], s[1]), reverse=True):
        if kind == "delete":
            del lines[sl:el + 1]
            continue
        ec, text = payload
        head = lines[sl][:sc]
        tail = lines[el][ec:]
        lines[sl:el + 1] = [head + text + tail]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    src = ENGINE.read_text()
    edits, report = plan(src)
    for r in report:
        print(" | ".join(str(x) for x in r))
    print(f"[plan] deletes={sum(1 for e in edits if e[0] == 'delete')} retypes={sum(1 for e in edits if e[0] == 'retype')}")
    if not a.apply:
        print("dry-run — re-run with --apply")
        return
    new = apply(src, edits)
    compile(new, str(ENGINE), "exec")
    left = {k: v for k, v in _decls(new).items() if len(v) > 1}
    if left:
        sys.exit(f"REFUSED: still duplicated after splice: {list(left)[:10]}")
    ENGINE.write_text(new)
    print("[applied]", ENGINE)


if __name__ == "__main__":
    main()
