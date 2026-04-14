#!/usr/bin/env python3
# pylint: disable=W,C,R
"""
HEDGE MONITOR — Daily 21:00 UTC report comparing hedge vs no-hedge performance.
Reads decision JSONL files to track:
  1. Hedge trades opened/closed today
  2. Hedge PnL (realized gains from hedge positions)
  3. Positions saved by hedging (losers that recovered because hedge was in place)
  4. Cumulative comparison: before (2026-03-27 baseline) vs after (hedge enabled 2026-03-28+)

Baseline date: 2026-03-27 — last full day without active hedging.
Output: logs/hedge_monitor_daily.log + data/hedge_monitor/daily_YYYYMMDD.json
"""
import json, os, sys, time, logging, platform
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
DECISIONS_DIR = BASE_PATH / "data" / "decisions"
MONITOR_DIR = BASE_PATH / "data" / "hedge_monitor"
MONITOR_DIR.mkdir(parents=True, exist_ok=True)
BASELINE_DATE = "20260327"  # Last full day before hedge system enabled
HEDGE_START_DATE = "20260328"  # First full day with new hedge system
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
LOG_FILE = Path("/Users/niels/logs/hedge_monitor_daily.log") if platform.system() == "Darwin" else Path("/home/niels/logs/hedge_monitor_daily.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [HEDGE_MON] %(message)s", handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(str(LOG_FILE), mode="a")])
log = logging.getLogger(__name__)


