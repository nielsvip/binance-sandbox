#!/usr/bin/env python3
"""poly_report.py — Hourly P&L report for live + paper mirror Polymarket accounts."""

import json
from datetime import datetime, timezone
from pathlib import Path

LIVE_FILE = Path("./data/poly/highconf/live_positions.json")
PAPER_FILE = Path("./data/poly/paper_mirror/paper_positions.json")


def report(label: str, path: Path) -> str:
    if not path.exists():
        return f"{label}: no data yet"
    try:
        d = json.loads(path.read_text())
    except Exception as e:
        return f"{label}: read error {e}"

    positions = d.get("positions", {})
    closed = d.get("closed", [])

    open_exp = sum(p.get("bet_usdc", 0) for p in positions.values())
    wins = [c for c in closed if c.get("pnl_usdc", 0) > 0]
    losses = [c for c in closed if c.get("pnl_usdc", 0) <= 0]
    total_pnl = sum(c.get("pnl_usdc", 0) for c in closed)
    total_bet_closed = sum(c.get("bet_usdc", 0) for c in closed)
    roi = total_pnl / total_bet_closed * 100 if total_bet_closed else 0
    wr = len(wins) / len(closed) * 100 if closed else 0

    by_cat = {}
    for c in closed:
        cat = c.get("cat", "other")
        if cat not in by_cat:
            by_cat[cat] = {"n": 0, "wins": 0, "pnl": 0.0}
        by_cat[cat]["n"] += 1
        by_cat[cat]["pnl"] += c.get("pnl_usdc", 0)
        if c.get("pnl_usdc", 0) > 0:
            by_cat[cat]["wins"] += 1

    lines = [
        f"── {label} ──",
        f"  Resolved : {len(closed)} trades  ({len(wins)}W / {len(losses)}L  {wr:.0f}% WR)",
        f"  Net P&L  : ${total_pnl:+.4f}  ROI {roi:+.2f}%  (on ${total_bet_closed:.0f} closed)",
        f"  Open     : {len(positions)} positions  ${open_exp:.0f} exposure",
    ]
    if by_cat:
        lines.append("  By category:")
        for cat, s in sorted(by_cat.items(), key=lambda x: -abs(x[1]["pnl"])):
            cat_wr = s["wins"] / s["n"] * 100
            lines.append(f"    {cat:<18} {s['n']:>3} trades  {cat_wr:.0f}% WR  ${s['pnl']:+.4f}")
    return "\n".join(lines)


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*55}")
    print(f"POLYMARKET HOURLY REPORT — {now}")
    print(f"{'='*55}")
    print(report("LIVE ($5 real bets)", LIVE_FILE))
    print()
    print(report("PAPER MIRROR ($5 simulated, same filters)", PAPER_FILE))
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
