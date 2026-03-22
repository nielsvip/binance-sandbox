#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
TRADE BLOCK MONITOR — Real-time monitoring of blocked/missed trades across all accounts.
Parses decision JSONL + log files to show what's being blocked and why.

Features:
  - Per-account breakdown of BLOCKED, BOYCOTT, WAIT, SKIPPED events
  - Tracks which signals qualified but couldn't execute
  - Shows execution success rate per account
  - Identifies recurring block patterns (e.g. always STALE_INDICATORS)
  - Monitors both crypto (ez_) and stock (tradier_) systems
  - Continuous tail mode: watches live logs in real-time
  - Summary mode: aggregates last N hours of blocks

Usage:
  python3 trade_block_monitor.py                     # Live monitor (tails logs)
  python3 trade_block_monitor.py --summary 24        # Summary of last 24h
  python3 trade_block_monitor.py --account ang       # Filter to one account
  python3 trade_block_monitor.py --decisions          # Analyze decision JSONL only
"""
import os, sys, json, time, re, glob, signal
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
from pathlib import Path

BASE_PATH = Path("/Users/niels/Documents/binance")
LOCAL_LOGS = Path("/Users/niels/logs")
SERVER_LOGS = Path("/home/niels/logs")
DECISIONS_DIR = BASE_PATH / "data" / "decisions"
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc"]
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + STOCK_ACCOUNTS
# Block patterns to search for in logs
BLOCK_PATTERNS = [
    (r"BLOCKED_HARD_AUGMENT_LOCK", "HARD_AUGMENT_LOCK"),
    (r"BLOCKED_EMERGENCY_BRAKE", "EMERGENCY_BRAKE"),
    (r"BLOCKED_QUARANTINED", "QUARANTINED"),
    (r"BLOCKED_BY_STRICT_NO_LOSS", "STRICT_NO_LOSS"),
    (r"BLOCKED_BY_HEDGE_MODE", "HEDGE_MODE"),
    (r"BLOCK_SKIPPED_LOCK_ACTIVE", "LOCK_ACTIVE"),
    (r"BLOCK_DEBOUNCE_ACTIVE", "DEBOUNCE"),
    (r"BLOCKED_AUGMENT_QUEUE_LOW_GAIN", "LOW_GAIN"),
    (r"SKIPPED_OPEN_BLOCKED", "OPEN_BLOCKED"),
    (r"SKIPPED_DUPLICATE", "DUPLICATE"),
    (r"SKIPPED_IN_LIMBO", "IN_LIMBO"),
    (r"SKIPPED_ACCOUNT_MISMATCH", "ACCT_MISMATCH"),
    (r"STALE_INDICATORS", "STALE_INDICATORS"),
    (r"BOYCOTT", "BOYCOTT"),
    (r"WAIT_BLOCK", "WAIT_BLOCK"),
    (r"HTF_STRICT", "HTF_STRICT"),
    (r"NOT_TRADEABLE", "NOT_TRADEABLE"),
    (r"BLOCKED_BLACKLIST", "BLACKLIST"),
    (r"BLOCKED_NO_GAIN", "NO_GAIN"),
    (r"BLOCKED_SHORT_UNAVAILABLE", "SHORT_UNAVAIL"),
    (r"BLOCKED_REDIS_LOCK", "REDIS_LOCK"),
    (r"BLOCKED_AUGMENT_COOLDOWN", "AUGMENT_COOLDOWN"),
    (r"BLOCKED_REDUCTION_COOLDOWN", "REDUCE_COOLDOWN"),
    (r"BLOCKED_REL_VOL", "LOW_VOLUME"),
]
shutdown_flag = False


def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def get_log_dir():
    """Return whichever log dir exists."""
    if LOCAL_LOGS.exists():
        return LOCAL_LOGS
    if SERVER_LOGS.exists():
        return SERVER_LOGS
    return LOCAL_LOGS


def parse_decisions(hours=24, account_filter=None):
    """Parse decision JSONL files for the given time window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stats = defaultdict(lambda: {"executions": 0, "by_action": Counter(), "symbols": Counter(), "reasons": Counter()})
    for acct in ALL_ACCOUNTS:
        if account_filter and acct != account_filter:
            continue
        # Find decision files for this account
        pattern = str(DECISIONS_DIR / f"decisions_{acct}_*.jsonl")
        files = sorted(glob.glob(pattern))
        for fpath in files[-3:]:  # last 3 days max
            try:
                with open(fpath) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        ts_str = rec.get("timestamp", "")
                        try:
                            ts = datetime.fromisoformat(ts_str)
                            if ts < cutoff:
                                continue
                        except Exception:
                            continue
                        action = rec.get("action", "UNKNOWN")
                        reason = rec.get("reason", "")
                        pk = rec.get("position_key", "")
                        symbol = pk.split(":")[-1].replace("_LONG", "").replace("_SHORT", "") if ":" in pk else pk
                        stats[acct]["executions"] += 1
                        stats[acct]["by_action"][action] += 1
                        stats[acct]["symbols"][symbol] += 1
                        # Classify reason
                        reason_cat = "OTHER"
                        for _, cat in BLOCK_PATTERNS:
                            if cat.lower() in reason.lower():
                                reason_cat = cat
                                break
                        if action in ("OPEN", "AUGMENT", "REENTRY"):
                            reason_cat = "ENTRY"
                        elif action in ("CLOSE", "REDUCE", "WEAK_REDUCE"):
                            reason_cat = "EXIT"
                        stats[acct]["reasons"][reason_cat] += 1
            except Exception:
                pass
    return stats


