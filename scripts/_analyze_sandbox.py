#!/usr/bin/env python3
import json
from collections import defaultdict

with open("data/poly/limitless_sandbox/positions.json") as f:
    d = json.load(f)
cl = d.get("closed", [])
print(f"Balance: ${d.get('balance', 0):.2f} / Started: $100")
print(f"Closed: {len(cl)}")
wins = [c for c in cl if c.get("won")]
losses = [c for c in cl if not c.get("won")]
print(f"Record: {len(wins)}W/{len(losses)}L ({len(wins)/len(cl)*100:.1f}% WR)" if cl else "No trades")
if wins:
    print(f"Avg win entry: {sum(c['entry'] for c in wins)/len(wins):.3f} avg_pnl: ${sum(c['pnl'] for c in wins)/len(wins):.2f}")
if losses:
    print(f"Avg loss entry: {sum(c['entry'] for c in losses)/len(losses):.3f} avg_pnl: ${sum(c['pnl'] for c in losses)/len(losses):.2f}")

by_entry = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0})
for c in cl:
    e = c.get("entry", 0)
    if e < 0.2: bucket = "<20%"
    elif e < 0.4: bucket = "20-40%"
    elif e < 0.6: bucket = "40-60%"
    elif e < 0.8: bucket = "60-80%"
    else: bucket = "80-100%"
    by_entry[bucket]["w" if c.get("won") else "l"] += 1
    by_entry[bucket]["pnl"] += c.get("pnl", 0)
print("\nBy entry price:")
for k in sorted(by_entry):
    v = by_entry[k]
    t = v["w"] + v["l"]
    print(f"  {k:>8s}: {v['w']}W/{v['l']}L = {v['w']/t*100:.0f}% pnl=${v['pnl']:+.2f}")

by_tk = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0})
for c in cl:
    by_tk[c.get("ticker", "?")]["w" if c.get("won") else "l"] += 1
    by_tk[c.get("ticker", "?")]["pnl"] += c.get("pnl", 0)
print("\nBy ticker:")
for k in sorted(by_tk, key=lambda x: by_tk[x]["pnl"]):
    v = by_tk[k]
    t = v["w"] + v["l"]
    print(f"  {k:>5s}: {v['w']}W/{v['l']}L = {v['w']/t*100:.0f}% pnl=${v['pnl']:+.2f}")

by_side = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0})
for c in cl:
    by_side[c.get("side", "?")]["w" if c.get("won") else "l"] += 1
    by_side[c.get("side", "?")]["pnl"] += c.get("pnl", 0)
print("\nBy side:")
for k in sorted(by_side):
    v = by_side[k]
    t = v["w"] + v["l"]
    print(f"  {k:>4s}: {v['w']}W/{v['l']}L = {v['w']/t*100:.0f}% pnl=${v['pnl']:+.2f}")

# Resolution source
by_res = defaultdict(int)
for c in cl:
    by_res["api" if c.get("resolved_via") == "api" else "binance"] += 1
print(f"\nResolution source: {dict(by_res)}")

print("\nLast 15 trades:")
for c in cl[-15:]:
    tag = "W" if c.get("won") else "L"
    print(f"  {tag} {c['side']:>3s} {c['ticker']:>5s} e={c['entry']:.3f} pnl=${c['pnl']:+.2f} ev={c.get('ev_edge',0):.0f}% | strike={c['strike']:.4f} spot={c['spot']:.4f}")
