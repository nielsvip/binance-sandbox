"""USER 2026-10-23: S1 lane verdicts must reach the Mac converge loop.

The locked converge reader (board_state_converge.py) reads ONLY the local
ledger data/pure_board/intents_<YYYY-MM-DD>.jsonl. The S1 backup lane writes
its verdicts to S1's own ledger, mirrored to Mac as intents_s1_<date>.jsonl
(S1_INTENTS_PULL cron) — which no production code reads.

This script is the missing link, deliberately a NEW unlocked file so the
locked converge path stays untouched: it appends fresh S1-mirror rows into
the local ledger (same schema, deduped per bar), so the locked loop consumes
them like any local verdict and applies its own 1200s freshness filter.

Safety: additive-only (never modifies/deletes local rows), merges only rows
with ts within MERGE_MAX_AGE_S, dedupes on (acct, sym_side, bar_ts, action),
atomic append under flock. A merged row is byte-identical to the S1 source
row — provenance stays auditable in data/converge/ records via board_md5.
"""
from __future__ import annotations

import fcntl
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PB = os.path.join(ROOT, "data", "pure_board")
MERGE_MAX_AGE_S = 1800.0


def _read_jsonl(fp):
    rows = []
    try:
        with open(fp, errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln.startswith("{"):
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        continue
    except OSError:
        pass
    return rows


def _day_file(day):
    return os.path.join(PB, f"intents_{day}.jsonl")


def merge(now=None):
    now = float(now if now is not None else time.time())
    days = {datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")}
    days.add(datetime.fromtimestamp(now - 3600, timezone.utc).strftime("%Y-%m-%d"))
    have = set()
    for day in sorted(days):
        for r in _read_jsonl(_day_file(day)):
            try:
                have.add((r.get("acct"), r.get("sym_side"), float(r.get("bar_ts") or 0), r.get("action")))
            except Exception:
                continue
    merged, stale, dup = 0, 0, 0
    for fp in glob.glob(os.path.join(PB, "intents_s1_*.jsonl")):
        base = os.path.basename(fp)
        day = base[len("intents_s1_"):-len(".jsonl")] if base.startswith("intents_s1_") and base.endswith(".jsonl") else ""
        if day not in days:
            continue
        fresh_rows = []
        for r in _read_jsonl(fp):
            try:
                if now - float(r.get("ts") or 0) > MERGE_MAX_AGE_S:
                    stale += 1
                    continue
                key = (r.get("acct"), r.get("sym_side"), float(r.get("bar_ts") or 0), r.get("action"))
            except Exception:
                continue
            if key in have:
                dup += 1
                continue
            have.add(key)
            fresh_rows.append(r)
        if not fresh_rows:
            continue
        dest = _day_file(day)
        with open(dest, "a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
                for r in fresh_rows:
                    fh.write(json.dumps(r, default=str, separators=(",", ":")) + "\n")
            finally:
                try:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                except Exception:
                    pass
        merged += len(fresh_rows)
    return {"merged": merged, "stale_skipped": stale, "dup_skipped": dup}


def main():
    os.makedirs(PB, exist_ok=True)
    res = merge()
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **res}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
