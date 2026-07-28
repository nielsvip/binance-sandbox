#!/usr/bin/env python3
"""Heartbeat guard for the master symbol allowlists.

USER 2026-07-28: "Symbols.json like symbols_tradier.json are the heartbeat of the
system. Without them ez_rankings can not produce the rankings lists that generate
the tradeable keys that everything depends on."

`symbols_tradier.json` has been unlinked 12 times between 2026-07-20 and
2026-07-28. `tradier_rankings.load_symbols()` restores it, but only when rankings
next runs, so the file can be absent for hours. `symbols.json` (crypto) had no
guard at all.

This does two jobs:

1. **Restore fast.** Poll every ``--interval`` seconds (default 15) and rebuild a
   missing or invalid master from its last-known-good seed, so the outage window
   is seconds instead of hours.
2. **Name the culprit.** The restore loop cannot say who deleted the file, so on
   every detection this captures a forensic snapshot — wall clock, a full process
   listing, and any process still holding the path open — into
   ``data/symbol_guard_incidents.jsonl``. The next deletion therefore arrives
   with the evidence attached instead of only a hole.

A seed is only refreshed from a master that passes validation and is no smaller
than the seed, so a truncated master can never poison the recovery copy.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
INCIDENTS = REPO / "data" / "symbol_guard_incidents.jsonl"
GUARDED = [
    {"master": REPO / "symbols_tradier.json",
     "seed": REPO / "symbols_tradier.last_known_good.json",
     "min_symbols": 100},
    {"master": REPO / "symbols.json",
     "seed": REPO / "symbols.last_known_good.json",
     "min_symbols": 100},
]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def log(msg):
    print(f"[{now_iso()}] {msg}", flush=True)


def validated(path, min_symbols):
    with open(path) as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError("top-level JSON value is not a list")
    symbols = list(dict.fromkeys(
        str(s).strip().upper() for s in raw if str(s).strip()
    ))
    if len(symbols) < min_symbols:
        raise ValueError(f"only {len(symbols)} symbols; expected >= {min_symbols}")
    return symbols


def atomic_write(path, symbols):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w") as fh:
            json.dump(symbols, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def forensics(master):
    """Capture who was running the moment the file went missing."""
    snap = {"ts": now_iso(), "file": str(master)}
    try:
        ps = subprocess.run(["ps", "-eo", "pid,ppid,etime,comm,args"],
                            capture_output=True, text=True, timeout=20)
        keep = [ln for ln in ps.stdout.splitlines()
                if any(t in ln for t in ("python", "rsync", "git", "ez_", "tradier_",
                                         "push.py", "autosave", "scanner", "copilot"))]
        snap["processes"] = keep[:120]
    except Exception as exc:
        snap["processes_error"] = repr(exc)
    try:
        lsof = subprocess.run(["lsof", "--", str(master)],
                              capture_output=True, text=True, timeout=20)
        snap["lsof"] = lsof.stdout.splitlines()[:40]
    except Exception as exc:
        snap["lsof_error"] = repr(exc)
    try:
        siblings = sorted(p.name for p in master.parent.glob(f".{master.name}.*"))
        snap["stale_tmp_siblings"] = siblings[:20]
    except Exception:
        pass
    return snap


def record(snap):
    INCIDENTS.parent.mkdir(parents=True, exist_ok=True)
    with INCIDENTS.open("a") as fh:
        fh.write(json.dumps(snap) + "\n")


def check_one(entry):
    master, seed, floor = entry["master"], entry["seed"], entry["min_symbols"]
    try:
        symbols = validated(master, floor)
    except Exception as master_error:
        snap = forensics(master)
        snap["reason"] = repr(master_error)
        try:
            seed_symbols = validated(seed, floor)
        except Exception as seed_error:
            snap["outcome"] = f"UNRECOVERABLE seed invalid: {seed_error!r}"
            record(snap)
            log(f"CRITICAL {master.name} missing/invalid AND seed unusable: {seed_error!r}")
            return
        atomic_write(master, seed_symbols)
        snap["outcome"] = f"restored {len(seed_symbols)} symbols from seed"
        record(snap)
        log(f"CRITICAL restored {master.name} ({len(seed_symbols)} symbols) — "
            f"cause: {master_error!r}; forensics in {INCIDENTS.name}")
        return
    try:
        seed_symbols = validated(seed, floor)
    except Exception:
        seed_symbols = []
    if len(symbols) >= len(seed_symbols):
        if symbols != seed_symbols:
            atomic_write(seed, symbols)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    log(f"symbol heartbeat guard start — interval {args.interval}s, "
        f"guarding {', '.join(e['master'].name for e in GUARDED)}")
    while True:
        for entry in GUARDED:
            try:
                check_one(entry)
            except Exception as exc:
                log(f"guard error on {entry['master'].name}: {exc!r}")
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
