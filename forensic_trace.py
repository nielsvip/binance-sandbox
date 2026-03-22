#!/usr/bin/env python3
"""FORENSIC TRACE — Retraces every event that led to position destruction.
Reads ALL log files and reconstructs the timeline of positionAmt changes for a given symbol.
Usage: python3 forensic_trace.py IOTAUSDT inf LONG
"""
import sys, os, json, glob, re
from datetime import datetime
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
LOGS = Path("/Users/niels/logs")

def main():
    if len(sys.argv) < 4:
        print("Usage: python3 forensic_trace.py <SYMBOL> <ACCOUNT> <SIDE>")
        print("Example: python3 forensic_trace.py IOTAUSDT inf LONG")
        sys.exit(1)
    symbol = sys.argv[1].upper()
    account = sys.argv[2].lower()
    side = sys.argv[3].upper()
    pk = f"{account}:{symbol}_{side}"
    print(f"\n{'='*120}")
    print(f"FORENSIC TRACE: {pk}")
    print(f"{'='*120}\n")
    # 1. Backup timeline
    print("--- BACKUP TIMELINE (positionAmt across all backups) ---")
    side_str = side.lower()
    backup_dir = BASE / account / "backups"
    if backup_dir.exists():
        files = sorted(glob.glob(str(backup_dir / f"{side_str}_positions_backup_*.json")))
        prev_amt = None
        for f in files:
            try:
                data = json.load(open(f))
                if isinstance(data, dict) and "positions" in data: data = data["positions"]
                pos = data.get(pk, {})
                amt = float(pos.get("positionAmt", 0))
                ep = float(pos.get("entry_price", 0))
                oa = pos.get("opened_at", "null")
                mg = float(pos.get("max_gain", 0))
                flag = ""
                if prev_amt is not None and prev_amt != 0 and amt == 0: flag = " *** ZEROED ***"
                elif prev_amt is not None and prev_amt == 0 and amt != 0: flag = " *** RESTORED ***"
                elif prev_amt is not None and amt != prev_amt: flag = f" (changed from {prev_amt})"
                print(f"  {os.path.basename(f)}: amt={amt} ep={ep:.6f} opened_at={str(oa)[:19]} max_gain={mg:.4f}{flag}")
                prev_amt = amt
            except Exception as e:
                print(f"  {os.path.basename(f)}: ERROR {e}")
    # Current disk
    disk_file = BASE / account / f"{side_str}_positions.json"
    if disk_file.exists():
        data = json.load(open(disk_file))
        if isinstance(data, dict) and "positions" in data: data = data["positions"]
        pos = data.get(pk, {})
        print(f"  CURRENT DISK: amt={pos.get('positionAmt', 'MISSING')} ep={pos.get('entry_price', 0)} opened_at={str(pos.get('opened_at', 'null'))[:19]}")
    # 2. JSONL trade history
    print(f"\n--- JSONL TRADE HISTORY ---")
    jf = BASE / "data" / "history" / account / f"{symbol}_{side}.jsonl"
    if jf.exists():
        with open(jf) as f:
            lines = f.readlines()
        print(f"  Total events: {len(lines)}")
        for line in lines[-20:]:
            try:
                rec = json.loads(line)
                ts = rec.get("timestamp", "?")[:19]
                action = rec.get("type", rec.get("action", "?"))
                qty = rec.get("qty", rec.get("quantity", 0))
                price = rec.get("price", 0)
                print(f"  [{ts}] {action} qty={qty} price={price}")
            except: pass
    else:
        print(f"  NO JSONL FILE: {jf}")
    # 3. Decision log
    print(f"\n--- DECISION LOG (last 20 for this position) ---")
    dec_files = sorted(glob.glob(str(BASE / "data" / "decisions" / f"decisions_{account}_*.jsonl")))
    decisions = []
    for df in dec_files[-3:]:
        try:
            with open(df) as f:
                for line in f:
                    if pk in line or symbol in line:
                        try:
                            rec = json.loads(line)
                            if rec.get("position_key") == pk:
                                decisions.append(rec)
                        except: pass
        except: pass
    for d in decisions[-20:]:
        ts = d.get("timestamp", "?")[:19]
        action = d.get("action", "?")
        reason = d.get("reason", "?")[:60]
        print(f"  [{ts}] {action}: {reason}")
    # 4. Log grep — ALL mentions across ALL log files
    print(f"\n--- LOG EVENTS (all scripts, last mentions) ---")
    log_files = [
        LOGS / f"ez_manage_{account}_app.log",
        LOGS / f"quick_{account}_process.log",
        LOGS / f"ez_positions.log",
        LOGS / f"ez_positions_{account}.log",
        LOGS / f"ez_positions_quick_general_{account}.log",
        LOGS / f"ez_positions_quick_wait_{account}.log",
    ]
    for lf in log_files:
        if not lf.exists(): continue
        matches = []
        try:
            with open(lf) as f:
                for line in f:
                    if pk in line or f"{symbol}_{side}" in line:
                        matches.append(line.rstrip())
        except: continue
        if matches:
            print(f"\n  [{lf.name}] ({len(matches)} total matches, showing last 15):")
            for m in matches[-15:]:
                print(f"    {m[:200]}")
    # 5. POSAMT_WRITE events (from our new logging)
    print(f"\n--- POSAMT_WRITE EVENTS (from new logging) ---")
    for lf in log_files:
        if not lf.exists(): continue
        try:
            with open(lf) as f:
                for line in f:
                    if "POSAMT_WRITE" in line and pk in line:
                        print(f"  {line.rstrip()[:200]}")
        except: pass
    # 6. Redis current state
    print(f"\n--- REDIS CURRENT STATE ---")
    try:
        import redis
        r = redis.Redis()
        data = r.get(f"positions:{account}")
        if data:
            d = json.loads(data)
            pos = d.get("positions", d).get(pk, {})
            print(f"  Redis: amt={pos.get('positionAmt', 'MISSING')} ep={pos.get('entry_price', 0)} opened_at={str(pos.get('opened_at', 'null'))[:19]}")
        else:
            print(f"  Redis: NO DATA for positions:{account}")
    except Exception as e:
        print(f"  Redis error: {e}")
    print(f"\n{'='*120}")
    print("FORENSIC TRACE COMPLETE")
    print(f"{'='*120}")

if __name__ == "__main__":
    main()
