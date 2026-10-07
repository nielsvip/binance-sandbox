#!/usr/bin/env python3
"""Snapshot crypto WR/LR symbol lists into data/wr_lists_history.jsonl.

Appends one row when any list changed since the tail row:
{"ts": epoch, "ang_long": [...], ... "fin_short": [...]}
Consumer: vec_decisions/check_entry_candidates_crypto__wr_lr_pullback.wr_in_list_at
(backtest_v12 WR history). No archive => wr_in_list_at returns None => WR
backtests fail-closed (never invent membership). Pure observer: never touches
the list files themselves. Run from cron/launchd every 5-15 min on the live box.
"""
import json
import os
import sys
import time

ACCTS = ("ang", "inf", "men", "flz", "fin")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, "data", "wr_lists_history.jsonl")


def _read(acct, side):
    p = os.path.join(ROOT, f"symbols_{acct}_{side}.json")
    try:
        with open(p) as f:
            d = json.load(f)
        return sorted({str(s) for s in (d if isinstance(d, list) else []) if s})
    except Exception:
        return None


def main():
    snap = {}
    for acct in ACCTS:
        for side in ("long", "short"):
            v = _read(acct, side)
            if v is not None:
                snap[f"{acct}_{side}"] = v
    if not snap:
        print("WR_ARCHIVE: no list files readable; nothing appended")
        return 1
    tail = None
    if os.path.exists(HIST):
        try:
            with open(HIST) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            tail = json.loads(line)
                        except Exception:
                            pass
        except Exception:
            tail = None
    if isinstance(tail, dict):
        same = all(sorted(tail.get(k) or []) == sorted(snap.get(k) or []) for k in set(list(tail.keys()) + list(snap.keys())) if k != "ts")
        if same:
            print("WR_ARCHIVE: unchanged since tail ts=%s; nothing appended" % tail.get("ts"))
            return 0
    row = {"ts": time.time()}
    row.update(snap)
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    with open(HIST, "a") as f:
        f.write(json.dumps(row) + "\n")
    print("WR_ARCHIVE: appended ts=%s accts=%d" % (row["ts"], len(snap)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
