#!/usr/bin/env python3
"""Monitor Satoshit2024 strategy trades in real-time. Run via: python monitor_satoshit.py"""
import glob
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
LOG_DIR = Path("/Users/niels/logs")
DECISIONS_DIR = BASE / "data" / "decisions"

def scan_logs_for_satoshit(minutes_back=10):
    """Scan recent log lines for SATOSHIT entries/exits."""
    entries = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes_back)
    for pattern in ["ez_manage_*_watchdog.log", "ez_manage_*cron*.log", "ez_positions_quick_general_*.log", "quick_*_process.log"]:
        for logfile in glob.glob(str(LOG_DIR / pattern)):
            try:
                with open(logfile) as f:
                    lines = f.readlines()
                for line in lines[-500:]:
                    if "SATOSHIT" in line and "NO_RESULT" not in line:
                        entries.append({"file": os.path.basename(logfile), "line": line.strip()})
            except Exception:
                pass
    return entries

def scan_decisions_for_satoshit():
    """Scan today's decision JSONL files for SATOSHIT trades."""
    trades = []
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for f in glob.glob(str(DECISIONS_DIR / f"decisions_*_{today}.jsonl")):
        try:
            with open(f) as fh:
                for line in fh:
                    if "SATOSHIT" in line:
                        try:
                            d = json.loads(line)
                            trades.append(d)
                        except Exception:
                            pass
        except Exception:
            pass
    return trades

def get_positions_with_satoshit_tag():
    """Check position files for any with SATOSHIT in augment_reason."""
    tagged = []
    positions_dir = BASE / "data" / "positions"
    if not positions_dir.exists():
        return tagged
    for f in positions_dir.glob("*.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for key, pos in data.items():
                    if isinstance(pos, dict):
                        aug = pos.get("augment_reason", "") or ""
                        sig = pos.get("last_signal", "") or ""
                        if "SATOSHIT" in aug or "SATOSHIT" in sig:
                            gain = pos.get("gain", 0)
                            amt = pos.get("positionAmt", 0)
                            entry = pos.get("entry_price", 0)
                            tagged.append({"key": key, "gain": gain, "positionAmt": amt, "entry_price": entry, "augment_reason": aug, "last_signal": sig})
        except Exception:
            pass
    return tagged

def main():
    now = datetime.now(timezone.utc)
    print(f"\n{'='*100}")
    print(f"  SATOSHIT MONITOR — {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*100}")
    # 1. Check log activity
    log_entries = scan_logs_for_satoshit(minutes_back=60)
    print(f"\n  Log entries with SATOSHIT (last 60min): {len(log_entries)}")
    if log_entries:
        for e in log_entries[-10:]:
            print(f"    [{e['file']}] {e['line'][:150]}")
    else:
        print(f"    (none yet — strategy hasn't triggered)")
    # 2. Check decision JSONL
    decisions = scan_decisions_for_satoshit()
    print(f"\n  SATOSHIT decisions today: {len(decisions)}")
    if decisions:
        wins = sum(1 for d in decisions if float(d.get("pnl", d.get("gain", 0)) or 0) > 0)
        losses = sum(1 for d in decisions if float(d.get("pnl", d.get("gain", 0)) or 0) < 0)
        total_pnl = sum(float(d.get("pnl", 0) or 0) for d in decisions)
        print(f"    Wins: {wins} | Losses: {losses} | Total PnL: ${total_pnl:+.2f}")
        for d in decisions[-5:]:
            print(f"    {d.get('timestamp', '?')[:19]} {d.get('position_key', '?')[:30]} {d.get('action', '?')} {d.get('reason', '?')[:80]}")
    # 3. Check active positions with SATOSHIT tag
    tagged = get_positions_with_satoshit_tag()
    print(f"\n  Active positions tagged SATOSHIT: {len(tagged)}")
    if tagged:
        total_gain = 0
        print(f"    {'Position Key':<35} {'Gain%':>8} {'Amt':>12} {'Entry':>12} {'Reason'}")
        print(f"    {'─'*35} {'─'*8} {'─'*12} {'─'*12} {'─'*50}")
        for p in sorted(tagged, key=lambda x: x.get("gain", 0), reverse=True):
            gain = float(p.get("gain", 0) or 0)
            total_gain += gain
            print(f"    {p['key']:<35} {gain:>+7.2f}% {float(p.get('positionAmt', 0)):>12.6f} {float(p.get('entry_price', 0)):>12.4f} {p.get('augment_reason', '')[:50]}")
        print(f"\n    Portfolio gain: {total_gain:+.2f}% across {len(tagged)} positions")
    # 4. Quick system health
    print(f"\n  System processes:")
    import subprocess
    result = subprocess.run(["pgrep", "-fl", "ez_manage"], capture_output=True, text=True)
    manage_count = len([l for l in result.stdout.strip().split("\n") if l and "grep" not in l and "ez_manage.py" in l])
    result2 = subprocess.run(["pgrep", "-fl", "ez_positions_quick"], capture_output=True, text=True)
    quick_count = len([l for l in result2.stdout.strip().split("\n") if l and "grep" not in l and "ez_positions_quick" in l])
    print(f"    ez_manage processes: {manage_count}")
    print(f"    ez_positions_quick processes: {quick_count}")
    print(f"\n{'='*100}")

if __name__ == "__main__":
    main()
