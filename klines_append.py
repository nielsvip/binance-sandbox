#!/usr/bin/env python
"""Append fresh klines from klines_cache/ (primary) and klines_cache_gateway/ (backup)
into klines_cache_backtest/ (4yr canonical, used by precompute and sweeps).

Idempotent (timestamp-deduped). Safe to cron at any cadence.

Source preference per symbol/TF:
  1. klines_cache/{name}.json          — primary live cache (fresh)
  2. klines_cache_gateway/{name}.json  — backup (only fills gaps)

Target: klines_cache_backtest/{name}.json — appends fresh bars; creates file if missing.

Both stores use the same JSON-list-of-dicts format with ISO 8601 timestamps.
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
PRIMARY = REPO / "klines_cache"
BACKUP = REPO / "klines_cache_gateway"
TARGET = REPO / "klines_cache_backtest"
SYMBOLS_JSON = REPO / "symbols.json"
VALID_TFS = ("1m", "3m", "15m", "1h", "4h", "D", "W", "M")


def load_json_list(p: Path) -> list[dict]:
    if not p.exists() or p.stat().st_size == 0:
        return []
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, list) else []
    except Exception:
        return []


def merge_klines(existing: list[dict], fresh: list[dict]) -> tuple[list[dict], int]:
    """Append entries from `fresh` whose timestamp is not already in `existing`.
    Sort by timestamp. Returns (merged, n_added)."""
    if not fresh:
        return existing, 0
    seen = {b.get("timestamp") for b in existing if b.get("timestamp")}
    n_added = 0
    out = list(existing)
    for b in fresh:
        ts = b.get("timestamp")
        if not ts or ts in seen:
            continue
        out.append(b)
        seen.add(ts)
        n_added += 1
    if n_added:
        out.sort(key=lambda b: b.get("timestamp", ""))
    return out, n_added


def gather_fresh(name: str) -> list[dict]:
    """Read primary; fill any timestamp gaps from backup."""
    primary = load_json_list(PRIMARY / name)
    backup = load_json_list(BACKUP / name)
    if not primary:
        return backup
    if not backup:
        return primary
    seen = {b.get("timestamp") for b in primary if b.get("timestamp")}
    extra = [b for b in backup if b.get("timestamp") and b["timestamp"] not in seen]
    if not extra:
        return primary
    merged = primary + extra
    merged.sort(key=lambda b: b.get("timestamp", ""))
    return merged


def filenames_to_process(symbols: list[str] | None) -> list[str]:
    if symbols:
        roots = set(symbols)
    else:
        roots = None
    seen: set[str] = set()
    for d in (PRIMARY, BACKUP):
        if not d.exists():
            continue
        for p in d.glob("*_*.json"):
            name = p.name
            stem = name[:-5]  # strip .json
            if "_" not in stem:
                continue
            sym, tf = stem.rsplit("_", 1)
            if tf not in VALID_TFS:
                continue
            if roots is not None and sym not in roots:
                continue
            seen.add(name)
    return sorted(seen)


def append_one(name: str, dry_run: bool = False) -> dict:
    fresh = gather_fresh(name)
    if not fresh:
        return {"file": name, "status": "no_fresh", "n_added": 0}
    target = TARGET / name
    existed = target.exists()
    existing = load_json_list(target)
    merged, n_added = merge_klines(existing, fresh)
    if n_added == 0:
        return {"file": name, "status": "up_to_date", "n_added": 0,
                "total": len(merged), "created": False}
    status = "appended" if existed else "created"
    if dry_run:
        return {"file": name, "status": f"dry:{status}", "n_added": n_added,
                "total": len(merged), "created": not existed}
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged))
    tmp.replace(target)
    return {"file": name, "status": status, "n_added": n_added,
            "total": len(merged), "created": not existed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="Comma-separated. Empty = all from primary+backup ∩ symbols.json")
    ap.add_argument("--all", action="store_true", help="Process every klines file (ignore symbols.json filter)")
    ap.add_argument("--limit", type=int, default=0, help="Process at most N files (debugging)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not TARGET.exists():
        sys.exit(f"target dir does not exist: {TARGET}")
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.all:
        syms = None
    else:
        syms = json.loads(SYMBOLS_JSON.read_text())
    files = filenames_to_process(syms)
    if args.limit:
        files = files[: args.limit]
    print(f"[append] {len(files)} files to process | dry_run={args.dry_run}")
    t0 = time.time()
    stats = {"appended": 0, "created": 0, "up_to_date": 0, "no_fresh": 0,
             "dry:appended": 0, "dry:created": 0}
    new_total = 0
    for i, name in enumerate(files, 1):
        r = append_one(name, dry_run=args.dry_run)
        stats[r["status"]] = stats.get(r["status"], 0) + 1
        new_total += r.get("n_added", 0)
        if args.verbose or r["status"] in ("created", "dry:created") or (i % 200 == 0):
            print(f"  [{i}/{len(files)}] {name}: {r['status']} +{r['n_added']} total={r.get('total', 0)}")
    elapsed = time.time() - t0
    print(f"[append] DONE in {elapsed:.1f}s | stats={stats} | bars_added={new_total}")


if __name__ == "__main__":
    main()
