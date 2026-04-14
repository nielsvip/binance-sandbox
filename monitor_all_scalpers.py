#!/usr/bin/env python3
"""Monitor BOTH crypto + stock outlier scalpers. Runs every 5 min via cron.
Logs performance, flags issues, writes actionable recommendations."""
import json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

CRYPTO_LOG = Path("/Users/niels/logs/ez_manage_fin_outlier.log")
STOCK_LOG = Path("/Users/niels/logs/tradier_manage_trc_outlier.log")
REPORT = Path("/Users/niels/Documents/binance/data/scalper_monitor_24h.txt")

def parse_scalper_log(log_file, tag):
    entries = []; exits = []
    if not log_file.exists(): return entries, exits
    for line in open(log_file):
        if tag not in line: continue
        if "ENTER" in line:
            m = re.search(r'ENTER (LONG|SHORT) (\w+) chg=([+\-\d.]+)%.*median=([+\-\d.]+)%', line)
            ts = re.search(r'(\d{2}:\d{2}:\d{2})', line)
            if m and ts: entries.append({"side": m.group(1), "sym": m.group(2), "chg": float(m.group(3)), "med": float(m.group(4)), "time": ts.group(1)})
        elif "EXIT" in line:
            m = re.search(r'EXIT (\w+?)_(LONG|SHORT) (.+)', line)
            ts = re.search(r'(\d{2}:\d{2}:\d{2})', line)
            if m and ts:
                gm = re.search(r'g=([+\-\d.]+)%', m.group(3))
                exits.append({"sym": m.group(1), "side": m.group(2), "gain": float(gm.group(1)) if gm else 0, "reason": m.group(3)[:60], "time": ts.group(1)})
    return entries, exits

def report(tag, entries, exits):
    wins = [e for e in exits if e["gain"] > 0]
    losses = [e for e in exits if e["gain"] <= 0]
    pnl = sum(e["gain"] for e in exits)
    wr = len(wins) / max(len(exits), 1) * 100
    avg_w = sum(e["gain"] for e in wins) / max(len(wins), 1)
    avg_l = sum(e["gain"] for e in losses) / max(len(losses), 1)
    return f"  {tag}: {len(entries)} entries, {len(exits)} exits, WR={wr:.0f}%, PnL={pnl:+.2f}%, AvgWin={avg_w:+.3f}%, AvgLoss={avg_l:+.3f}%, Open={len(entries)-len(exits)}"

now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
ce, cx = parse_scalper_log(CRYPTO_LOG, "OUTLIER_SCALP")
se, sx = parse_scalper_log(STOCK_LOG, "STOCK_OUTLIER")
r = f"\n{'='*70}\n[{now}] SCALPER MONITOR\n{'='*70}\n"
r += report("CRYPTO(fin)", ce, cx) + "\n"
r += report("STOCKS(trc)", se, sx) + "\n"
# Recommendations
cwr = len([e for e in cx if e["gain"]>0]) / max(len(cx),1) * 100
swr = len([e for e in sx if e["gain"]>0]) / max(len(sx),1) * 100
if len(cx) > 10 and cwr < 45: r += "  ⚠️ CRYPTO WR<45% — raise outlier_threshold or tighten WT filter\n"
if len(cx) > 20 and cwr > 70: r += "  ✅ CRYPTO WR>70% — consider lowering threshold for more trades\n"
if len(sx) > 10 and swr < 45: r += "  ⚠️ STOCKS WR<45% — raise threshold or add SMA200 filter\n"
if len(ce) > 50 and len(cx) < 5: r += "  ⚠️ CRYPTO too many entries, few exits — exits not firing\n"
if len(ce) == 0: r += "  ⚠️ CRYPTO no entries — check if fin is running\n"
if len(se) == 0 and 13 <= datetime.now(timezone.utc).hour <= 20: r += "  ⚠️ STOCKS no entries during market hours — check trc\n"
# Last 5 exits
for tag2, exs in [("CRYPTO", cx), ("STOCKS", sx)]:
    if exs:
        r += f"  Last 3 {tag2}: " + " | ".join(f"{e['sym']} {e['gain']:+.2f}%" for e in exs[-3:]) + "\n"
print(r)
REPORT.parent.mkdir(parents=True, exist_ok=True)
with open(REPORT, "a") as f: f.write(r)
