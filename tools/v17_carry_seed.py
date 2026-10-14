#!/usr/bin/env python3
"""Seed V17 CARRY books (~/v15_carry/<SYM_SIDE>.json) from the append-only delta logs (USER 2026-10-14 "99% time savings").

Boards are split by a >1 h gap between 'naked' entries (boards with <300 naked rows are ignored). For every main-pass row
key (sheet|switch|cand) the book stores [evaluated-dead streak counted back from the newest board, 0 carried boards];
last_at = the newest board's last naked entry. Real evals only (same records the pilot reads); an existing book is never
overwritten (the pilot owns it after its first board). Usage: v17_carry_seed.py [--pdir DIR] [--apply]
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ts(x):
    return dt.datetime.strptime(x[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()


def boards_from_log(path, gap_s=3600.0, min_rows=300):
    boards, cur, last = [], None, None
    for line in open(path, errors="replace"):
        if '"naked"' not in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("label") != "naked" or r.get("delta") is None or not r.get("ts"):
            continue
        t = _ts(r["ts"])
        if last is None or t - last > gap_s:
            cur = {"rows": {}, "last": t}
            boards.append(cur)
        last = t
        cur["last"] = t
        cur["rows"][f"{str(r.get('sheet')).strip()}|{str(r.get('switch')).strip()}|{str(r.get('cand')).strip()}"] = float(r["delta"])
    return [b for b in boards if len(b["rows"]) >= min_rows]


def book_from_boards(boards, eps=1e-6):
    rows = {}
    keys = set().union(*[set(b["rows"]) for b in boards]) if boards else set()
    for k in keys:
        streak = 0
        for b in reversed(boards):
            d = b["rows"].get(k)
            if d is None or abs(d) >= eps:
                break
            streak += 1
        rows[k] = [streak, 0]
    return {"rows": rows, "start_fp": "", "last_at": boards[-1]["last"] if boards else 0.0, "boards": len(boards), "seeded_from": "delta_log"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdir", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    pdir = a.pdir or open(os.path.expanduser("~/v15_current_progress_dir.txt")).read().strip()
    out = Path(os.path.expanduser(os.environ.get("V15_V17_CARRY_DIR", "~/v15_carry")))
    n_w = n_skip = n_dead = 0
    for f in sorted(glob.glob(os.path.join(pdir, "v15_delta_log", "*_jump.jsonl"))):
        ss = os.path.basename(f)[: -len("_jump.jsonl")]
        bp = out / f"{ss}.json"
        if bp.exists():
            n_skip += 1
            continue
        bs = boards_from_log(f)
        if len(bs) < 2:
            continue
        book = book_from_boards(bs)
        n_dead += sum(1 for v in book["rows"].values() if v[0] >= 2)
        n_w += 1
        if a.apply:
            out.mkdir(parents=True, exist_ok=True)
            bp.with_suffix(".tmp").write_text(json.dumps(book))
            os.replace(bp.with_suffix(".tmp"), bp)
    print(json.dumps({"apply": a.apply, "books": n_w, "existing_kept": n_skip, "rows_streak_ge2": n_dead, "dir": str(out)}))


if __name__ == "__main__":
    main()
