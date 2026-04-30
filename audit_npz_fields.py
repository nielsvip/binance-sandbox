#!/usr/bin/env python3
"""audit_npz_fields.py — compare v8_quick_engine NPZ accesses to actual NPZ keys.

Per CLAUDE.md NPZ regen rule 7: "NPZ MUST include EVERY param the v8 sweep ever asks for".
Zero-filled missing fields = lying results.

Usage:
    python3 audit_npz_fields.py <engine.py> <npz_path> [<engine2.py> ...]

Reports:
- READS_MISSING: fields the engine reads via _safe() that NPZ doesn't have → zero-fill = LIE
- NPZ_UNUSED:   fields NPZ has that the engine never reads → backtest could use, quick ignores

Exits 1 if any READS_MISSING. Use as pre-sweep gate.
"""
import re
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"_safe\s*\(\s*\w+\s*,\s*['\"]([a-zA-Z][a-zA-Z0-9_]*)['\"]"),
    re.compile(r"\bnpz\s*\[\s*['\"]([a-zA-Z][a-zA-Z0-9_]*)['\"]\s*\]"),
    re.compile(r"\bnpz\.get\s*\(\s*['\"]([a-zA-Z][a-zA-Z0-9_]*)['\"]"),
    re.compile(r"['\"]([a-zA-Z][a-zA-Z0-9_]*)['\"]\s+in\s+npz"),
    re.compile(r"store\.get\s*\(\s*['\"]([a-zA-Z][a-zA-Z0-9_]*)['\"]"),
    re.compile(r"store\.arrays\s*\[\s*['\"]([a-zA-Z][a-zA-Z0-9_]*)['\"]\s*\]"),
    re.compile(r"['\"]([a-zA-Z][a-zA-Z0-9_]*)['\"]\s+in\s+store\.arrays"),
]
TEMPLATE_PATTERN = re.compile(r"_safe\s*\(\s*\w+\s*,\s*f['\"]([a-zA-Z_][^'\"]*\{[^}]+\}[^'\"]*)['\"]")
TFS = ["3m", "5m", "15m", "1h", "4h", "D", "W", "M"]


def extract_fields(source: str) -> set:
    fields = set()
    for pat in PATTERNS:
        for m in pat.finditer(source):
            fields.add(m.group(1))
    # Expand f-string templates against all TFs
    for m in TEMPLATE_PATTERN.finditer(source):
        tmpl = m.group(1)
        for tf in TFS:
            fields.add(re.sub(r"\{[^}]+\}", tf, tmpl))
    return fields


def get_npz_keys(npz_path: str) -> set:
    import numpy as np
    d = np.load(npz_path, allow_pickle=True)
    keys = set(d.files)
    d.close()
    return keys


def main():
    if len(sys.argv) < 3:
        print(f"usage: {sys.argv[0]} <engine.py> <npz_path> [<engine2.py> ...]", file=sys.stderr)
        sys.exit(2)
    npz_path = sys.argv[2]
    engines = [sys.argv[1]] + list(sys.argv[3:])
    npz_keys = get_npz_keys(npz_path)
    print(f"NPZ {npz_path}: {len(npz_keys)} keys\n")
    any_missing = False
    for ep in engines:
        src = Path(ep).read_text()
        reads = extract_fields(src)
        missing = sorted(reads - npz_keys)
        unused = sorted(npz_keys - reads)
        print(f"=== {ep} ===")
        print(f"  reads: {len(reads)} fields (after template expansion)")
        print(f"  READS_MISSING ({len(missing)}) — zero-fill = LIE:")
        for f in missing:
            print(f"    {f}")
        print(f"  NPZ_UNUSED ({len(unused)}) — first 20:")
        for f in unused[:20]:
            print(f"    {f}")
        if len(unused) > 20:
            print(f"    ... and {len(unused)-20} more")
        print()
        if missing:
            any_missing = True
    if any_missing:
        print("RESULT: REJECT — fields missing from NPZ. Add to precompute or remove from engine.")
        sys.exit(1)
    print("RESULT: ALL FIELDS PRESENT ✓")


if __name__ == "__main__":
    main()
