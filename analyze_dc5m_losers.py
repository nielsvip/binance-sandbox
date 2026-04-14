#!/usr/bin/env python3
"""Analyze DC5M entries — what confirmations would filter losers?"""
import json, glob, sys, numpy as np
from collections import defaultdict

files = glob.glob(sys.argv[1]) if len(sys.argv) > 1 else glob.glob("backtest_v5/logs/full_WT_EXIT_trb_*.jsonl")
entries = []
for f in files:
    for line in open(f):
        entries.append(json.loads(line))

opens = sorted([e for e in entries if e["action"] == "OPEN"], key=lambda x: x["ts"])
closes = sorted([e for e in entries if e["action"] == "CLOSE"], key=lambda x: x["ts"])

# Match opens to closes
open_q = defaultdict(list)
for o in opens:
    open_q[f"{o['symbol']}_{o['side']}"].append(o)

matched = []
for c in closes:
    key = f"{c['symbol']}_{c['side']}"
    if open_q[key]:
        o = open_q[key].pop(0)
        matched.append({"open": o, "close": c})

# Separate by entry type
dc5m_long = [m for m in matched if "DC5M" in m["open"]["reason"] and m["open"]["side"] == "LONG"]
dc5m_short = [m for m in matched if "DC5M" in m["open"]["reason"] and m["open"]["side"] == "SHORT"]
dc1h_long = [m for m in matched if "DC1H" in m["open"]["reason"] and m["open"]["side"] == "LONG"]
dc1h_short = [m for m in matched if "DC1H" in m["open"]["reason"] and m["open"]["side"] == "SHORT"]
dc4h = [m for m in matched if "DC4H" in m["open"]["reason"]]
fast_bounce = [m for m in matched if "Fast" in m["open"]["reason"]]
score_short = [m for m in matched if "SHORT_STRONG" in m["open"]["reason"]]
sniper = [m for m in matched if "SNIPER" in m["open"]["reason"]]

def stats(name, trades):
    if not trades:
        print(f"{name}: 0 trades"); return
    wins = sum(1 for t in trades if t["close"]["gain"] > 0)
    pnl = sum(t["close"]["pnl"] for t in trades)
    avg_gain = np.mean([t["close"]["gain"] for t in trades])
    avg_hold = np.mean([(t["close"]["ts"] - t["open"]["ts"]) / 3600 for t in trades])
    loss_pnl = sum(t["close"]["pnl"] for t in trades if t["close"]["gain"] <= 0)
    print(f"{name:30s}: {len(trades):5d} trades, WR={wins/len(trades)*100:.1f}%, PnL=${pnl:8.2f}, AvgGain={avg_gain:+.2f}%, AvgHold={avg_hold:.1f}h, LossPnL=${loss_pnl:.2f}")

print("=" * 120)
print("ENTRY TYPE COMPARISON")
print("=" * 120)
for name, trades in [("DC5M_LONG", dc5m_long), ("DC5M_SHORT", dc5m_short),
                     ("DC1H_LONG", dc1h_long), ("DC1H_SHORT", dc1h_short),
                     ("DC4H", dc4h), ("Fast_Bounce", fast_bounce),
                     ("SHORT_STRONG_SELL", score_short), ("SNIPER", sniper)]:
    stats(name, trades)

# Analyze DC5M losers in detail
print(f"\n{'='*120}")
print("DC5M LOSER ANALYSIS — What went wrong?")
print(f"{'='*120}")

dc5m_all = dc5m_long + dc5m_short
dc5m_losers = [t for t in dc5m_all if t["close"]["gain"] <= 0]
dc5m_winners = [t for t in dc5m_all if t["close"]["gain"] > 0]

print(f"DC5M total: {len(dc5m_all)}, winners: {len(dc5m_winners)}, losers: {len(dc5m_losers)}")
if dc5m_losers:
    print(f"\nWorst DC5M losers:")
    for t in sorted(dc5m_losers, key=lambda x: x["close"]["gain"])[:20]:
        hold_h = (t["close"]["ts"] - t["open"]["ts"]) / 3600
        print(f"  {t['open']['symbol']:8s} {t['open']['side']:5s} entry={t['open']['price']:.2f} exit={t['close']['price']:.2f} gain={t['close']['gain']:+.2f}% pnl=${t['close']['pnl']:.2f} hold={hold_h:.1f}h reason={t['open']['reason'][:50]}")

# Compare DC5M vs DC1H — what's different about them?
print(f"\n{'='*120}")
print("DC1H vs DC5M — Why DC1H wins and DC5M doesn't")
print(f"{'='*120}")
dc1h_all = dc1h_long + dc1h_short
print(f"DC1H: {len(dc1h_all)} trades, {sum(1 for t in dc1h_all if t['close']['gain']>0)/max(1,len(dc1h_all))*100:.1f}% WR")
print(f"DC5M: {len(dc5m_all)} trades, {sum(1 for t in dc5m_all if t['close']['gain']>0)/max(1,len(dc5m_all))*100:.1f}% WR")
print(f"DC1H avg gain: {np.mean([t['close']['gain'] for t in dc1h_all]):.2f}%")
print(f"DC5M avg gain: {np.mean([t['close']['gain'] for t in dc5m_all]):.2f}%")
print(f"DC1H avg hold: {np.mean([(t['close']['ts']-t['open']['ts'])/3600 for t in dc1h_all]):.1f}h")
print(f"DC5M avg hold: {np.mean([(t['close']['ts']-t['open']['ts'])/3600 for t in dc5m_all]):.1f}h")

# Fast bounce analysis
print(f"\n{'='*120}")
print("FAST BOUNCE ANALYSIS")
print(f"{'='*120}")
fb_long = [t for t in fast_bounce if t["open"]["side"] == "LONG"]
fb_short = [t for t in fast_bounce if t["open"]["side"] == "SHORT"]
stats("Fast_Bounce_LONG", fb_long)
stats("Fast_Bounce_SHORT", fb_short)

# What % of fast bounce trades hold < 1 hour?
short_holds = sum(1 for t in fast_bounce if (t["close"]["ts"] - t["open"]["ts"]) < 3600)
print(f"Fast Bounce < 1h hold: {short_holds}/{len(fast_bounce)} ({short_holds/max(1,len(fast_bounce))*100:.0f}%)")
fb_short_hold_wins = sum(1 for t in fast_bounce if (t["close"]["ts"] - t["open"]["ts"]) < 3600 and t["close"]["gain"] > 0)
print(f"Fast Bounce < 1h WR: {fb_short_hold_wins/max(1,short_holds)*100:.1f}%")
