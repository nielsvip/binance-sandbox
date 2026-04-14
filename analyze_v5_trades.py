#!/usr/bin/env python3
"""Analyze V5 backtest trade JSONL files."""
import json, glob, sys
from collections import Counter, defaultdict

files = glob.glob(sys.argv[1]) if len(sys.argv) > 1 else glob.glob("backtest_v5/logs/full_WT_EXIT_trb_*.jsonl")
entries = []
for f in files:
    for line in open(f):
        entries.append(json.loads(line))

opens = [e for e in entries if e["action"] == "OPEN"]
closes = [e for e in entries if e["action"] == "CLOSE"]
augments = [e for e in entries if e["action"] == "AUGMENT"]

print(f"Total entries: {len(entries)}")
print(f"Opens: {len(opens)}, Closes: {len(closes)}, Augments: {len(augments)}")

if not closes:
    print("No closed trades to analyze.")
    sys.exit(0)

winning = [c for c in closes if c["gain"] > 0]
losing = [c for c in closes if c["gain"] <= 0]
print(f"\nWinning: {len(winning)} ({len(winning)/len(closes)*100:.1f}%)")
print(f"Losing:  {len(losing)} ({len(losing)/len(closes)*100:.1f}%)")
print(f"Avg win PnL: ${sum(c['pnl'] for c in winning)/max(1,len(winning)):.2f}")
print(f"Avg loss PnL: ${sum(c['pnl'] for c in losing)/max(1,len(losing)):.2f}")
print(f"Total PnL: ${sum(c['pnl'] for c in closes):.2f}")

# Direction analysis
long_c = [c for c in closes if c["side"] == "LONG"]
short_c = [c for c in closes if c["side"] == "SHORT"]
long_w = sum(1 for c in long_c if c["gain"] > 0)
short_w = sum(1 for c in short_c if c["gain"] > 0)
print(f"\n--- DIRECTION ---")
print(f"LONG:  {len(long_c)} trades, WR={long_w/max(1,len(long_c))*100:.1f}%, PnL=${sum(c['pnl'] for c in long_c):.2f}")
print(f"SHORT: {len(short_c)} trades, WR={short_w/max(1,len(short_c))*100:.1f}%, PnL=${sum(c['pnl'] for c in short_c):.2f}")

long_o = sum(1 for o in opens if o["side"] == "LONG")
short_o = sum(1 for o in opens if o["side"] == "SHORT")
print(f"LONG opens: {long_o} ({long_o/max(1,len(opens))*100:.0f}%)")
print(f"SHORT opens: {short_o} ({short_o/max(1,len(opens))*100:.0f}%)")

# Entry reason breakdown with win rates
print(f"\n--- ENTRY REASONS ---")
# Match closes back to their entry reasons (same symbol+side, nearest prior open)
open_queue = defaultdict(list)
for o in sorted(opens, key=lambda x: x["ts"]):
    key = f"{o['symbol']}_{o['side']}"
    open_queue[key].append(o)

reason_stats = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "losses_pnl": 0.0})
for c in sorted(closes, key=lambda x: x["ts"]):
    key = f"{c['symbol']}_{c['side']}"
    if open_queue[key]:
        matched_open = open_queue[key].pop(0)
        reason = matched_open["reason"].split(":")[0] if ":" in matched_open["reason"] else matched_open["reason"].split("_")[0]
        reason_stats[reason]["n"] += 1
        reason_stats[reason]["pnl"] += c["pnl"]
        if c["gain"] > 0:
            reason_stats[reason]["wins"] += 1
        else:
            reason_stats[reason]["losses_pnl"] += c["pnl"]

print(f"{'Reason':35s} {'N':>5s} {'WR':>6s} {'PnL':>10s} {'LossPnL':>10s}")
for r, s in sorted(reason_stats.items(), key=lambda x: -x[1]["pnl"]):
    wr = s["wins"] / max(1, s["n"]) * 100
    print(f"  {r:33s} {s['n']:5d} {wr:5.1f}% ${s['pnl']:9.2f} ${s['losses_pnl']:9.2f}")

# Avg hold time (bars between open and close)
print(f"\n--- HOLD TIME ---")
hold_bars = []
for c in closes:
    key = f"{c['symbol']}_{c['side']}"
    # Find prior opens
    priors = [o for o in opens if o["symbol"] == c["symbol"] and o["side"] == c["side"] and o["ts"] <= c["ts"]]
    if priors:
        last_open = max(priors, key=lambda x: x["ts"])
        bars = (c["ts"] - last_open["ts"]) / 900  # 15m bars
        hold_bars.append(bars)
if hold_bars:
    import numpy as np
    arr = np.array(hold_bars)
    print(f"Avg hold: {np.mean(arr):.1f} bars ({np.mean(arr)*15/60:.1f} hours)")
    print(f"Median hold: {np.median(arr):.1f} bars ({np.median(arr)*15/60:.1f} hours)")
    print(f"Max hold: {np.max(arr):.0f} bars ({np.max(arr)*15/60:.0f} hours)")
