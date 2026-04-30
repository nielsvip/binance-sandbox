#!/usr/bin/env python3
"""remediate_lying_csvs.py — operate on data/_lie_audit/categorization.json

Buckets:
  A_RECOMPUTABLE   — has trade-list JSONL sibling: rewrite CSV with real pool_sharpe
  B_UNVERIFIABLE   — no trade list anywhere: prepend # [UNVERIFIED ...] header, move
                     to data/_legacy_unverified/
  C_LEGACY_DELETE  — archive/ subdir OR > 30d old: move to
                     data/_legacy_unverified/archive/ keeping subpath
  D_ACTIVE_KEEP    — referenced by active scripts: print to
                     data/_lie_audit/active_to_retrofit.txt; DO NOT mutate

Usage:
    python remediate_lying_csvs.py --bucket {A,B,C,D,all} [--apply]

By default --dry-run is True. --apply must be explicit.

Hard constraints:
    * /tmp/BACKTEST_HOLD must exist (caller writes it).
    * Originals backed up to data/_lie_audit/originals/ before any rewrite.
    * Files in data/sweep_results/ are NEVER auto-mutated by this tool — they
      become D_ACTIVE_KEEP.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
AUDIT_DIR = REPO / "data" / "_lie_audit"
CAT_PATH = AUDIT_DIR / "categorization.json"
ORIGINALS_DIR = AUDIT_DIR / "originals"
LEGACY_UNVERIFIED = REPO / "data" / "_legacy_unverified"
RECOMPUTE_LOG = AUDIT_DIR / "recompute_log.jsonl"
ACTIVE_RETROFIT = AUDIT_DIR / "active_to_retrofit.txt"
HOLD_FILE = Path("/tmp/BACKTEST_HOLD")

sys.path.insert(0, str(REPO))
import metrics_guard  # noqa: E402


def ensure_hold():
    if not HOLD_FILE.exists():
        sys.stderr.write(f"REFUSED: {HOLD_FILE} missing — caller must set hold\n")
        sys.exit(2)


def backup_original(csv_path: Path):
    """Copy CSV under originals/ preserving relative path under repo root."""
    try:
        rel = csv_path.relative_to(REPO)
    except ValueError:
        rel = Path(csv_path.name)
    dst = ORIGINALS_DIR / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(csv_path, dst)
    return dst


def log_recompute(rec: dict):
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    with RECOMPUTE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


# ---------- BUCKET A: recompute from sibling JSONL --------------------------

def remediate_A(records, apply: bool):
    actions = []
    for rec in records:
        csv_path = Path(rec["path"])
        sib = Path(rec["sibling_jsonl"])
        if not csv_path.exists() or not sib.exists():
            actions.append({"path": str(csv_path), "skip": "missing"})
            continue
        result = metrics_guard.recompute_pool_sharpe_from_jsonl(sib)
        if not result.get("recovered"):
            actions.append({"path": str(csv_path), "skip": "no_recover", "result": result})
            continue
        # Read original CSV
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            rd = csv.DictReader(f)
            cols = rd.fieldnames or []
            rows = list(rd)
        # Rename Sharpe-bearing columns to *_legacy_lie
        col_map = {}
        for c in cols:
            if "sharpe" in c.lower() and c not in ("sym_sharpe", "pool_sharpe", "sharpe_per_trade", "sharpe_pt"):
                col_map[c] = c + "_legacy_lie"
        new_cols = [col_map.get(c, c) for c in cols]
        # Compute the canonical metrics from result
        # NOTE: at module level we lack a years/n_syms ground truth — derive from JSONL
        # n_syms / n_trades from result; years is approximate if present in records
        years = float(rec.get("years") or 1.0)
        n_syms = int(result.get("n_syms") or 0)
        n_trades = int(result.get("n_trades") or 0)
        # Pool stats
        pool = float(result.get("pool_sharpe") or 0.0)
        sym = float(result.get("sym_sharpe") or 0.0)
        # Append canonical columns at end of header
        canon = {
            "pool_sharpe": pool,
            "sym_sharpe": sym,
            "trades": n_trades,
            "n_syms": n_syms,
            "years": years,
        }
        for ck in canon:
            if ck not in new_cols:
                new_cols.append(ck)
        action = {
            "path": str(csv_path),
            "rename": col_map,
            "added_canonical": canon,
            "trades": n_trades,
            "n_syms": n_syms,
        }
        actions.append(action)
        if apply:
            backup_original(csv_path)
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=new_cols)
                w.writeheader()
                # Each row gets canonical pool_sharpe (single CSV-wide value).
                for row in rows:
                    new_row = {col_map.get(k, k): v for k, v in row.items()}
                    new_row.update({k: canon[k] for k in canon})
                    w.writerow(new_row)
            log_recompute({
                "ts": time.time(),
                "csv": str(csv_path),
                "sibling": str(sib),
                "old_columns": cols,
                "new_columns": new_cols,
                "pool_sharpe": pool,
                "sym_sharpe": sym,
                "trades": n_trades,
                "n_syms": n_syms,
            })
    return actions


# ---------- BUCKET B: tag + move to _legacy_unverified ----------------------

def remediate_B(records, apply: bool):
    actions = []
    LEGACY_UNVERIFIED.mkdir(parents=True, exist_ok=True)
    header_comment = "# [UNVERIFIED -- no trade list, Sharpe values unrecoverable]\n"
    for rec in records:
        csv_path = Path(rec["path"])
        if not csv_path.exists():
            actions.append({"path": str(csv_path), "skip": "missing"})
            continue
        try:
            rel = csv_path.relative_to(REPO)
        except ValueError:
            rel = Path(csv_path.name)
        dst = LEGACY_UNVERIFIED / rel
        actions.append({"path": str(csv_path), "dst": str(dst), "tag": True})
        if apply:
            backup_original(csv_path)
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Read original, prepend tag, write to dst, then unlink original
            with csv_path.open("rb") as f:
                content = f.read()
            with dst.open("wb") as f:
                f.write(header_comment.encode("utf-8"))
                f.write(content)
            csv_path.unlink()
    return actions


# ---------- BUCKET C: move to legacy archive --------------------------------

def remediate_C(records, apply: bool):
    actions = []
    LEGACY_UNVERIFIED.mkdir(parents=True, exist_ok=True)
    for rec in records:
        csv_path = Path(rec["path"])
        if not csv_path.exists():
            actions.append({"path": str(csv_path), "skip": "missing"})
            continue
        try:
            rel = csv_path.relative_to(REPO)
        except ValueError:
            rel = Path(csv_path.name)
        dst = LEGACY_UNVERIFIED / "archive" / rel
        actions.append({"path": str(csv_path), "dst": str(dst), "age_days": rec.get("age_days")})
        if apply:
            backup_original(csv_path)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(csv_path), str(dst))
    return actions


# ---------- BUCKET D: print manual retrofit list ----------------------------

def remediate_D(records, apply: bool):
    """D never mutates files. Always writes the retrofit txt."""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Files in active dirs that claim Sharpe without canonical column.",
             "# Manual retrofit required — DO NOT auto-modify.",
             f"# Generated {time.strftime('%Y-%m-%d %H:%M:%S')}",
             ""]
    actions = []
    for rec in records:
        csv_path = Path(rec["path"])
        line = f"{csv_path}  reason={rec.get('reason')}  verdict={rec.get('verdict')}"
        lines.append(line)
        actions.append({"path": str(csv_path), "manual": True})
    if apply:
        ACTIVE_RETROFIT.write_text("\n".join(lines) + "\n")
    return actions


# ---------- main -----------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True, choices=["A", "B", "C", "D", "all"])
    ap.add_argument("--apply", action="store_true",
                    help="Actually mutate files. Default is dry-run.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N records per bucket (0 = no limit).")
    args = ap.parse_args()
    if args.apply:
        ensure_hold()
    if not CAT_PATH.exists():
        sys.stderr.write(f"Missing {CAT_PATH}\n")
        sys.exit(2)
    cat = json.loads(CAT_PATH.read_text())
    handlers = {
        "A": ("A_RECOMPUTABLE", remediate_A),
        "B": ("B_UNVERIFIABLE", remediate_B),
        "C": ("C_LEGACY_DELETE", remediate_C),
        "D": ("D_ACTIVE_KEEP", remediate_D),
    }
    targets = list(handlers.keys()) if args.bucket == "all" else [args.bucket]
    summary = {"dry_run": not args.apply, "buckets": {}}
    for b in targets:
        key, fn = handlers[b]
        records = cat.get(key, [])
        if args.limit:
            records = records[:args.limit]
        actions = fn(records, args.apply)
        summary["buckets"][b] = {
            "key": key,
            "n_records": len(records),
            "n_actions": len(actions),
            "sample": actions[:5],
        }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
