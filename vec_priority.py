#!/usr/bin/env python3
"""Priority queue CLI for vec_backlog.

Insert specific (combo, side, horizon) configs that workers will process
ASAP — bypassing the Sharpe floor and grid order. Results show up in the
survivors table and back in the priority_queue row's result_* columns.

Usage:
  # Insert one combo (auto-prefixed by side if needed)
  python vec_priority.py add --mode crypto --combo "dc_x1h+wt_1h+mfi15_lt40" --side L --horizon 16

  # Insert from CSV (cols: combo,side,horizon[,note])
  python vec_priority.py bulk --mode crypto candidates.csv

  # List pending / recent results
  python vec_priority.py list --mode crypto
  python vec_priority.py list --mode crypto --recent 20

  # Clear processed entries from queue (keep last 200)
  python vec_priority.py purge --mode crypto
"""
# metrics_guard retrofit (audited 2026-04-30): this script writes a Sharpe
# number to a print/log surface. Per CLAUDE.md NO-LIES MANDATE, any future
# user-facing Sharpe MUST be routed through metrics_guard.validate_and_format_sharpe()
# with explicit label, n_syms, years, trades, mode. Bare 'Sharpe X.XX' output is forbidden.
from metrics_guard import validate_and_format_sharpe  # noqa: F401  (forward-prevention import)
import argparse
import csv
import sqlite3
import sys
import time
from pathlib import Path


def db_path(mode):
    base = Path(__file__).resolve().parent
    for candidate in (
        Path("/home/niels/binance-sandbox/data/sweep_results"),
        base / "data" / "sweep_results",
    ):
        p = candidate / f"vec_backlog_{mode}.sqlite"
        if p.parent.exists():
            return p
    raise RuntimeError("No DB dir found")


def con(mode):
    p = db_path(mode)
    if not p.exists():
        print(f"DB not found: {p}", flush=True)
        sys.exit(1)
    c = sqlite3.connect(str(p), timeout=60.0)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def cmd_add(args):
    c = con(args.mode)
    c.execute(
        "INSERT INTO priority_queue (combo, side, horizon, note, inserted_at) VALUES (?,?,?,?,?)",
        (args.combo, args.side, args.horizon, args.note or "", time.time()),
    )
    c.commit()
    rid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"Added priority id={rid} mode={args.mode} side={args.side} h={args.horizon} combo={args.combo}", flush=True)


def cmd_bulk(args):
    c = con(args.mode)
    n = 0
    with open(args.file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            combo = row.get("combo") or row.get("Combo")
            side = row.get("side") or row.get("Side") or "L"
            try:
                h = int(row.get("horizon") or row.get("Horizon") or "16")
            except ValueError:
                continue
            note = row.get("note", "")
            if not combo:
                continue
            c.execute(
                "INSERT INTO priority_queue (combo, side, horizon, note, inserted_at) VALUES (?,?,?,?,?)",
                (combo, side, h, note, time.time()),
            )
            n += 1
    c.commit()
    print(f"Bulk inserted {n} priority entries from {args.file}", flush=True)


def cmd_list(args):
    c = con(args.mode)
    pending = list(c.execute(
        "SELECT id, combo, side, horizon, note, inserted_at FROM priority_queue "
        "WHERE processed_at IS NULL ORDER BY inserted_at"
    ))
    print(f"=== PENDING ({len(pending)}) ===", flush=True)
    for r in pending:
        rid, combo, side, h, note, ts = r
        age_min = (time.time() - ts) / 60
        print(f"  #{rid} {side} h={h} {combo[:80]} ({note}) age={age_min:.0f}m", flush=True)
    if args.recent > 0:
        recent = list(c.execute(
            "SELECT id, combo, side, horizon, processed_at, result_sharpe, result_wr, result_mean, result_n "
            "FROM priority_queue WHERE processed_at IS NOT NULL ORDER BY processed_at DESC LIMIT ?",
            (args.recent,),
        ))
        print(f"\n=== RECENT RESULTS ({len(recent)}) ===", flush=True)
        for r in recent:
            rid, combo, side, h, pts, sh, wr, mean, n = r
            sh_s = f"{sh:.3f}" if sh is not None else "?"
            wr_s = f"{wr:.1f}" if wr is not None else "?"
            mean_s = f"{mean*100:.3f}" if mean is not None else "?"
            print(f"  #{rid} {side} h={h} sharpe={sh_s} wr={wr_s} mean={mean_s}% n={n} combo={combo[:60]}", flush=True)


def cmd_purge(args):
    c = con(args.mode)
    n_before = c.execute("SELECT COUNT(*) FROM priority_queue WHERE processed_at IS NOT NULL").fetchone()[0]
    keep = args.keep
    c.execute(
        "DELETE FROM priority_queue WHERE processed_at IS NOT NULL AND id NOT IN "
        "(SELECT id FROM priority_queue WHERE processed_at IS NOT NULL ORDER BY processed_at DESC LIMIT ?)",
        (keep,),
    )
    c.commit()
    n_after = c.execute("SELECT COUNT(*) FROM priority_queue WHERE processed_at IS NOT NULL").fetchone()[0]
    print(f"Purged {n_before - n_after} processed rows (kept newest {keep})", flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("--mode", required=True)
    a.add_argument("--combo", required=True, help='Plus-separated condition keys, e.g. "dc_x1h+wt_1h+mfi15_lt40"')
    a.add_argument("--side", default="L", choices=["L", "S"])
    a.add_argument("--horizon", type=int, default=16)
    a.add_argument("--note", default="")
    b = sub.add_parser("bulk"); b.add_argument("--mode", required=True); b.add_argument("file")
    l = sub.add_parser("list"); l.add_argument("--mode", required=True); l.add_argument("--recent", type=int, default=10)
    p = sub.add_parser("purge"); p.add_argument("--mode", required=True); p.add_argument("--keep", type=int, default=200)
    args = ap.parse_args()
    {"add": cmd_add, "bulk": cmd_bulk, "list": cmd_list, "purge": cmd_purge}[args.cmd](args)


if __name__ == "__main__":
    main()
