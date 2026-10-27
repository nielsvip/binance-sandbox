#!/usr/bin/env python3
"""tools/parity_monitor.py — USER 2026-10-27: read-only verification that every tradeable
account:sym_side's LIVE position state matches S1's forward-rolling-tested state
(data/forward_state/<SYM_SIDE>.json, the extensively-tested forward NPZ paper test).

Read-only. Places no orders. Calls execute_now nowhere. Reuses board_state_converge's
already-tested read_live_snapshots() for live state; reads data/forward_state/*.json
directly (the same file the live BOARD_CONVERGE loop already reads) for the forward-tested
state. This is a diagnostic/audit layer on top of that enforcement, not a replacement for it.

Usage: python3 tools/parity_monitor.py [--loop SECONDS]
Writes data/reports/parity_monitor_latest.json every run.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import board_state_converge as bsc

BASE = Path(__file__).resolve().parent.parent
MAX_FWD_AGE_S = 1200.0  # same staleness tolerance board_state_converge already uses for its tier-2 fallback


def load_tradeable():
    raw = json.load(open(BASE / "tradeable_keys.json"))
    out = []
    for k in raw:
        if ":" not in k:
            continue
        acct, ss = k.split(":", 1)
        out.append((acct, ss))
    return out


def forward_state_for(ss):
    fp = BASE / "data" / "forward_state" / f"{ss}.json"
    try:
        st = json.loads(fp.read_text())
    except Exception:
        return None, "missing"
    if not isinstance(st, dict) or not st.get("ok"):
        return None, f"not_ok:{(st or {}).get('reason')}"
    if not isinstance(st.get("open"), bool):
        return None, "no_open_field"
    try:
        age = time.time() - fp.stat().st_mtime
    except Exception:
        age = 1e9
    if age > MAX_FWD_AGE_S:
        return None, f"stale:{age:.0f}s"
    tail = float(st.get("npz_tail") or 0.0)
    if tail <= 0:
        return None, "no_npz_tail"
    return bool(st["open"]), "ok"


def run_once(now=None):
    now = float(now if now is not None else time.time())
    keys = load_tradeable()
    by_acct = {}
    for acct, ss in keys:
        by_acct.setdefault(acct, []).append(ss)

    total_checked = 0
    total_match = 0
    mismatches = []
    unknown = 0
    skipped_accounts = []

    for acct, sides in sorted(by_acct.items()):
        try:
            held, _ = bsc.read_live_snapshots(str(BASE), acct, now=now)
        except bsc.StaleInput as exc:
            skipped_accounts.append({"acct": acct, "reason": str(exc)})
            continue
        for ss in sorted(sides):
            live_qty = (held.get((acct, ss)) or {}).get("qty", 0.0)
            live_open = live_qty > 0
            fwd_open, status = forward_state_for(ss)
            if fwd_open is None:
                unknown += 1
                continue
            total_checked += 1
            if fwd_open == live_open:
                total_match += 1
            else:
                mismatches.append({
                    "acct": acct, "sym_side": ss,
                    "forward_open": fwd_open, "live_open": live_open,
                    "live_qty": live_qty,
                })

    pct = (100.0 * total_match / total_checked) if total_checked else 0.0
    out = {"ts": now, "checked": total_checked, "match": total_match,
           "mismatch": len(mismatches), "unknown_or_stale_forward": unknown,
           "parity_pct": pct, "mismatches": mismatches,
           "skipped_accounts": skipped_accounts}
    return out


def report(out):
    print(f"=== PARITY MONITOR {time.strftime('%FT%TZ', time.gmtime(out['ts']))} ===")
    print(f"checked={out['checked']} match={out['match']} mismatch={out['mismatch']} "
          f"unknown/stale_forward={out['unknown_or_stale_forward']} parity={out['parity_pct']:.1f}%")
    if out["skipped_accounts"]:
        print("--- ACCOUNTS SKIPPED (stale/missing live snapshot) ---")
        for s in out["skipped_accounts"]:
            print(f"  {s['acct']}: {s['reason']}")
    if out["mismatches"]:
        print("--- MISMATCHES (forward-tested state vs live state) ---")
        for m in out["mismatches"]:
            print(f"  {m['acct']}:{m['sym_side']}  forward_open={m['forward_open']}  "
                  f"live_open={m['live_open']} (qty={m['live_qty']:.6f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, default=0.0, help="repeat every N seconds; 0 = run once")
    args = ap.parse_args()
    out_path = BASE / "data" / "reports" / "parity_monitor_latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        out = run_once()
        report(out)
        out_path.write_text(json.dumps(out, indent=2))
        if args.loop <= 0:
            return 0 if not out["mismatches"] else 1
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
