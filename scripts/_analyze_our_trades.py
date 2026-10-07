#!/usr/bin/env python3
"""Analyze OUR actual trade history to find what works and what doesn't."""
import json, os, re
from collections import defaultdict
from pathlib import Path

HISTORY_DIR = Path("/home/niels/binance/data/history")
accounts = ["ang", "inf", "men", "fin", "flz"]

all_trades = []
for acct in accounts:
    acct_dir = HISTORY_DIR / acct
    if not acct_dir.exists():
        continue
    for f in acct_dir.glob("*.jsonl"):
        stem = f.stem
        parts = stem.rsplit("_", 1)
        symbol = parts[0]
        side = parts[1] if len(parts) == 2 else "?"
        seen = set()
        with open(f) as fh:
            for line in fh:
                try:
                    d = json.loads(line.strip())
                    key = (str(d.get("timestamp", ""))[:19], d.get("type", ""), str(d.get("qty", "")))
                    if key in seen:
                        continue
                    seen.add(key)
                    d["account"] = acct
                    d["symbol"] = symbol
                    d["side"] = side
                    all_trades.append(d)
                except Exception:
                    pass

print(f"Total trade events: {len(all_trades)}")

closes = [t for t in all_trades if t.get("type") in ("CLOSE", "REDUCE", "PARTIAL_CLOSE")]
print(f"Close/reduce events: {len(closes)}")

reason_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0, "count": 0})
for c in closes:
    reason = c.get("reason", c.get("origin", "unknown"))
    reason = re.sub(r"_\d+\.\d+%.*", "", reason)
    reason = re.sub(r"_k\d+:.*", "", reason)
    reason = re.sub(r"_ang.*|_inf.*|_men.*|_fin.*|_flz.*", "", reason)
    reason = re.sub(r"QUICK_HEDGE_ELECTED_\w+", "HEDGE_ELECTED_*", reason)
    reason = re.sub(r"SUBSTITUTION_FOR_\w+", "SUBSTITUTION_*", reason)
    reason = reason[:50]
    pnl = float(c.get("realized_pnl", c.get("pnl", 0)) or 0)
    reason_stats[reason]["count"] += 1
    reason_stats[reason]["total_pnl"] += pnl
    if pnl > 0:
        reason_stats[reason]["wins"] += 1
    else:
        reason_stats[reason]["losses"] += 1

sep = "=" * 95
print(f"\n{sep}")
print(f"BEST EXIT REASONS (generating profit in OUR system)")
print(sep)
print(f"{'Reason':>50s} {'Count':>6s} {'WR':>5s} {'Total PnL':>12s} {'Avg':>8s}")
print("-" * 95)
for reason in sorted(reason_stats, key=lambda x: -reason_stats[x]["total_pnl"])[:20]:
    s = reason_stats[reason]
    wr = s["wins"] / s["count"] * 100 if s["count"] else 0
    avg = s["total_pnl"] / s["count"]
    print(f"{reason:>50s} {s['count']:>6d} {wr:>4.0f}% ${s['total_pnl']:>10.2f} ${avg:>6.2f}")

print(f"\n{sep}")
print("WORST EXIT REASONS (destroying value)")
print(sep)
for reason in sorted(reason_stats, key=lambda x: reason_stats[x]["total_pnl"])[:20]:
    s = reason_stats[reason]
    if s["total_pnl"] >= 0:
        continue
    wr = s["wins"] / s["count"] * 100 if s["count"] else 0
    avg = s["total_pnl"] / s["count"]
    print(f"{reason:>50s} {s['count']:>6d} {wr:>4.0f}% ${s['total_pnl']:>10.2f} ${avg:>6.2f}")

# Summary
total_pnl = sum(s["total_pnl"] for s in reason_stats.values())
total_wins = sum(s["wins"] for s in reason_stats.values())
total_losses = sum(s["losses"] for s in reason_stats.values())
total_count = sum(s["count"] for s in reason_stats.values())
print(f"\n{sep}")
print(f"OVERALL: {total_wins}W/{total_losses}L ({total_wins/total_count*100:.1f}% WR) PnL=${total_pnl:.2f}")
print(sep)