def parse_decisions_for_date(date_str: str) -> dict:
    """Parse all decision JSONL files for a given date. Returns summary stats."""
    stats = {
        "date": date_str,
        "total_trades": 0,
        "hedge_opens": 0,
        "hedge_closes": 0,
        "hedge_reduces": 0,
        "hedge_pnl_sum": 0.0,
        "hedge_wins": 0,
        "hedge_losses": 0,
        "non_hedge_trades": 0,
        "non_hedge_closes": 0,
        "non_hedge_pnl_sum": 0.0,
        "non_hedge_wins": 0,
        "non_hedge_losses": 0,
        "positions_with_active_hedge": set(),
        "hedge_symbols": set(),
        "accounts_active": set(),
        "hedge_details": [],
    }
    for account in CRYPTO_ACCOUNTS:
        fpath = DECISIONS_DIR / f"decisions_{account}_{date_str}.jsonl"
        if not fpath.exists():
            continue
        stats["accounts_active"].add(account)
        with open(fpath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stats["total_trades"] += 1
                extra = d.get("extra", {})
                is_hedge = extra.get("is_hedge", False)
                hedge_for = extra.get("hedge_for", None)
                action = d.get("action", "")
                reason = d.get("reason", "")
                pk = d.get("position_key", "")
                snap = d.get("snapshot", {})
                # Extract gain from reason string if present
                gain_pct = _extract_gain(reason)
                if is_hedge or "HEDGE" in reason.upper():
                    if action == "OPEN" or "OPEN" in action:
                        stats["hedge_opens"] += 1
                        if hedge_for:
                            stats["positions_with_active_hedge"].add(hedge_for)
                        sym = pk.split(":")[-1].replace("_LONG", "").replace("_SHORT", "") if ":" in pk else pk
                        stats["hedge_symbols"].add(sym)
                    elif action in ("CLOSE", "REDUCE"):
                        if action == "CLOSE":
                            stats["hedge_closes"] += 1
                        else:
                            stats["hedge_reduces"] += 1
                        if gain_pct is not None:
                            stats["hedge_pnl_sum"] += gain_pct
                            if gain_pct > 0:
                                stats["hedge_wins"] += 1
                            else:
                                stats["hedge_losses"] += 1
                    stats["hedge_details"].append({"time": d.get("timestamp", ""), "pk": pk, "action": action, "reason": reason[:80], "gain": gain_pct})
                else:
                    stats["non_hedge_trades"] += 1
                    if action in ("CLOSE", "REDUCE"):
                        stats["non_hedge_closes"] += 1
                        if gain_pct is not None:
                            stats["non_hedge_pnl_sum"] += gain_pct
                            if gain_pct > 0:
                                stats["non_hedge_wins"] += 1
                            else:
                                stats["non_hedge_losses"] += 1
    # Convert sets to lists for JSON serialization
    stats["positions_with_active_hedge"] = list(stats["positions_with_active_hedge"])
    stats["hedge_symbols"] = sorted(stats["hedge_symbols"])
    stats["accounts_active"] = sorted(stats["accounts_active"])
    return stats


def _extract_gain(reason: str):
    """Extract gain% from reason strings like 'TP_3.20%' or 'gain_1.50%' or 'HEDGE_KILL_PREEMPTIVE_0.08%'."""
    import re
    # Match patterns like _1.50% or _-0.30% or gain=1.50
    patterns = [
        r'[-]?\d+\.\d+%',  # 3.20% or -0.30%
        r'gain[=_]([-]?\d+\.\d+)',  # gain=1.50 or gain_1.50
    ]
    for pat in patterns:
        m = re.search(pat, reason)
        if m:
            val = m.group(0).replace('%', '').replace('gain=', '').replace('gain_', '')
            try:
                return float(val)
            except ValueError:
                continue
    return None


def load_history() -> list:
    """Load all daily summaries for trend analysis."""
    history = []
    for f in sorted(MONITOR_DIR.glob("daily_*.json")):
        try:
            data = json.loads(f.read_text())
            history.append(data)
        except Exception:
            continue
    return history


def generate_report(today_stats: dict, history: list) -> str:
    """Generate the daily comparison report."""
    lines = []
    lines.append("=" * 80)
    lines.append(f"  HEDGE MONITOR — Daily Report {today_stats['date']}")
    lines.append(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append("=" * 80)
    # Today's hedge activity
    lines.append("\n── TODAY'S HEDGE ACTIVITY ──")
    lines.append(f"  Hedge opens:    {today_stats['hedge_opens']}")
    lines.append(f"  Hedge closes:   {today_stats['hedge_closes']}")
    lines.append(f"  Hedge reduces:  {today_stats['hedge_reduces']}")
    h_total = today_stats['hedge_wins'] + today_stats['hedge_losses']
    h_wr = today_stats['hedge_wins'] / max(1, h_total) * 100
    lines.append(f"  Hedge W/L:      {today_stats['hedge_wins']}W / {today_stats['hedge_losses']}L ({h_wr:.0f}% WR)")
    lines.append(f"  Hedge PnL sum:  {today_stats['hedge_pnl_sum']:+.2f}%")
    lines.append(f"  Symbols hedged: {', '.join(today_stats['hedge_symbols'][:10]) or 'none'}")
    lines.append(f"  Positions with active hedge: {len(today_stats['positions_with_active_hedge'])}")
    # Today's regular trading
    lines.append("\n── TODAY'S REGULAR TRADING ──")
    lines.append(f"  Total trades:   {today_stats['non_hedge_trades']}")
    lines.append(f"  Closes:         {today_stats['non_hedge_closes']}")
    nh_total = today_stats['non_hedge_wins'] + today_stats['non_hedge_losses']
    nh_wr = today_stats['non_hedge_wins'] / max(1, nh_total) * 100
    lines.append(f"  W/L:            {today_stats['non_hedge_wins']}W / {today_stats['non_hedge_losses']}L ({nh_wr:.0f}% WR)")
    lines.append(f"  PnL sum:        {today_stats['non_hedge_pnl_sum']:+.2f}%")
    # Comparison with baseline and trend
    if len(history) >= 2:
        lines.append("\n── TREND: HEDGE SYSTEM PERFORMANCE ──")
        # Split into pre-hedge and post-hedge periods
        pre_hedge = [h for h in history if h["date"] < HEDGE_START_DATE]
        post_hedge = [h for h in history if h["date"] >= HEDGE_START_DATE]
        if pre_hedge and post_hedge:
            pre_avg_pnl = sum(h["non_hedge_pnl_sum"] for h in pre_hedge) / len(pre_hedge)
            post_avg_pnl = sum(h["non_hedge_pnl_sum"] + h["hedge_pnl_sum"] for h in post_hedge) / len(post_hedge)
            pre_avg_trades = sum(h["non_hedge_trades"] for h in pre_hedge) / len(pre_hedge)
            post_avg_trades = sum(h["non_hedge_trades"] + h["hedge_opens"] + h["hedge_closes"] for h in post_hedge) / len(post_hedge)
            post_avg_hedge_pnl = sum(h["hedge_pnl_sum"] for h in post_hedge) / len(post_hedge)
            post_avg_hedge_opens = sum(h["hedge_opens"] for h in post_hedge) / len(post_hedge)
            lines.append(f"  Pre-hedge days:  {len(pre_hedge)} | Avg daily PnL: {pre_avg_pnl:+.2f}% | Avg trades: {pre_avg_trades:.0f}")
            lines.append(f"  Post-hedge days: {len(post_hedge)} | Avg daily PnL: {post_avg_pnl:+.2f}% | Avg trades: {post_avg_trades:.0f}")
            lines.append(f"  Hedge contribution: {post_avg_hedge_pnl:+.2f}%/day avg | {post_avg_hedge_opens:.0f} hedges/day avg")
            delta = post_avg_pnl - pre_avg_pnl
            lines.append(f"  DELTA (post - pre): {delta:+.2f}%/day {'BETTER' if delta > 0 else 'WORSE'}")
        # Rolling 3-day comparison
        if len(post_hedge) >= 3:
            last3 = post_hedge[-3:]
            l3_pnl = sum(h["non_hedge_pnl_sum"] + h["hedge_pnl_sum"] for h in last3) / 3
            l3_hedge = sum(h["hedge_pnl_sum"] for h in last3) / 3
            lines.append(f"\n  Last 3 days avg PnL: {l3_pnl:+.2f}% (hedge contribution: {l3_hedge:+.2f}%)")
    # Top hedge trades today (by gain)
    if today_stats.get("hedge_details"):
        closes = [d for d in today_stats["hedge_details"] if d["action"] in ("CLOSE", "REDUCE") and d.get("gain") is not None]
        if closes:
            closes.sort(key=lambda x: x.get("gain", 0), reverse=True)
            lines.append("\n── TOP HEDGE TRADES TODAY ──")
            for d in closes[:5]:
                lines.append(f"  {d['pk']:<35} {d['action']:<7} gain={d['gain']:+.2f}%  {d['reason'][:50]}")
            if len(closes) > 5:
                worst = closes[-3:]
                lines.append("  --- Worst ---")
                for d in worst:
                    lines.append(f"  {d['pk']:<35} {d['action']:<7} gain={d['gain']:+.2f}%  {d['reason'][:50]}")
    lines.append("\n" + "=" * 80)
    # Verdict
    total_hedge_pnl = today_stats["hedge_pnl_sum"]
    if total_hedge_pnl > 0:
        lines.append(f"  VERDICT: HEDGE SYSTEM PROFITABLE TODAY (+{total_hedge_pnl:.2f}%)")
    elif total_hedge_pnl == 0 and today_stats["hedge_opens"] == 0:
        lines.append("  VERDICT: No hedges triggered today")
    elif total_hedge_pnl > -0.5:
        lines.append(f"  VERDICT: Hedge system slightly negative ({total_hedge_pnl:+.2f}%) — normal fee drag")
    else:
        lines.append(f"  VERDICT: HEDGE SYSTEM LOSING ({total_hedge_pnl:+.2f}%) — REVIEW NEEDED")
    lines.append("=" * 80)
    return "\n".join(lines)


def backfill_baseline():
    """Parse pre-hedge dates to build baseline comparison data."""
    # Backfill last 7 days before hedge enable
    start = datetime.strptime(BASELINE_DATE, "%Y%m%d")
    for i in range(7):
        dt = start - timedelta(days=i)
        date_str = dt.strftime("%Y%m%d")
        out_file = MONITOR_DIR / f"daily_{date_str}.json"
        if out_file.exists():
            continue
        stats = parse_decisions_for_date(date_str)
        if stats["total_trades"] > 0:
            # Remove non-serializable fields
            stats.pop("hedge_details", None)
            out_file.write_text(json.dumps(stats, indent=2))
            log.info(f"Backfilled {date_str}: {stats['total_trades']} trades")


def main():
    log.info("=" * 60)
    log.info("HEDGE MONITOR — Starting daily report")
    # Backfill baseline data if needed
    backfill_baseline()
    # Parse today
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    today_stats = parse_decisions_for_date(today)
    # Save today's data
    out_file = MONITOR_DIR / f"daily_{today}.json"
    save_stats = {k: v for k, v in today_stats.items() if k != "hedge_details"}
    out_file.write_text(json.dumps(save_stats, indent=2))
    # Load history for trend
    history = load_history()
    # Generate and print report
    report = generate_report(today_stats, history)
    print(report)
    log.info(report)
    log.info("Report complete")


if __name__ == "__main__":
    main()