def parse_log_blocks(hours=24, account_filter=None):
    """Parse log files for BLOCKED/BOYCOTT/WAIT events."""
    log_dir = get_log_dir()
    cutoff = datetime.now() - timedelta(hours=hours)
    blocks = defaultdict(lambda: {"total": 0, "by_type": Counter(), "by_symbol": Counter(), "recent": []})
    log_patterns = []
    for acct in CRYPTO_ACCOUNTS:
        if account_filter and acct != account_filter:
            continue
        log_patterns.append((acct, str(log_dir / f"ez_manage_{acct}.log")))
        log_patterns.append((acct, str(log_dir / f"ez_positions_quick_general_{acct}.log")))
        log_patterns.append((acct, str(log_dir / f"ez_positions_quick_wait_{acct}.log")))
    for acct in STOCK_ACCOUNTS:
        if account_filter and acct != account_filter:
            continue
        log_patterns.append((acct, str(log_dir / f"tradier_manage_{acct}.log")))
    for acct, log_path in log_patterns:
        if not os.path.exists(log_path):
            continue
        try:
            # Read last 50K lines max (avoid reading huge logs fully)
            with open(log_path) as f:
                lines = f.readlines()[-50000:]
        except Exception:
            continue
        for line in lines:
            # Quick filter: only process lines with block keywords
            line_upper = line.upper()
            matched = False
            for pattern, category in BLOCK_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    # Extract timestamp
                    ts_match = re.match(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
                    if ts_match:
                        try:
                            ts = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                            if ts < cutoff:
                                continue
                        except Exception:
                            pass
                    # Extract symbol if present
                    sym_match = re.search(r'([A-Z0-9]{3,}USDT|[A-Z0-9]{3,}USDC)', line)
                    symbol = sym_match.group(0) if sym_match else "UNKNOWN"
                    blocks[acct]["total"] += 1
                    blocks[acct]["by_type"][category] += 1
                    blocks[acct]["by_symbol"][symbol] += 1
                    if len(blocks[acct]["recent"]) < 20:
                        blocks[acct]["recent"].append({"time": ts_match.group(1) if ts_match else "?", "type": category, "symbol": symbol, "line": line.strip()[:200]})
                    matched = True
                    break
    return blocks


def print_summary(hours=24, account_filter=None):
    """Print a comprehensive summary of trades and blocks."""
    print(f"\n{'='*120}")
    print(f"TRADE BLOCK MONITOR — Last {hours}h Summary")
    print(f"{'='*120}")
    # Decision analysis
    dec_stats = parse_decisions(hours, account_filter)
    print(f"\n{'─'*60}")
    print("EXECUTED DECISIONS (from JSONL)")
    print(f"{'─'*60}")
    for acct in ALL_ACCOUNTS:
        if account_filter and acct != account_filter:
            continue
        s = dec_stats.get(acct)
        if not s or s["executions"] == 0:
            continue
        print(f"\n  [{acct.upper()}] Total executions: {s['executions']}")
        print(f"    Actions: {dict(s['by_action'].most_common(10))}")
        print(f"    Top symbols: {dict(s['symbols'].most_common(5))}")
    # Block analysis
    block_stats = parse_log_blocks(hours, account_filter)
    print(f"\n{'─'*60}")
    print("BLOCKED/SKIPPED EVENTS (from logs)")
    print(f"{'─'*60}")
    grand_total = 0
    grand_by_type = Counter()
    for acct in ALL_ACCOUNTS:
        if account_filter and acct != account_filter:
            continue
        b = block_stats.get(acct)
        if not b or b["total"] == 0:
            continue
        grand_total += b["total"]
        grand_by_type.update(b["by_type"])
        print(f"\n  [{acct.upper()}] Total blocks: {b['total']}")
        print(f"    By type:")
        for btype, count in b["by_type"].most_common(15):
            pct = count / b["total"] * 100
            bar = "█" * int(pct / 3)
            print(f"      {btype:<25} {count:>6} ({pct:>5.1f}%) {bar}")
        if b["by_symbol"]:
            print(f"    Top blocked symbols: {dict(b['by_symbol'].most_common(5))}")
    # Grand summary
    if grand_total > 0:
        print(f"\n{'─'*60}")
        print(f"GRAND TOTAL BLOCKS: {grand_total}")
        print(f"{'─'*60}")
        for btype, count in grand_by_type.most_common(20):
            pct = count / grand_total * 100
            bar = "█" * int(pct / 2)
            print(f"  {btype:<25} {count:>6} ({pct:>5.1f}%) {bar}")
    # Execution efficiency
    print(f"\n{'─'*60}")
    print("EXECUTION EFFICIENCY")
    print(f"{'─'*60}")
    for acct in ALL_ACCOUNTS:
        if account_filter and acct != account_filter:
            continue
        executions = dec_stats.get(acct, {}).get("executions", 0)
        blocked = block_stats.get(acct, {}).get("total", 0)
        total = executions + blocked
        if total > 0:
            eff = executions / total * 100
            print(f"  [{acct.upper()}] Executed: {executions:>5} | Blocked: {blocked:>5} | Efficiency: {eff:.1f}%")
    print()


def tail_logs(account_filter=None):
    """Tail logs in real-time, filtering for block events."""
    log_dir = get_log_dir()
    print(f"\n{'='*80}")
    print(f"LIVE TRADE BLOCK MONITOR — tailing {log_dir}")
    print(f"{'='*80}")
    print("Watching for: BLOCKED, BOYCOTT, WAIT, SKIPPED, STALE events...")
    print("Press Ctrl+C to stop\n")
    # Track file positions
    files_to_watch = {}
    for acct in CRYPTO_ACCOUNTS:
        if account_filter and acct != account_filter:
            continue
        for pattern in [f"ez_manage_{acct}.log", f"ez_positions_quick_general_{acct}.log"]:
            fpath = str(log_dir / pattern)
            if os.path.exists(fpath):
                files_to_watch[fpath] = {"acct": acct, "pos": os.path.getsize(fpath)}
    for acct in STOCK_ACCOUNTS:
        if account_filter and acct != account_filter:
            continue
        fpath = str(log_dir / f"tradier_manage_{acct}.log")
        if os.path.exists(fpath):
            files_to_watch[fpath] = {"acct": acct, "pos": os.path.getsize(fpath)}
    print(f"Watching {len(files_to_watch)} log files\n")
    block_counts = Counter()
    while not shutdown_flag:
        for fpath, info in files_to_watch.items():
            try:
                size = os.path.getsize(fpath)
                if size <= info["pos"]:
                    if size < info["pos"]:
                        info["pos"] = 0  # file rotated
                    continue
                with open(fpath) as f:
                    f.seek(info["pos"])
                    new_lines = f.readlines()
                    info["pos"] = f.tell()
                for line in new_lines:
                    for pattern, category in BLOCK_PATTERNS:
                        if re.search(pattern, line, re.IGNORECASE):
                            sym_match = re.search(r'([A-Z0-9]{3,}USDT|[A-Z0-9]{3,}USDC)', line)
                            symbol = sym_match.group(0) if sym_match else "?"
                            block_counts[category] += 1
                            ts_match = re.match(r'(\d{2}:\d{2}:\d{2})', line) or re.match(r'\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2}:\d{2})', line)
                            ts = ts_match.group(1) if ts_match else "??:??:??"
                            acct = info["acct"].upper()
                            color = "\033[91m" if category in ("STRICT_NO_LOSS", "EMERGENCY_BRAKE", "STALE_INDICATORS") else "\033[93m"
                            reset = "\033[0m"
                            print(f"{color}[{ts}] [{acct}] {category:<25} {symbol:<15}{reset} | total: {block_counts[category]}")
                            break
                    # Also detect actual executions
                    if "EXECUTE_NOW_START" in line or "TRADE_EXECUTED" in line or "FILL_VERIFIED" in line:
                        sym_match = re.search(r'([A-Z0-9]{3,}USDT|[A-Z0-9]{3,}USDC)', line)
                        symbol = sym_match.group(0) if sym_match else "?"
                        print(f"\033[92m[{info['acct'].upper()}] EXECUTED {symbol}\033[0m")
            except Exception:
                pass
        time.sleep(0.5)
    # Print final stats
    print(f"\n{'='*60}")
    print("SESSION BLOCK SUMMARY")
    print(f"{'='*60}")
    for btype, count in block_counts.most_common(20):
        print(f"  {btype:<25} {count:>6}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Trade Block Monitor")
    parser.add_argument("--summary", type=int, nargs="?", const=24, help="Summary mode (hours, default 24)")
    parser.add_argument("--account", type=str, help="Filter to specific account")
    parser.add_argument("--decisions", action="store_true", help="Analyze decision JSONL only")
    args = parser.parse_args()
    if args.decisions:
        stats = parse_decisions(24, args.account)
        for acct, s in stats.items():
            if s["executions"] > 0:
                print(f"\n[{acct}] {s['executions']} decisions: {dict(s['by_action'])}")
    elif args.summary is not None:
        print_summary(args.summary, args.account)
    else:
        # Live tail mode
        print_summary(4, args.account)
        tail_logs(args.account)


if __name__ == "__main__":
    main()
