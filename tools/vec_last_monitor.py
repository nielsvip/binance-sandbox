#!/usr/bin/env python3
"""vec_last_monitor — USER 2026-10-24: every VEC_ appearance names a path to fix.

Scans live logs + trade history + the disparity queue for VEC_ override lines
and aggregates them into a fix queue: each row names the native gate(s) or the
vec reason family that fired, with a count. Exit 0 always (monitoring, never a
gate); the verdict line states whether VEC_LAST_OVERRIDE hit zero.

Reads (all fail-open, missing files are skipped):
  ~/logs/ez_manage_*.log ~/logs/tradier_*.log  (VEC_LAST_OVERRIDE/REFUSE,
    VEC_DRIVEN_*, VEC_EXACT, VEC_CONVERGE, VEC_STALE, VEC_EXIT_OWED,
    VECTOR_EXEC_REFUSED)
  data/history/*/*.jsonl newer than --since-h (VEC_ reasons on fills)
  data/vector_disparity_queue.jsonl (per-order bypassed-gate records)
  data/vector_kills.json (standing killed families)

Writes: data/reports/vec_last_monitor.jsonl (one summary row per run).
Usage: python3 tools/vec_last_monitor.py [--since-h 24] [--log-dir ~/logs]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG_TAGS = (
    "VEC_LAST_OVERRIDE",
    "VEC_LAST_REFUSE",
    "VEC_LAST_CLEAN",
    "VEC_DRIVEN_EXEC",
    "VEC_DRIVEN_NATIVE_SUPPRESSED",
    "NATIVE_ENTRY_BLOCK_VEC_DRIVEN",
    "VEC_EXACT_EXEC",
    "VEC_CONVERGE_OPEN",
    "VEC_STALE_FLATTEN",
    "VEC_CONVERGE_TO_FLAT",
    "VEC_EXIT_OWED",
    "VEC_EXACT_OPEN",
    "VEC_DRIVEN_OPEN",
    "VEC_DRIVEN_CLOSE",
    "VECTOR_EXEC_REFUSED",
    "VEC_LAST_DEFER",
)
TAG_RE = re.compile(r"\[(" + "|".join(LOG_TAGS) + r")[ _\]]")
GATES_RE = re.compile(r"\[VEC_LAST_OVERRIDE\][^\[]*?bypassed \[(.*?)\]")
HIST_VEC_RE = re.compile(r"VEC_[A-Z_]+|\|VEC_EXACT")


def _since_ts(hours):
    return time.time() - float(hours) * 3600.0


def _log_line_timestamp(line, now=None):
    """Parse the manager log timestamps used on this Mac; None means unknown."""
    now = now or datetime.now().astimezone()
    s = str(line or "").lstrip("\ufeff \t")
    if s.startswith("["):
        s = s[1:]
    # ISO date, with optional milliseconds and UTC suffix.
    m = re.match(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)", s)
    if m:
        raw = m.group(1).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=now.tzinfo)
            return dt.timestamp()
        except ValueError:
            return None
    # Tradier logs commonly use MM-DD HH:MM:SS; crypto logs use DD HH:MM:SS.
    m = re.match(r"^(\d{1,2})-(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})", s)
    if m:
        month, day, hour, minute, second = map(int, m.groups())
        year = now.year
    else:
        m = re.match(r"^(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})", s)
        if not m:
            return None
        day, hour, minute, second = map(int, m.groups())
        month, year = now.month, now.year
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=now.tzinfo).timestamp()
    except ValueError:
        return None


def scan_logs(log_dir, since_ts):
    tag_counts = collections.Counter()
    gate_counts = collections.Counter()
    files = sorted(glob.glob(os.path.join(log_dir, "ez_manage_*.log")) + glob.glob(os.path.join(log_dir, "tradier_*.log")))
    for path in files:
        try:
            if os.path.getmtime(path) < since_ts - 7 * 86400:
                continue
        except OSError:
            continue
        try:
            with open(path, errors="replace") as fh:
                for line in fh:
                    line_ts = _log_line_timestamp(line)
                    if line_ts is None or line_ts < since_ts:
                        continue
                    m = TAG_RE.search(line)
                    if not m:
                        continue
                    tag_counts[m.group(1)] += 1
                    g = GATES_RE.search(line)
                    if g:
                        for gate in g.group(1).split("+"):
                            gate = gate.strip()
                            if gate:
                                gate_counts[gate] += 1
        except OSError:
            continue
    return tag_counts, gate_counts, len(files)


def scan_history(since_ts):
    fam_counts = collections.Counter()
    n_events = 0
    for path in glob.glob(os.path.join(ROOT, "data", "history", "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < since_ts:
                continue
        except OSError:
            continue
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    try:
                        ets = datetime.fromisoformat(str(d.get("ts", ""))).timestamp()
                        if ets < since_ts:
                            continue
                    except Exception:
                        pass
                    r = str(d.get("reason", ""))
                    if "VEC" not in r.upper():
                        continue
                    n_events += 1
                    head = r.split(" ")[0][:80]
                    fam_counts[head] += 1
        except OSError:
            continue
    return fam_counts, n_events


def scan_queue():
    gates = collections.Counter()
    n = 0
    p = os.path.join(ROOT, "data", "vector_disparity_queue.jsonl")
    try:
        with open(p) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                n += 1
                for g in d.get("gates", []) or []:
                    gates[str(g)] += 1
    except OSError:
        pass
    return gates, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-h", type=float, default=24.0)
    ap.add_argument("--log-dir", default=os.path.expanduser("~/logs"))
    args = ap.parse_args()
    since_ts = _since_ts(args.since_h)
    tag_counts, log_gates, n_logs = scan_logs(args.log_dir, since_ts)
    hist_fams, n_hist_vec = scan_history(since_ts)
    q_gates, n_queue = scan_queue()
    override_n = tag_counts.get("VEC_LAST_OVERRIDE", 0)
    refuse_n = tag_counts.get("VEC_LAST_REFUSE", 0)
    verdict = "VEC_SILENT" if (override_n == 0 and n_hist_vec == 0) else ("OVERRIDE_ZERO" if override_n == 0 else "OVERRIDE_ACTIVE")
    summary = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "since_h": args.since_h,
        "n_log_files": n_logs,
        "log_tags": dict(tag_counts.most_common()),
        "override_gates": dict(log_gates.most_common(20)),
        "queue_gates_all_time": dict(q_gates.most_common(20)),
        "history_vec_events": n_hist_vec,
        "history_vec_top": dict(hist_fams.most_common(20)),
        "queue_rows": n_queue,
        "verdict": verdict,
    }
    out = os.path.join(ROOT, "data", "reports", "vec_last_monitor.jsonl")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "a") as fh:
            fh.write(json.dumps(summary) + "\n")
    except OSError:
        pass
    print(f"[VEC_LAST_MONITOR] verdict={verdict} override={override_n} refuse={refuse_n} hist_vec={n_hist_vec} logs={n_logs}")
    for gate, c in log_gates.most_common(15):
        print(f"  LIVE_WINDOW {c:6d} {gate}")
    for gate, c in q_gates.most_common(10):
        print(f"  QUEUE_HIST  {c:6d} {gate}")
    for fam, c in hist_fams.most_common(10):
        print(f"  HIST {c:6d} {fam[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
