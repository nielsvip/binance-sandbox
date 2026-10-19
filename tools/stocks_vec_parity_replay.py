#!/usr/bin/env python3
"""Stocks zero-discrepancy replay (USER 2026-10-19: trade EXACTLY what the vector
dictates — not one more, not one less, entry or exit).

Dummy-trades a session from the logs WITHOUT touching live state (read-only):
  1. every STRICT_VEC_PARITY BLOCKED decision is replayed through
     vec_paths.vec_parity_gate.is_vec_achievable -> a vector reason that was
     blocked is a NOT-ONE-LESS violation;
  2. every broker SENT order is replayed the same way -> a native-only reason
     that reached the broker is a NOT-ONE-MORE violation (GHOST_CLEARED
     bookkeeping and broker-rejected attempts are reported separately);
  3. --preopen (cron at 13:05 UTC weekdays, before the bell) checks the DATA
     precondition only: every traded-universe stock NPZ must be bar-fresh so
     the VEC_EXACT twin cannot fall back to native.

Exit 0 = parity held. Exit 1 = violation (cron pages the log).
"""
import glob
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
LOGS = os.path.expanduser("~/logs")

from vec_paths.vec_parity_gate import is_vec_achievable as achievable

DATE = sys.argv[sys.argv.index("--date") + 1] if "--date" in sys.argv else time.strftime("%m-%d")
PREOPEN = "--preopen" in sys.argv

blocked, executed, ghosts, rejected = [], [], [], []
stale_fallbacks = 0
for acct in ("tra", "trb", "trc"):
    try:
        fp = open(os.path.join(LOGS, f"tradier_manage_{acct}.log"), errors="replace")
    except OSError:
        continue
    for line in fp:
        if DATE not in line[:16]:
            continue
        m = re.search(r"STRICT_VEC_PARITY\] (\S+): BLOCKED .* action=(\S+) reason=(.*)$", line)
        if m:
            blocked.append((m.group(1), m.group(2), m.group(3).strip()))
            continue
        if "VEC_EXACT_STALE_FALLBACK" in line:
            stale_fallbacks += 1
# Broker attempts + confirmations carry the gate's input reason in the GENERAL logs.
for acct in ("tra", "trb", "trc"):
    try:
        fp = open(os.path.join(LOGS, f"tradier_manage_general_{acct}.log"), errors="replace")
    except OSError:
        continue
    for line in fp:
        if DATE not in line[:16] or "[TRADE]" not in line:
            continue
        m = re.search(r"\[TRADE\]\s+(?:SENT:\s+)?(?:✅\s+)?([A-Z]+)\s+(OPEN|CLOSE|REDUCE)\b.*?Status: (\S+)", line)
        if not m:
            continue
        kind, status = m.group(2), m.group(3)
        rm = re.search(r"Reason: (.*?)(?: \| Gain:|\|$)", line)
        reason = rm.group(1).strip() if rm else ""
        if "GHOST_CLEARED" in line:
            ghosts.append(line.strip()[:100])
        elif status.startswith("ORDER_REJECTED"):
            rejected.append((kind, reason, status))
        elif status == "SUBMITTED" or (status in ("ok", "filled") and reason):
            # SUBMITTED is the gate-passing record (carries Reason); bare
            # confirmations without Reason pair to an evaluated attempt.
            executed.append((kind, reason or "(no reason)"))

violations = []
for key, act, reason in blocked:
    if achievable(reason):
        violations.append(f"NOT-ONE-LESS {key} {act} blocked but vec-achievable: {reason[:80]}")
seen_exec = set()
for kind, reason in executed:
    if (kind, reason) in seen_exec:  # SUBMITTED attempt + broker confirmation share one reason
        continue
    seen_exec.add((kind, reason))
    if not achievable(reason):
        violations.append(f"NOT-ONE-MORE {kind} executed but native-only: {reason[:80]}")

fresh = stale = 0
stale_syms = []
if PREOPEN or True:
    syms = set()
    for f in glob.glob(os.path.join(BASE, "symbols_tr[abc]_*.json")):
        try:
            d = json.load(open(f))
            l = d if isinstance(d, list) else (d.get("symbols") or list(d.keys()))
            syms |= {str(x).upper() for x in l if str(x).strip()}
        except Exception:
            pass
    try:
        import numpy as np
        now = time.time()
        # Monday structural gap: nothing trades on weekends, so Friday-close bars
        # are the freshest POSSIBLE at Monday's bell (no new vector decision can
        # exist before new bars form; weekend exits are covered by EXIT_OWED).
        max_age_min = (100 * 60) if time.gmtime(now).tm_wday == 0 else (24 * 60)
        for s in sorted(syms):
            try:
                ts = np.load(os.path.join(BASE, "backtest_v8", "indicators", f"{s}.npz"),
                             allow_pickle=True)["timestamps"]
                age_min = (now - float(ts[-1])) / 60.0
                if age_min < max_age_min:
                    fresh += 1
                else:
                    stale += 1
                    stale_syms.append(f"{s}:{age_min / 60:.0f}h")
            except Exception:
                stale += 1
                stale_syms.append(f"{s}:missing")
    except ImportError:
        pass

print(f"date={DATE} blocked={len(blocked)} executed={len(executed)} "
      f"ghosts={len(ghosts)} broker_rejected={len(rejected)} stale_fallbacks={stale_fallbacks}")
print(f"npz fresh={fresh} stale={stale}" + (f" [{', '.join(stale_syms[:12])}]" if stale_syms else ""))
for v in violations[:20]:
    print("VIOLATION:", v)
if PREOPEN and stale:
    print("PREOPEN-FAIL: twin would fall back on stale symbols")
    sys.exit(1)
if violations:
    sys.exit(1)
print("PARITY-HELD")
