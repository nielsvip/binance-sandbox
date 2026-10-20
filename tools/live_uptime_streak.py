#!/usr/bin/env python3
"""How long has the live tier actually run WITHOUT an interruption? Measured, never asserted.

USER 2026-10-20: "you can not stop until everyhting has been running for 24 hours without an interruption".
That claim is only worth anything if something counts it, so `tools/live_component_health.py --jsonl` appends
one line per tick and this reads them back.

An interruption is ANY of:
  - a trader fault (ez_manage / tradier_manage NO_CHILD, LOG_STALE, FLAPPING, WRAPPER_DEAD)
  - any component fault at all, when --strict
  - a trader restart: min_trader_child_up_s went DOWN, i.e. some trader's process is younger than last tick
  - a gap in the record itself longer than --max-gap-s (the watch stopped; absence of evidence is not uptime)
FENCED is not an interruption on its own — a fenced host is correctly idle — but it IS reported, because the
operator's rule is that the Mac trades whenever it is up, so time spent fenced is time to explain.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default="/Users/niels/logs/live_component_health.jsonl")
    ap.add_argument("--max-gap-s", type=float, default=600.0)
    ap.add_argument("--strict", action="store_true", help="any component fault breaks the streak, not just traders")
    ap.add_argument("--target-h", type=float, default=24.0)
    a = ap.parse_args()

    p = pathlib.Path(a.jsonl)
    if not p.exists():
        print(f"no record yet at {p} — the streak is 0 by definition, not 'fine'")
        return 2
    rows = []
    for line in p.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("epoch") or 0)
    if not rows:
        print(f"{p} has no parseable lines — streak 0")
        return 2

    breaks, streak_start, prev = [], rows[0], None
    best = (0.0, None, None)
    cur_start = rows[0]
    fenced_s = 0.0
    for r in rows:
        why = []
        if prev is not None:
            gap = (r.get("epoch") or 0) - (prev.get("epoch") or 0)
            if gap > a.max_gap_s:
                why.append(f"record gap {int(gap)}s")
            pm, cm = prev.get("min_trader_child_up_s"), r.get("min_trader_child_up_s")
            if pm is not None and cm is not None and cm < pm:
                why.append(f"trader restarted (child_up {pm}s -> {cm}s)")
            if r.get("n_fenced"):
                fenced_s += min(gap, a.max_gap_s)
        if r.get("n_trader_faults"):
            why.append(f"trader fault: {','.join(r.get('faults') or [])[:120]}")
        elif a.strict and r.get("n_faults"):
            why.append(f"component fault: {','.join(r.get('faults') or [])[:120]}")
        if why:
            length = (prev or r).get("epoch", 0) - cur_start.get("epoch", 0)
            if length > best[0]:
                best = (length, cur_start.get("ts"), (prev or r).get("ts"))
            breaks.append({"ts": r.get("ts"), "why": why})
            cur_start = r
        prev = r
    now = time.time()
    current = now - (cur_start.get("epoch") or now)
    if current > best[0]:
        best = (current, cur_start.get("ts"), "now")

    span = (rows[-1].get("epoch") or 0) - (rows[0].get("epoch") or 0)
    target_s = a.target_h * 3600.0
    print(f"record: {len(rows)} ticks over {span / 3600.0:.2f} h  ({rows[0].get('ts')} .. {rows[-1].get('ts')})")
    print(f"CURRENT interruption-free streak: {current / 3600.0:.2f} h  (since {cur_start.get('ts')})")
    print(f"LONGEST streak in the record:     {best[0] / 3600.0:.2f} h  ({best[1]} .. {best[2]})")
    print(f"interruptions recorded: {len(breaks)}")
    print(f"time with a host fenced: {fenced_s / 3600.0:.2f} h")
    for b in breaks[-15:]:
        print(f"  {b['ts']}  {'; '.join(b['why'])}")
    ok = current >= target_s
    print(f"\n{'PASS' if ok else 'NOT YET'}: target {a.target_h} h, current {current / 3600.0:.2f} h"
          + ("" if ok else f" — {(target_s - current) / 3600.0:.2f} h still to go, and any trader restart resets it"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
