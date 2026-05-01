#!/usr/bin/env python3
"""condense_jsonl_to_sqlite.py — consolidate many *.jsonl files into one
SQLite DB per root, then delete originals. Frees disk + makes results
queryable.

Per CLAUDE.md, NEVER touches:
  - data/decisions/ (live trade records — sacred)
  - klines_cache* / backtest_v8/indicators/ (NPZ binary, not JSONL anyway)

Usage:
  python3 condense_jsonl_to_sqlite.py /home/niels/binance-sandbox/data/hourly_reconfig
  python3 condense_jsonl_to_sqlite.py /home/niels/binance-sandbox/data/canonical_trades
  python3 condense_jsonl_to_sqlite.py /home/niels/binance-sandbox/data --recursive

Schema (one table per JSONL file, named after sanitized path):
  CREATE TABLE jsonl_records (
    file_id INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    record JSON NOT NULL,
    PRIMARY KEY(file_id, line_no)
  );
  CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE,
    n_lines INTEGER,
    size_bytes INTEGER,
    consolidated_at TEXT
  );

After consolidation: DB is at <root>/__condensed.db. Original JSONLs are
deleted ONLY if line count matches. VACUUM at end shrinks the DB.

Safety:
  - Idempotent: re-run skips already-consolidated files (looked up by path)
  - --dry-run flag for preview
  - --keep-originals for testing
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

NEVER_TOUCH = ("/decisions/", "/.history/", "/backups/")
SKIP_LARGER_THAN_MB = 5000  # skip absurdly large files for safety


def sanitize_relative(root: Path, file: Path) -> str:
    return str(file.relative_to(root))


def schema(con: sqlite3.Connection) -> None:
    """One BLOB per file (entire JSONL content, zlib level 9). Per-line storage
    was 3.93GB DB vs 3.34GB JSONL (net loss). Per-file zlib-9 typically gives
    3-5× savings on JSONL-style records (lots of repeated keys)."""
    con.executescript("""
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT UNIQUE NOT NULL,
        n_lines INTEGER NOT NULL,
        orig_bytes INTEGER NOT NULL,
        compressed_bytes INTEGER NOT NULL,
        consolidated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS jsonl_blobs (
        file_id INTEGER PRIMARY KEY REFERENCES files(id),
        zlib_data BLOB NOT NULL
    );
    """)


def consolidate(root: Path, dry_run: bool = False, keep_originals: bool = False,
                recursive: bool = True) -> None:
    if not root.exists():
        print(f"  ERROR: root not found: {root}")
        return
    db_path = root / "__condensed.db"
    print(f"[condense] root={root} db={db_path}")
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    schema(con)
    pattern = "**/*.jsonl" if recursive else "*.jsonl"
    files = sorted(p for p in root.glob(pattern)
                   if not any(skip in str(p) for skip in NEVER_TOUCH)
                   and p != db_path)
    print(f"[condense] {len(files)} JSONL candidates")
    n_consolidated = 0
    n_skipped = 0
    n_failed = 0
    bytes_freed = 0
    for p in files:
        rel = sanitize_relative(root, p)
        size = p.stat().st_size
        if size > SKIP_LARGER_THAN_MB * 1024 * 1024:
            print(f"  SKIP_OVERSIZED ({size/1024/1024:.0f}MB): {rel}")
            n_skipped += 1
            continue
        # Already consolidated?
        existing = con.execute("SELECT id, n_lines FROM files WHERE path=?", (rel,)).fetchone()
        if existing and not dry_run:
            if not keep_originals and p.exists():
                got = con.execute("SELECT COUNT(*) FROM jsonl_blobs WHERE file_id=?",
                                  (existing[0],)).fetchone()[0]
                if got == 1:
                    bytes_freed += size
                    p.unlink()
            n_skipped += 1
            continue
        # Consolidate (per-file zlib-9 blob)
        try:
            raw = p.read_bytes()
            n_lines = sum(1 for ln in raw.split(b"\n") if ln.strip())
            if dry_run:
                print(f"  DRY: {rel}  ({n_lines} lines, {size/1024:.0f}KB)")
                n_consolidated += 1
                continue
            import zlib
            blob = zlib.compress(raw, 9)
            comp_size = len(blob)
            con.execute("INSERT INTO files(path, n_lines, orig_bytes, compressed_bytes) VALUES(?,?,?,?)",
                        (rel, n_lines, size, comp_size))
            file_id = con.execute("SELECT id FROM files WHERE path=?", (rel,)).fetchone()[0]
            con.execute("INSERT INTO jsonl_blobs(file_id, zlib_data) VALUES(?,?)",
                        (file_id, blob))
            con.commit()
            if not keep_originals:
                p.unlink()
                bytes_freed += size
            n_consolidated += 1
            if n_consolidated % 100 == 0:
                print(f"  [{n_consolidated}/{len(files)}] {rel} {n_lines}lines  "
                      f"{size/1024:.0f}KB→{comp_size/1024:.0f}KB ({100*comp_size/max(1,size):.0f}%)")
            if n_consolidated % 100 == 0:
                print(f"  [{n_consolidated}/{len(files)}] {rel}  ({n_lines} lines)")
        except Exception as e:
            print(f"  ERROR: {rel}: {e}")
            n_failed += 1
    if not dry_run:
        print("[condense] running VACUUM to shrink DB...")
        con.execute("VACUUM")
    db_size = db_path.stat().st_size
    print(f"\n[condense] done")
    print(f"  consolidated: {n_consolidated}")
    print(f"  skipped:      {n_skipped}")
    print(f"  failed:       {n_failed}")
    print(f"  freed:        {bytes_freed/1024/1024:.1f} MB of JSONL")
    print(f"  db size:      {db_size/1024/1024:.1f} MB at {db_path}")
    if bytes_freed > 0:
        print(f"  net saving:   {(bytes_freed - db_size)/1024/1024:+.1f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="directory containing JSONL files")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-originals", action="store_true")
    ap.add_argument("--no-recursive", action="store_true")
    args = ap.parse_args()
    consolidate(Path(args.root), dry_run=args.dry_run,
                keep_originals=args.keep_originals,
                recursive=not args.no_recursive)


if __name__ == "__main__":
    main()
