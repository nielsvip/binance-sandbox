#!/usr/bin/env python3
"""v15_open_positions_push — publish the sym_sides that have an OPEN position (USER ruling 2026-10-06).

USER: no CPU on non-tradeable keys UNLESS they have an open position. The positions live on the Mac; the universe /
chain run on S1. This writes data/open_position_sym_sides.json (atomic) from every account's {acct}/long_positions.json +
short_positions.json (crypto flz/men/ang/inf/fin, stocks tra/trb/trc; dict keyed "acct:SYM_SIDE", open = |positionAmt| > 0)
and rsyncs it to S1/s2/s5 ~/binance-sandbox/data/ (rsync writes a temp file then renames = atomic), md5-verified.
Any unreadable positions file aborts the run and keeps the last good file (a partial list would drop open positions).
Mac cron: */5 * * * * ... tools/v15_open_positions_push.py --push   # V15_OPEN_POSITIONS_PUSH
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "open_position_sym_sides.json"
ACCOUNTS = ("flz", "men", "ang", "inf", "fin", "tra", "trb", "trc")


def collect():
    by_acct, errors = {}, []
    for acct in ACCOUNTS:
        for side in ("long", "short"):
            p = ROOT / acct / f"{side}_positions.json"
            if not p.exists():
                continue
            try:
                d = json.loads(p.read_text())
            except Exception as e:
                errors.append(f"{p}: {e}")
                continue
            for k, v in (d.items() if isinstance(d, dict) else []):
                if not isinstance(v, dict):
                    continue
                try:
                    amt = abs(float(v.get("positionAmt") or 0.0))
                except Exception:
                    amt = 0.0
                if amt > 0:
                    ss = str(k).split(":", 1)[-1].upper()
                    by_acct.setdefault(acct, []).append(ss)
    return by_acct, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--hosts", default=str(ROOT / "tools" / "fleet_hosts.json"))
    a = ap.parse_args()
    by_acct, errors = collect()
    if errors:
        print(f"[open-pos] ABORT (last good file kept): {errors[:3]}", file=sys.stderr)
        return 1
    sym_sides = sorted({s for v in by_acct.values() for s in v})
    obj = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "source": "Mac {acct}/{long,short}_positions.json |positionAmt|>0", "sym_sides": sym_sides, "by_account": {k: sorted(v) for k, v in sorted(by_acct.items())}}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    json.loads(tmp.read_text())
    os.replace(tmp, OUT)
    md5 = hashlib.md5(OUT.read_bytes()).hexdigest()
    print(f"[open-pos] {len(sym_sides)} open sym_sides -> {OUT} md5={md5}")
    if not a.push:
        return 0
    bad = 0
    for h in json.loads(Path(a.hosts).read_text())["hosts"]:
        ok = False
        for t in h["ssh"]:
            r = subprocess.run(["rsync", "-az", "-e", "ssh -o ConnectTimeout=10 -o BatchMode=yes", str(OUT), f"{t}:binance-sandbox/data/open_position_sym_sides.json"], capture_output=True, text=True, timeout=60)
            if r.returncode:
                continue
            m = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", t, "md5sum ~/binance-sandbox/data/open_position_sym_sides.json"], capture_output=True, text=True, timeout=30)
            ok = m.stdout.split()[:1] == [md5]
            print(f"[open-pos] push {h['name']} via {t}: md5 {'OK' if ok else 'MISMATCH ' + m.stdout.strip()[:60]}")
            break
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
