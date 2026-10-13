#!/usr/bin/env python3
"""v15_npz_freeze — RULE#4 resume guard (2026-10-13, RESUME-FROM-CELL).

A board's done rows are valid only on the NPZ it was measured on; the pilot archives the whole board
(NPZ-CHANGED-REFILL) when the NPZ identity (path/mtime/size/bars/first/last ts) differs at resume.
Measured 2026-10-13 over run32: 44 boards archived in 24h (up to 3546 done rows each), every one because
the symbol's NPZ tail advanced between two slices of an unfinished board (S1 rebuild mirrored to the worker
by sync_indicators.sh, daily fundoi install, ...).

The fleet scheduler (S1) writes the registry every tick: every symbol with an UNFINISHED board in the
current round (progress json without final_gain, or needs_redo) or a running pilot. Every NPZ writer on
S1 consults it and leaves those symbols' NPZs untouched (and unpushed) until the board finishes:
  tools/v15_npz_prep.py (dispatch JIT rebuild), tools/sync_indicators.sh (S1 -> worker mirror),
  tools/npz_fundoi/install_fundoi_npz.py, tools/npz_sentiment_force.py.
  python3 tools/v15_npz_freeze.py --list            # one symbol per line (sync_indicators exclude helper)
Kill switch: V15_NPZ_FREEZE=0 (readers then see an empty registry).
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path(os.environ.get("V15_NPZ_FREEZE_FILE", str(ROOT / "data" / "npz_frozen_syms.json")))


def build(started, done, redo, running):
    """{sym: [reasons]} — started-but-not-done or needs_redo sides, plus running pilots (sym_side strings)."""
    out = {}
    for ss in sorted((set(started) - set(done)) | set(redo)):
        out.setdefault(ss.rsplit("_", 1)[0], []).append(f"unfinished:{ss}")
    for ss in sorted(set(running)):
        out.setdefault(ss.rsplit("_", 1)[0], []).append(f"running:{ss}")
    return out


def write(frozen, path=None, now=None):
    p = Path(path) if path else REGISTRY
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": float(now if now is not None else time.time()), "syms": frozen}, indent=1, sort_keys=True))
    os.replace(tmp, p)


def frozen_syms(path=None):
    """Set of frozen symbols. Missing/unreadable registry or V15_NPZ_FREEZE=0 -> empty set. A stale registry
    (scheduler dead) is still honoured — fail-safe for boards."""
    if os.environ.get("V15_NPZ_FREEZE", "1") != "1":
        return set()
    p = Path(path) if path else REGISTRY
    try:
        d = json.loads(p.read_text())
        return {str(s).upper() for s in (d.get("syms") or {})}
    except Exception:
        return set()


def is_frozen(sym, path=None):
    return str(sym).upper() in frozen_syms(path)


if __name__ == "__main__":
    if "--list" in sys.argv:
        for s in sorted(frozen_syms()):
            print(s)
