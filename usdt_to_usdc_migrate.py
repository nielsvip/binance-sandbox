#!/usr/bin/env python3
"""usdt_to_usdc_migrate.py — mass-rename USDT references → USDC for the 10
USDC-perp majors per CLAUDE.md USDC-OVER-USDT policy.

Targets:
  - File names in data/sweep_results/, data/autonomous/, /tmp/v8_trades/
  - 'symbol' / 'sym' / 'symbols' / 'symbols_long_list' / 'symbols_short_list'
    columns inside CSVs
  - JSONL trade records: rewrites the "symbol": "BTCUSDT" → "BTCUSDC" in place

NEVER touches:
  - data/decisions/ (live trade records, real-time history)
  - backups/ (frozen history, intentional)
  - .history/ (CLAUDE.md prohibition)
  - klines_cache* (already cleaned separately)
  - backtest_v8/indicators/ (NPZ, already renamed)

Usage:
  python3 usdt_to_usdc_migrate.py [--dry-run]   # preview
  python3 usdt_to_usdc_migrate.py               # execute
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import sys
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
TRADES = Path("/tmp/v8_trades")

# 10 USDC-perp majors. USDT label MUST be rewritten to USDC.
MAJORS = ["BTC", "ETH", "SOL", "ADA", "BNB", "AVAX", "XRP", "LINK", "LTC", "UNI"]
SUB_MAP = {f"{m}USDT": f"{m}USDC" for m in MAJORS}
PATTERN = re.compile(r"\b(" + "|".join(re.escape(k) for k in SUB_MAP) + r")\b")

EXCLUDE_DIRS = {"backups", ".history", "backtest_v8", "klines_cache",
                "klines_cache_backtest", "klines_cache_gateway",
                "decisions", "node_modules", ".git", "old", "data/decisions"}


def excluded(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & EXCLUDE_DIRS)


def rename_files(roots, dry_run: bool):
    """Rename files whose name contains 'BTCUSDT' etc. → 'BTCUSDC' etc."""
    n_renamed = 0
    for root in roots:
        if not root.exists(): continue
        for p in root.rglob("*"):
            if p.is_dir() or excluded(p):
                continue
            new_name = PATTERN.sub(lambda m: SUB_MAP[m.group(1)], p.name)
            if new_name == p.name:
                continue
            new_path = p.with_name(new_name)
            if new_path.exists():
                print(f"  COLLISION: {p.name} → {new_name} already exists, skipping")
                continue
            if dry_run:
                print(f"  would rename: {p} → {new_path.name}")
            else:
                p.rename(new_path)
                print(f"  renamed: {p.name} → {new_path.name}")
            n_renamed += 1
    return n_renamed


def rewrite_csvs(roots, dry_run: bool):
    """In-place rewrite of CSV cells containing USDT-major refs."""
    n_csv = 0
    n_rows = 0
    for root in roots:
        if not root.exists(): continue
        for p in root.rglob("*.csv"):
            if excluded(p):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if not PATTERN.search(text):
                continue
            new_text = PATTERN.sub(lambda m: SUB_MAP[m.group(1)], text)
            n_changed = sum(1 for a, b in zip(text.splitlines(), new_text.splitlines()) if a != b)
            n_csv += 1
            n_rows += n_changed
            if dry_run:
                print(f"  would rewrite: {p}  ({n_changed} rows changed)")
            else:
                p.write_text(new_text, encoding="utf-8")
                print(f"  rewrote: {p}  ({n_changed} rows)")
    return n_csv, n_rows


def rewrite_jsonls(roots, dry_run: bool):
    """In-place rewrite of JSONL files — substitutes USDT-major refs in any
    line containing 'BTCUSDT' etc. Most reliable for /tmp/v8_trades/ records."""
    n_jsonl = 0
    n_lines = 0
    for root in roots:
        if not root.exists(): continue
        for p in root.rglob("*.jsonl"):
            if excluded(p):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if not PATTERN.search(text):
                continue
            new_text = PATTERN.sub(lambda m: SUB_MAP[m.group(1)], text)
            n_changed = sum(1 for a, b in zip(text.splitlines(), new_text.splitlines()) if a != b)
            n_jsonl += 1
            n_lines += n_changed
            if dry_run:
                print(f"  would rewrite: {p}  ({n_changed} lines)")
            else:
                p.write_text(new_text, encoding="utf-8")
                print(f"  rewrote: {p}  ({n_changed} lines)")
    return n_jsonl, n_lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="preview, don't change files")
    args = ap.parse_args()
    print(f"[migrate] mode={'DRY-RUN' if args.dry_run else 'EXECUTE'}")
    print(f"[migrate] mapping: {SUB_MAP}")
    print()
    roots_files = [REPO / "data" / "sweep_results", REPO / "data" / "autonomous", TRADES]
    print(f"=== Step 1: rename files ===")
    n_files = rename_files(roots_files, args.dry_run)
    print(f"  total file renames: {n_files}\n")

    print(f"=== Step 2: rewrite CSV cell contents ===")
    n_csv, n_rows = rewrite_csvs(roots_files, args.dry_run)
    print(f"  CSVs touched: {n_csv}, rows changed: {n_rows}\n")

    print(f"=== Step 3: rewrite JSONL records ===")
    n_jsonl, n_lines = rewrite_jsonls(roots_files, args.dry_run)
    print(f"  JSONLs touched: {n_jsonl}, lines changed: {n_lines}\n")

    print(f"[migrate] {'PREVIEW COMPLETE' if args.dry_run else 'COMPLETE'}")


if __name__ == "__main__":
    main()
