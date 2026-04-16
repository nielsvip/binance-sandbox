#!/usr/bin/env python3
"""Config switch usage audit — finds dead/orphan config knobs.

For every top-level attribute in config.py and config_tradier.py, count
references in the live code surface (excluding the config files themselves
and old/). Buckets:
  DEAD          — 0 references anywhere
  CONFIG_ONLY   — only referenced in sweep_/backtest_ files (no live impact)
  LIVE          — referenced in ez_*.py / tradier_*.py / wt_dc_delta.py / service files
  DOC_ONLY      — only in .md / comments (should be DEAD)

Usage:  python3 config_usage_audit.py [--csv audit.csv] [--only dead|live|all]
"""
import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
CONFIG_FILES = ["config.py", "config_tradier.py"]
# Live-impact surface
LIVE_PATTERNS = (
    "ez_manage.py", "ez_positions_quick.py", "ez_positions_service.py",
    "ez_indicators.py", "ez_market_data.py", "ez_rankings.py", "ez_prices.py",
    "tradier_manage.py", "tradier_indicators.py", "tradier_positions.py",
    "tradier_prices.py", "tradier_rankings.py", "tradier_api.py",
    "wt_dc_delta.py", "utils.py",
)
SWEEP_PATTERNS = ("backtest_", "sweep_", "v8_", "rolling_", "adaptive_")
EXCLUDE_DIRS = ("old", "backups", ".git", "__pycache__", "memory")


def extract_config_attrs(path: Path) -> list[tuple[str, str, int]]:
    """Return list of (attr_name, default_value_repr, lineno) for every class-level annotation."""
    src = path.read_text()
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    name = item.target.id
                    val_repr = ast.unparse(item.value) if item.value else "<no-default>"
                    out.append((name, val_repr, item.lineno))
    return out


_PY_FILE_CACHE: dict[str, str] = {}


def _load_py_sources() -> dict[str, str]:
    """Load all .py files once (excluding EXCLUDE_DIRS and config files). Cached."""
    if _PY_FILE_CACHE:
        return _PY_FILE_CACHE
    for p in BASE.rglob("*.py"):
        rel = p.relative_to(BASE)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if p.name in CONFIG_FILES:
            continue
        try:
            _PY_FILE_CACHE[str(p)] = p.read_text(errors="replace")
        except Exception:
            pass
    return _PY_FILE_CACHE


_ALL_HITS: dict[str, list[tuple[str, int]]] = {}


def _build_all_hits(attr_names: set[str]):
    """Single-pass: scan every .py file once, record which attrs appear."""
    if _ALL_HITS:
        return
    files = _load_py_sources()
    word_re = re.compile(r"\b([A-Z_][A-Z0-9_]+)\b")
    for attr in attr_names:
        _ALL_HITS[attr] = []
    for path, src in files.items():
        for i, line in enumerate(src.splitlines(), 1):
            for m in word_re.finditer(line):
                w = m.group(1)
                if w in attr_names:
                    _ALL_HITS.setdefault(w, []).append((path, i))


def grep_references(name: str) -> list[tuple[str, int]]:
    return _ALL_HITS.get(name, [])


def bucket(hits: list[tuple[str, int]]) -> str:
    if not hits:
        return "DEAD"
    has_live = any(any(lp in fp for lp in LIVE_PATTERNS) for fp, _ in hits)
    has_sweep = any(any(sp in Path(fp).name for sp in SWEEP_PATTERNS) for fp, _ in hits)
    if has_live:
        return "LIVE"
    if has_sweep:
        return "CONFIG_ONLY"
    return "DOC_ONLY"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="")
    ap.add_argument("--only", choices=["dead", "config_only", "live", "doc_only", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0, help="limit N attrs (debug)")
    args = ap.parse_args()

    all_attr_names: set[str] = set()
    for cf in CONFIG_FILES:
        path = BASE / cf
        if path.exists():
            for name, _, _ in extract_config_attrs(path):
                all_attr_names.add(name)
    print(f"Building single-pass index for {len(all_attr_names)} attrs across {len(_load_py_sources())} source files...", flush=True)
    _build_all_hits(all_attr_names)
    print(f"Index built. Scanning...", flush=True)

    rows = []
    for cf in CONFIG_FILES:
        path = BASE / cf
        if not path.exists():
            continue
        attrs = extract_config_attrs(path)
        for i, (name, default, ln) in enumerate(attrs):
            if args.limit and i >= args.limit:
                break
            hits = grep_references(name)
            b = bucket(hits)
            if args.only != "all" and args.only.upper() != b:
                continue
            rows.append({
                "config_file": cf,
                "line": ln,
                "name": name,
                "default": default,
                "bucket": b,
                "n_refs": len(hits),
                "top_files": sorted({Path(fp).name for fp, _ in hits})[:5],
            })
            print(f"{b:12s} {cf}:{ln:<5d} {name:48s} refs={len(hits):<4d} default={default[:30]}")

    counts = defaultdict(int)
    for r in rows:
        counts[r["bucket"]] += 1
    print("\n=== SUMMARY ===")
    for b, n in sorted(counts.items()):
        print(f"  {b:12s} {n}")

    if args.csv:
        import csv
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["config_file", "line", "name", "default", "bucket", "n_refs", "top_files"])
            w.writeheader()
            for r in rows:
                r = dict(r)
                r["top_files"] = ",".join(r["top_files"])
                w.writerow(r)
        print(f"\nWrote {args.csv}")


if __name__ == "__main__":
    main()
