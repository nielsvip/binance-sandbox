#!/usr/bin/env python3
"""Reconstruct REAL P&L from trade history JSONL files.
Matches entry prices to exit prices per position to compute actual gains."""
import json, re, os
from collections import defaultdict
from pathlib import Path

HISTORY_DIR = Path("/home/niels/binance/data/history")
accounts = ["ang", "inf", "men", "fin", "flz"]

# Track P&L per exit reason
reason_pnl = defaultdict(lambda: {"total_pnl": 0.0, "count": 0, "wins": 0, "losses": 0, "total_value": 0})
# Track per position: collect entries and exits
position_data = defaultdict(lambda: {"entries": [], "exits": [], "side": ""})

for acct in accounts:
    acct_dir = HISTORY_DIR / acct
    if not acct_dir.exists():
        continue
    for f in sorted(acct_dir.glob("*.jsonl")):
        stem = f.stem
        parts = stem.rsplit("_", 1)
        symbol = parts[0]
        side = parts[1] if len(parts) == 2 else "?"
        pk = f"{acct}:{stem}"
        events = []
        seen = set()
        with open(f) as fh:
            for line in fh:
                try:
                    d = json.loads(line.strip())
                    key = (str(d.get("ts", ""))[:19], d.get("type", ""), str(d.get("qty", "")))
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(d)
                except Exception:
                    pass
        # Process events chronologically
        avg_entry = 0
        total_qty = 0
        for ev in events:
            evt_type = ev.get("type", "")
            price = float(ev.get("price", 0) or 0)
            qty = float(ev.get("qty", 0) or 0)
            reason = ev.get("reason", ev.get("origin", "unknown"))
            if evt_type in ("OPEN", "AUGMENT", "ADD") and price > 0 and qty > 0:
                # Entry — update average entry price
                old_value = avg_entry * total_qty
                total_qty += qty
                avg_entry = (old_value + price * qty) / total_qty if total_qty > 0 else price
            elif evt_type in ("CLOSE", "REDUCE", "PARTIAL_CLOSE") and price > 0 and qty > 0:
                if avg_entry <= 0 or total_qty <= 0:
                    continue
                # Calculate P&L based on side
                if side == "LONG":
                    pnl_pct = (price - avg_entry) / avg_entry * 100
                elif side == "SHORT":
                    pnl_pct = (avg_entry - price) / avg_entry * 100
                else:
                    continue
                pnl_usd = pnl_pct / 100 * price * qty
                # Normalize reason
                reason_norm = re.sub(r"_\d+\.\d+%.*", "", reason)
                reason_norm = re.sub(r"_k\d+:.*", "", reason)
                reason_norm = re.sub(r"_ang.*|_inf.*|_men.*|_fin.*|_flz.*", "", reason_norm)
                reason_norm = re.sub(r"QUICK_HEDGE_ELECTED_\w+", "HEDGE_ELECTED_*", reason_norm)
                reason_norm = re.sub(r"SUBSTITUTION_FOR_\w+:\w+", "SUBSTITUTION_*", reason_norm)
                reason_norm = reason_norm[:50]
                reason_pnl[reason_norm]["total_pnl"] += pnl_usd
                reason_pnl[reason_norm]["count"] += 1
                reason_pnl[reason_norm]["total_value"] += price * qty
                if pnl_usd > 0:
                    reason_pnl[reason_norm]["wins"] += 1
                else:
                    reason_pnl[reason_norm]["losses"] += 1
                total_qty = max(0, total_qty - qty)

print("=" * 100)
print("BEST EXIT REASONS (most profitable in OUR system)")
print("=" * 100)
print(f"{'Reason':>50s} {'Count':>6s} {'WR':>5s} {'Total PnL':>12s} {'Avg PnL':>10s} {'Value':>10s}")
print("-" * 100)
for reason in sorted(reason_pnl, key=lambda x: -reason_pnl[x]["total_pnl"])[:25]:
    s = reason_pnl[reason]
    wr = s["wins"] / s["count"] * 100 if s["count"] else 0
    avg = s["total_pnl"] / s["count"]
    print(f"{reason:>50s} {s['count']:>6d} {wr:>4.0f}% ${s['total_pnl']:>10.2f} ${avg:>8.2f} ${s['total_value']:>8.0f}")

print()
print("=" * 100)
print("WORST EXIT REASONS (destroying value)")
print("=" * 100)
worst = sorted(reason_pnl, key=lambda x: reason_pnl[x]["total_pnl"])
for reason in worst[:25]:
    s = reason_pnl[reason]
    if s["total_pnl"] >= 0:
        continue
    wr = s["wins"] / s["count"] * 100 if s["count"] else 0
    avg = s["total_pnl"] / s["count"]
    print(f"{reason:>50s} {s['count']:>6d} {wr:>4.0f}% ${s['total_pnl']:>10.2f} ${avg:>8.2f}")

total_pnl = sum(s["total_pnl"] for s in reason_pnl.values())
total_wins = sum(s["wins"] for s in reason_pnl.values())
total_losses = sum(s["losses"] for s in reason_pnl.values())
total_count = sum(s["count"] for s in reason_pnl.values())
print()
print("=" * 100)
wr = total_wins / total_count * 100 if total_count else 0
print(f"OVERALL: {total_count} exits | {total_wins}W/{total_losses}L ({wr:.1f}% WR) | PnL=${total_pnl:.2f}")
print("=" * 100)
