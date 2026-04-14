#!/usr/bin/env python3
"""Compare backtest trades vs live trades for the same time period.
Run AFTER tradier_manage restarts and produces live decisions."""
import json, csv
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

DECISIONS_DIR = Path("/Users/niels/Documents/binance/data/decisions")
BACKTEST_DIR = Path("/Users/niels/Documents/binance/backtest_v5/logs")

def get_live_trades(account="trb", date="20260331"):
    f = DECISIONS_DIR / f"decisions_{account}_{date[:4]}-{date[4:6]}-{date[6:]}.jsonl"
    if not f.exists():
        # Try alternate naming
        for p in DECISIONS_DIR.glob(f"*{account}*{date}*"):
            f = p; break
    if not f.exists():
        return []
    trades = []
    for line in f.read_text().strip().split("\n"):
        if not line: continue
        try:
            d = json.loads(line)
            trades.append(d)
        except: pass
    return trades

def get_backtest_trades():
    trades = []
    for f in sorted(BACKTEST_DIR.glob("full_ALL_trb_*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.stat().st_size < 100: continue
        for line in f.read_text().strip().split("\n"):
            if not line: continue
            try:
                trades.append(json.loads(line))
            except: pass
        break  # Only latest
    return trades

live = get_live_trades()
bt = get_backtest_trades()

print(f"Live trades: {len(live)}")
print(f"Backtest trades: {len(bt)}")
print()

live_exits = Counter(d.get("reason","")[:20] for d in live if "CLOSE" in d.get("action",""))
bt_exits = Counter(d.get("reason","")[:20] for d in bt if d.get("action") == "CLOSE")

print("LIVE EXIT TYPES:")
for k,v in live_exits.most_common(10): print(f"  {k:25s} {v}")
print()
print("BACKTEST EXIT TYPES:")
for k,v in bt_exits.most_common(10): print(f"  {k:25s} {v}")
print()

# Check overlap
live_set = set(live_exits.keys())
bt_set = set(bt_exits.keys())
overlap = live_set & bt_set
only_live = live_set - bt_set
only_bt = bt_set - live_set

print(f"MATCHING exit types: {len(overlap)} — {overlap}")
print(f"ONLY in live: {len(only_live)} — {only_live}")
print(f"ONLY in backtest: {len(only_bt)} — {only_bt}")
