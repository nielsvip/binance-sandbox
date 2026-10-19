#!/usr/bin/env python3
"""board_vintage_audit — a published board may never wear a date newer than the evals that produced it (USER 2026-10-18).

USER: "make sure we never rewrite an old test with a newer date that is deceiving which is lying which is forbidden."

THE DECEPTION THIS MEASURES. `v15_pilot` resumes: when a board's rows are already in the round's progress dir it logs
`[spec-fill] workbook complete, N rows filled, returning early` and publishes in ~4 minutes. The publish is then stamped
NOW while the cells it published were computed whenever the round last touched them. Measured 2026-10-19: 78 of the 80
most recent board logs returned early against a progress dir open since 2026-10-11, so boards published "today" carried
cell values up to 8 days old. Nothing was forged on purpose — the round could not roll because `stage_restart` died on a
destroyed host — but the artifact still claims a freshness it does not have, and that is the lie.

THE TRUTH SOURCE is the delta log, not a file mtime. Every eval `v15_pilot` performs appends a row carrying its own `ts`
to `~/v15_run*/[progress/]v15_delta_log/<SYM_SIDE>[_jump].jsonl`. The newest eval row for a sym_side is therefore when
that board was really last computed, and the oldest row in its current round is when its surviving cells began. A board
file whose mtime is materially newer than its newest eval row is wearing a borrowed date.

WHAT THIS TOOL DOES NOT DO. It never rewrites, renames or re-stamps anything — re-stamping published sheets is itself
the forbidden act (2026-10-13: `red_fixer` re-stamped published sheets, illegal, stopped). It reports, exits non-zero
when any board is flagged, and writes the evidence to data/reports/board_vintage/<stamp>.json so a claim can be checked
after the fact.

VERDICTS per board
  FRESH       file mtime within --tol-h of its newest eval  -> the date is honest
  RESUMED     mtime is >= --tol-h newer than its newest eval -> published later than it was computed
  STALE_CELLS newest eval older than --since                 -> content predates the cutoff whatever the file says
  NO_EVALS    no eval rows at all for this sym_side          -> nothing backs the file's date

usage:
  tools/board_vintage_audit.py --host s2                      # audit the boards on a worker
  tools/board_vintage_audit.py --since 2026-10-15T00:00:00Z   # also flag cells older than Thursday
  tools/board_vintage_audit.py --boards 'SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx' --tol-h 2
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reports" / "board_vintage"
SS_RE = re.compile(r"^([A-Z0-9]+)_(LONG|SHORT)")
LOG_GLOBS = ("~/v15_run*/progress/v15_delta_log/*.jsonl", "~/v15_run*/v15_delta_log/*.jsonl")

PROBE = r'''
import glob, json, os
last, first = {}, {}
for g in (%s):
    for f in glob.glob(os.path.expanduser(g)):
        base = os.path.basename(f)[:-6]
        ss = base[:-5] if base.endswith('_jump') else base
        try:
            with open(f, 'rb') as fh:
                fh.seek(0, 2); size = fh.tell()
                head = []
                fh.seek(0)
                for _ in range(400):
                    line = fh.readline()
                    if not line: break
                    head.append(line.decode('utf8', 'replace'))
                fh.seek(max(0, size - 20000))
                tail = fh.read().decode('utf8', 'replace').splitlines()
        except Exception:
            continue
        for lines, book, rev in ((tail, last, True), (head, first, False)):
            for line in (reversed(lines) if rev else lines):
                if '"gain_pct"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get('gain_pct') is None:
                    continue
                ts = str(d.get('ts') or '')
                if not ts:
                    continue
                if rev:
                    if ts > book.get(ss, ''): book[ss] = ts
                else:
                    if ss not in book or ts < book[ss]: book[ss] = ts
                break
print(json.dumps({'last': last, 'first': first}))
''' % ", ".join(repr(g) for g in LOG_GLOBS)


def eval_vintage(host):
    """{sym_side: (first_ts, last_ts)} of real eval rows, from the delta logs on `host` (None = this machine)."""
    cmd = [sys.executable or "python3", "-c", PROBE]
    if host:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ControlPath=none", host, "python3 -c " + json_quote(PROBE)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        d = json.loads((r.stdout or "{}").strip().splitlines()[-1])
    except Exception as exc:
        print(f"[vintage] probe on {host or 'local'} failed: {exc}", file=sys.stderr)
        return {}
    return {k: ((d.get("first") or {}).get(k, ""), v) for k, v in (d.get("last") or {}).items()}


def json_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def iso(ts):
    try:
        return dt.datetime.strptime(str(ts).replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def symside_of(name):
    m = SS_RE.match(os.path.basename(name).upper())
    return f"{m.group(1)}_{m.group(2)}" if m else None


def audit(boards, vint, tol_h, since):
    rows = []
    for f in boards:
        ss = symside_of(f)
        if not ss:
            continue
        try:
            mt = dt.datetime.fromtimestamp(os.path.getmtime(f), dt.timezone.utc)
        except OSError:
            continue
        first, last = vint.get(ss, ("", ""))
        lt = iso(last)
        verdict = "NO_EVALS" if not lt else None
        gap_h = None
        if lt:
            gap_h = (mt - lt).total_seconds() / 3600.0
            if since and last < since:
                verdict = "STALE_CELLS"
            elif gap_h >= tol_h:
                verdict = "RESUMED"
            else:
                verdict = "FRESH"
        rows.append({"file": os.path.basename(f), "symside": ss, "verdict": verdict,
                     "file_date_utc": mt.strftime("%Y-%m-%dT%H:%M:%SZ"), "cells_last_eval": last or None,
                     "cells_first_eval": first or None, "file_newer_than_cells_h": None if gap_h is None else round(gap_h, 2)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    # Repeatable, and the vintages are MERGED (newest eval wins): a board computed on S1 has no rows in S2's logs, so a
    # single-host probe reports NO_EVALS for it and would understate freshness. 2026-10-19: probing s2 alone called 1,107
    # boards NO_EVALS.
    ap.add_argument("--host", action="append", default=[], help="ssh host whose delta logs to read (repeatable; default: this machine)")
    ap.add_argument("--boards", action="append", default=[], help="glob of board files to audit (repeatable)")
    ap.add_argument("--tol-h", type=float, default=2.0, help="a file may be at most this many hours newer than its newest eval")
    ap.add_argument("--since", default="", help="ISO ts: flag boards whose newest eval predates this (e.g. Thursday)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    globs = a.boards or [str(ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / "*.xlsx")]
    boards = sorted({f for g in globs for f in glob.glob(os.path.expanduser(g))})
    if not boards:
        sys.exit(f"[vintage] no board files matched {globs}")
    vint = {}
    for h in (a.host or [None]):
        for ss, (first, last) in eval_vintage(h).items():
            pf, pl = vint.get(ss, ("", ""))
            vint[ss] = (first if (not pf or (first and first < pf)) else pf, last if last > pl else pl)
    rows = audit(boards, vint, a.tol_h, a.since)
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    rec = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "hosts": a.host or [os.uname().nodename],
           "tol_h": a.tol_h, "since": a.since or None, "n_boards": len(rows), "counts": counts,
           "truth_source": "v15_delta_log eval rows (ts of the real engine evals), never a file mtime", "rows": rows}
    (OUT / f"vintage_{stamp}.json").write_text(json.dumps(rec, indent=1))
    if a.json:
        print(json.dumps(rec, indent=1))
        return 0
    print(f"[vintage] {len(rows)} boards · {counts} · evidence {OUT / f'vintage_{stamp}.json'}")
    bad = [r for r in rows if r["verdict"] in ("RESUMED", "STALE_CELLS", "NO_EVALS")]
    for r in sorted(bad, key=lambda r: -(r["file_newer_than_cells_h"] or 0))[:20]:
        print(f"  {r['verdict']:12s} {r['symside']:16s} file {r['file_date_utc']}  cells last {r['cells_last_eval'] or '-'}"
              f"  file is {r['file_newer_than_cells_h'] if r['file_newer_than_cells_h'] is not None else '?'} h newer")
    if bad:
        print(f"[vintage] FLAGGED {len(bad)} of {len(rows)} — these files wear a date their cells do not back")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
