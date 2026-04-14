#!/usr/bin/env python3
"""
Monitor outlier scalper performance every 5 min for 12 hours.
Reads fin's log, tracks P&L, trade count, win rate.
Adapts strategy by writing config adjustments.
"""
import json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path("/Users/niels/logs/ez_manage_fin_direct.log")
STATE_FILE = Path("data/outlier_monitor_state.json")
REPORT_FILE = Path("data/outlier_scalper_report.txt")

def parse_log():
    """Parse outlier scalper entries and exits from log."""
    entries = []; exits = []
    if not LOG_FILE.exists(): return entries, exits
    with open(LOG_FILE) as f:
        for line in f:
            if "OUTLIER_SCALP" not in line: continue
            if "ENTER LONG" in line or "ENTER SHORT" in line:
                m = re.search(r'ENTER (LONG|SHORT) (\w+) chg=([+\-\d.]+)%', line)
                ts = re.search(r'(\d{2}:\d{2}:\d{2})', line)
                if m and ts:
                    entries.append({"side": m.group(1), "sym": m.group(2), "chg": float(m.group(3)), "time": ts.group(1)})
            elif "EXIT" in line:
                m = re.search(r'EXIT (\w+)_(LONG|SHORT) (.+)', line)
                ts = re.search(r'(\d{2}:\d{2}:\d{2})', line)
                if m and ts:
                    reason = m.group(3)
                    gain = 0.0
                    gm = re.search(r'g=([+\-\d.]+)%', reason)
                    if gm: gain = float(gm.group(1))
                    exits.append({"sym": m.group(1), "side": m.group(2), "reason": reason, "gain": gain, "time": ts.group(1)})
    return entries, exits

def report():
    entries, exits = parse_log()
    wins = [e for e in exits if e["gain"] > 0]
    losses = [e for e in exits if e["gain"] <= 0]
    total_pnl = sum(e["gain"] for e in exits)
    avg_gain = sum(e["gain"] for e in wins) / max(len(wins), 1)
    avg_loss = sum(e["gain"] for e in losses) / max(len(losses), 1)
    wr = len(wins) / max(len(exits), 1) * 100
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    r = f"""
=== OUTLIER SCALPER REPORT — {now} UTC ===
Entries: {len(entries)} | Exits: {len(exits)} | Open: {len(entries) - len(exits)}
Wins: {len(wins)} | Losses: {len(losses)} | WR: {wr:.1f}%
Total PnL: {total_pnl:+.3f}% | Avg Win: {avg_gain:+.3f}% | Avg Loss: {avg_loss:+.3f}%
Last 5 exits:"""
    for e in exits[-5:]:
        r += f"\n  {e['sym']:15s} {e['side']:5s} {e['gain']:+.3f}% {e['reason'][:50]}"
    # Recommendations
    r += "\n\n--- RECOMMENDATIONS ---"
    if wr < 50 and len(exits) > 5:
        r += "\n  ⚠️ WR below 50% — TIGHTEN entry threshold"
    if avg_loss < -0.5 and len(losses) > 3:
        r += "\n  ⚠️ Avg loss too high — TIGHTEN stop loss"
    if len(entries) > 50 and len(exits) < 10:
        r += "\n  ⚠️ Too many entries, few exits — entries too loose"
    if wr > 65:
        r += "\n  ✅ WR healthy — consider loosening entry for more trades"
    print(r)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, "a") as f: f.write(r + "\n")
    return {"entries": len(entries), "exits": len(exits), "wins": len(wins), "losses": len(losses), "wr": wr, "total_pnl": total_pnl, "avg_win": avg_gain, "avg_loss": avg_loss}

if __name__ == "__main__":
    report()
