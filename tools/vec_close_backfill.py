#!/usr/bin/env python3
"""vec_close_backfill — every FIXED_STOP / PEAK_GIVEBACK close firing gets a VEC_ queue entry.

The police checkpoints (VEC_DICTATE_ONLY_BLOCK / VEC_LAST_OVERRIDE / VEC_LAST_REFUSE) already write
data/vector_disparity_queue.jsonl. What they MISS (all read-only, in forbidden live files):
  * guardian VEC_DECISION_REFUSED lines (vec-dictated family close refused by a native pre-gate) — log only;
  * VECTOR_EXEC_REFUSED OFF_BAR_WINDOW defers (ez_manage.py:37343) — logger.info only, no jsonl.
This tool diffs designated live logs against the queue and APPENDS the missing ones as VEC_AUDIT_BACKFILL
(audit-derived, source-stamped — never presented as police-observed). Appends only with --apply.
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

FAMILIES = ("FIXED_STOP", "PEAK_GIVEBACK")
GUARDIAN_RE = re.compile(r"VEC_DECISION_REFUSED\s+(\S+)\s+::\s+(\S+)\s+(\S+).*?bar=(\S+).*?(BLOCKED_\S+|TIMEOUT\S*|SKIPPED_\S+)?")
OFFBAR_RE = re.compile(r"VECTOR_EXEC_REFUSED\]\s+(\S+)\s+action=(\S+)\s+reason=(\S+)")


def family_of(reason):
    up = str(reason or "").upper()
    for fam in FAMILIES:
        if fam in up:
            return fam
    return None


def hour_of(bar):
    try:
        return dt.datetime.fromisoformat(str(bar).replace("Z", "+00:00")).strftime("%Y-%m-%dT%H")
    except Exception:
        return None


def queue_keys(queue):
    keys = set()
    try:
        for line in open(queue, errors="ignore"):
            try:
                e = json.loads(line)
            except Exception:
                continue
            fam = family_of(e.get("reason", "")) or family_of(e.get("vec_path", ""))
            if not fam:
                continue
            try:
                h = dt.datetime.fromtimestamp(float(e.get("ts", 0)), dt.timezone.utc).strftime("%Y-%m-%dT%H")
            except Exception:
                h = None
            keys.add((str(e.get("position_key")), fam, h))
    except FileNotFoundError:
        pass
    return keys


def scan_log(path):
    out = []
    try:
        fh = open(path, errors="ignore")
    except FileNotFoundError:
        return out
    with fh:
        for line in fh:
            if "VEC_DECISION_REFUSED" in line:
                m = GUARDIAN_RE.search(line)
                if m:
                    pos, act, reason, bar, refused = m.groups()
                    out.append({"position_key": pos, "action": act, "reason": reason, "hour": hour_of(bar), "refused_by": refused or "?", "source": f"{Path(path).name} bar={bar}"})
            elif "VECTOR_EXEC_REFUSED" in line:
                m = OFFBAR_RE.search(line)
                if m:
                    pos, act, reason = m.groups()
                    out.append({"position_key": pos, "action": act, "reason": reason, "hour": None, "refused_by": "OFF_BAR_WINDOW", "source": Path(path).name})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--log", action="append", default=[])
    ap.add_argument("--venue", default="crypto")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    keys = queue_keys(a.queue)
    covered = ignored = backfilled = 0
    pending = []
    for lp in a.log:
        for f in scan_log(lp):
            fam = family_of(f["reason"])
            if not fam:
                ignored += 1
                continue
            hit = any(k[0] == f["position_key"] and k[1] == fam and (f["hour"] is None or k[2] is None or k[2] == f["hour"]) for k in keys)
            if hit:
                covered += 1
                continue
            f["family"] = fam
            pending.append(f)
    if a.apply and pending:
        with open(a.queue, "a") as fh:
            import time
            for f in pending:
                fh.write(json.dumps({"ts": time.time(), "venue": a.venue, "kind": "VEC_AUDIT_BACKFILL", "position_key": f["position_key"], "action": f["action"], "reason": str(f["reason"])[:160], "family": f["family"], "refused_by": f["refused_by"], "source": f["source"], "fix": "audit-backfilled family firing (log-only, never police-queued); batch-fix candidate"}) + "\n")
                keys.add((f["position_key"], f["family"], f["hour"]))
        backfilled = len(pending)
    print(json.dumps({"scanned_logs": len(a.log), "covered": covered, "ignored": ignored, "backfilled": backfilled if a.apply else len(pending), "applied": bool(a.apply)}))


if __name__ == "__main__":
    main()
