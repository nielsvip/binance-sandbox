#!/usr/bin/env python3
"""klines_master — one complete .klines.json.gz per symbol from every repo copy.

Reads (read-only): klines_cache/*.json, klines_cache/gateway/, klines_cache/tradier/,
klines_cache_macbook/ (feed buffer, unioned by timestamp), data/klines_*_hist_w2/.
Writes: OUT/{SYM}.klines.json.gz = {tf: [native rows sorted+deduped by UTC stamp]} + manifest.jsonl.
Append-only mindset: union everything, drop nothing, never touch sources.
Memory: two passes — index first, then one symbol at a time (never whole repo in RAM).
"""
import gzip
import json
import os
import sys
from pathlib import Path

REPO = Path(os.path.expanduser("~/binance-sandbox"))
SOURCES = [
    REPO / "klines_cache",
    REPO / "klines_cache" / "gateway",
    REPO / "klines_cache" / "tradier",
    REPO / "klines_cache_macbook",
    REPO / "data" / "klines_3m_hist_w2",
    REPO / "data" / "klines_5m_hist_w2",
]
SKIP_DIRS = {"backups", "consolidated_klines"}


def key_of(row):
    if isinstance(row, dict):
        return str(row.get("timestamp"))
    if isinstance(row, (list, tuple)) and row:
        return str(row[0])
    return None


def split_sym_tf(stem):
    if "_" in stem:
        sym, tf = stem.rsplit("_", 1)
        if sym:
            return sym, tf
    return stem, "bars"


def index_files():
    index = {}
    seen = set()
    n_files = 0
    for src in SOURCES:
        if not src.is_dir():
            continue
        for root, dirs, files in os.walk(src, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in files:
                if not fn.endswith(".json"):
                    continue
                p = Path(root) / fn
                rp = p.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                sym, tf = split_sym_tf(p.stem)
                index.setdefault(sym, []).append((tf, p))
                n_files += 1
    return index, n_files


def build_one(sym, entries, out):
    bucket = {}
    n_bad = 0
    for tf, p in entries:
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            n_bad += 1
            continue
        if not isinstance(data, list):
            n_bad += 1
            continue
        store = bucket.setdefault(tf, {})
        for row in data:
            k = key_of(row)
            if k and k != "None":
                store[k] = row
    doc, info = {}, {}
    for tf in sorted(bucket):
        rows = [bucket[tf][k] for k in sorted(bucket[tf])]
        doc[tf] = rows
        info[tf] = {"rows": len(rows)}
        if rows:
            info[tf]["first"] = key_of(rows[0])
            info[tf]["last"] = key_of(rows[-1])
    with gzip.open(out / f"{sym}.klines.json.gz", "wt", encoding="utf-8", compresslevel=6) as gz:
        json.dump(doc, gz, separators=(",", ":"))
    return info, n_bad


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    index, n_files = index_files()
    bad_total = 0
    with open(out / "manifest.jsonl", "w", encoding="utf-8") as mf:
        for i, sym in enumerate(sorted(index)):
            info, n_bad = build_one(sym, index[sym], out)
            bad_total += n_bad
            mf.write(json.dumps({"sym": sym, "tfs": info}) + "\n")
            mf.flush()
            if (i + 1) % 100 == 0:
                print(f"MASTER {i + 1}/{len(index)} syms", flush=True)
    print(f"MASTER DONE files={n_files} bad={n_bad} syms={len(index)} out={out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(REPO / ".." / "klines_master"))
