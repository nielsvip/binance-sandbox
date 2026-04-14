#!/opt/anaconda3/envs/binance_env/bin/python
"""Position Analyzer v2 — real decision-point counts from ez_manage, ez_positions_quick, tradier_manage."""
import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime


SERVER = "s1-int"
LOG_PATH = "/home/niels/logs"
BINANCE_PATH = "/home/niels/binance"
LOCAL_LOG_PATHS = ["/Users/niels/Documents/binance/logs", "/Users/niels/logs"]
LOCAL_BINANCE_PATH = "/Users/niels/Documents/binance"
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc"]
_source = "server"  # tracks whether we're reading server or local
_server_reachable = None  # None=unknown, True/False after first check

# ---------------------------------------------------------------------------
# Pattern registries — actual tags found in production logs
# ---------------------------------------------------------------------------
EZ_MANAGE_PATTERNS = {
    "entry": {
        "PULLBACK_REENTRY": r"\[PULLBACK_REENTRY\]",
        "STRONG_TREND_REENTRY": r"\[STRONG_TREND_REENTRY\]",
        "FAST_REENTRY": r"\[FAST_REENTRY\]",
        "REENTRY_ENFORCE": r"\[REENTRY_ENFORCE\]",
        "SIMPLE_REENTRY": r"\[SIMPLE_REENTRY\]",
        "LEADERBOARD_ENTRY": r"\[LEADERBOARD_ENTRY\]",
        "BREAKOUT_GUARD": r"\[BREAKOUT_GUARD\]",
    },
    "exit": {
        "MOMENTUM_TP": r"\[MOMENTUM_TP\]",
        "DC_BASIS_PROFIT_EXIT": r"\[DC_BASIS_PROFIT_EXIT\]",
        "IN_GAIN_TREND_EXIT": r"\[IN_GAIN_TREND_EXIT\]",
        "DC_BREACH_MONITOR": r"\[DC_BREACH_MONITOR\]",
        "STOP_FUNCTIONS_KILL": r"\[STOP_FUNCTIONS_KILL\]",
        "IMMEDIATE_WRONG_WAY": r"\[IMMEDIATE_WRONG_WAY\]",
        "TIER_TP": r"\[TIER_TP\]",
        "MARGIN_KILL": r"\[MARGIN_KILL\]",
    },
    "augment": {
        "FORCE_AUGMENT_DIRECT": r"\[FORCE_AUGMENT_DIRECT\]",
        "BIG_GAIN_ALERT": r"\[BIG_GAIN_ALERT\]",
        "DIRECT_HIGH_GAIN_HARD_GATE": r"\[DIRECT_HIGH_GAIN_HARD_GATE\]",
        "AUG_UNVERIFIED_BLOCKED": r"\[AUG_UNVERIFIED_BLOCKED\]",
        "MAKER_AUG_TIMEOUT": r"\[MAKER_AUG_TIMEOUT\]",
    },
    "execution": {
        "EXECUTE_NOW": r"\[EXECUTE_NOW\]",
        "EXECUTE_NOW_START": r"\[EXECUTE_NOW_START\]",
        "EXECUTE_UNVERIFIED": r"\[EXECUTE_UNVERIFIED\]",
        "REDUCE_UNVERIFIED": r"\[REDUCE_UNVERIFIED\]",
        "REDUCE_FAILED": r"\[REDUCE_FAILED\]",
        "WS_ORDER_CONFIRMED": r"\[WS_ORDER_CONFIRMED\]",
        "WS_COOLDOWN_SET": r"\[WS_COOLDOWN_SET\]",
        "MAKER_FALLBACK": r"\[MAKER_FALLBACK\]",
        "MAKER_BLOCK": r"\[MAKER_BLOCK\]",
    },
    "hedge": {
        "HEDGE_CLEANUP": r"\[HEDGE_CLEANUP\]",
        "HEDGE_CUT_ORIGINAL": r"\[HEDGE_CUT_ORIGINAL\]",
        "HEDGE_KILL": r"\[HEDGE_KILL\]",
    },
    "signals": {
        "SIGNAL_SUB": r"\[SIGNAL_SUB\]",
        "HANDLE_SIGNAL": r"\[HANDLE_SIGNAL\]",
        "WEBHOOK_CANCEL": r"\[WEBHOOK_CANCEL\]",
        "WEBHOOK_CANCEL_VERIFIED": r"\[WEBHOOK_CANCEL_VERIFIED\]",
    },
    "blocks": {
        "STRICT_NO_LOSS_BLOCK": r"\[STRICT_NO_LOSS_BLOCK\]",
        "EXECUTION_GUARD": r"\[EXECUTION_GUARD\]",
        "RATIO_GATE": r"\[RATIO_GATE\]",
        "RATIO_REBALANCE": r"\[RATIO_REBALANCE\]",
        "SUBSTITUTION": r"\[SUBSTITUTION\]",
        "NUKE": r"\[NUKE\]",
        "DEDUPE": r"\[DEDUPE\]",
    },
    "processing": {
        "VERBOSE_CALC": r"\[VERBOSE\]\[CALC\]",
        "SHARED_MEM": r"\[SHARED_MEM\]",
        "MARKET_INDEX_CORRECTION": r"\[MARKET_INDEX_CORRECTION\]",
        "MONITOR_REDUCTIONS": r"\[MONITOR_REDUCTIONS\]",
        "DIRECT_QUEUE": r"\[DIRECT_QUEUE\]",
        "DELAYED_CLEANUP": r"\[DELAYED_CLEANUP\]",
        "MARGIN_MANAGEMENT": r"\[MARGIN_MANAGEMENT\]",
    },
}

EZ_QUICK_PATTERNS = {
    "entry": {
        "ENTRY_STREAM": r"\[ENTRY_STREAM\]",
        "ENTRY_BULK": r"\[ENTRY_BULK\]",
        "ENTRY_CHECK_FAIL": r"\[ENTRY_CHECK_FAIL\]",
        "ENTRY_SKIP": r"\[ENTRY_SKIP\]",
        "SENTIMENT_FORCE": r"\[SENTIMENT_FORCE\]",
        "SENTIMENT_PYRAMID": r"\[SENTIMENT_PYRAMID\]",
        "SENTIMENT_RE_ENTRY": r"\[SENTIMENT_RE_ENTRY\]",
        "PULLBACK_AUGMENT_QUICK": r"\[PULLBACK_AUGMENT_QUICK\]",
        "SCALP_1M_BOYCOTT": r"\[SCALP_1M_BOYCOTT\]",
        "REBALANCE": r"\[REBALANCE\]",
        "REBALANCE_CRITICAL": r"\[REBALANCE_CRITICAL\]",
        "DIRECT_INJECT": r"\[DIRECT_INJECT\]",
    },
    "exit": {
        "EXIT_PRIORITY": r"\[EXIT_PRIORITY\]",
        "EXIT_MAINT": r"\[EXIT_MAINT\]",
        "STRONG_EXIT": r"\[STRONG_EXIT\]",
        "PROFIT_PROTECT": r"\[PROFIT_PROTECT\]",
        "BASIS_VIOLATION": r"\[BASIS_VIOLATION\]",
        "SENTIMENT_REDUCE": r"\[SENTIMENT_REDUCE\]",
    },
    "hedge": {
        "HEDGE_ATTEMPT": r"\[HEDGE_ATTEMPT\]",
        "HEDGE_SUCCESS": r"\[HEDGE_SUCCESS\]",
        "HEDGE_CRITICAL": r"\[HEDGE_CRITICAL\]",
        "HEDGE_KILL": r"\[HEDGE_KILL\]",
        "HEDGE_LIABILITY_KILL": r"\[HEDGE_LIABILITY_KILL\]",
        "HEDGE_WINNING_KILL_LOSER": r"\[HEDGE_WINNING_KILL_LOSER\]",
        "HEDGE_PROFIT_COORD": r"\[HEDGE_PROFIT_COORD\]",
        "HEDGE_RECOVERY_TRIM": r"\[HEDGE_RECOVERY_TRIM\]",
        "HEDGE_LOSING_KILL": r"\[HEDGE_LOSING_KILL\]",
        "HEDGE_NOCANDIDATE": r"\[HEDGE_NOCANDIDATE\]",
        "HEDGE_FALLBACK_REDUCE": r"\[HEDGE_FALLBACK_REDUCE\]",
        "HEDGE_FALLBACK_DYN": r"\[HEDGE_FALLBACK_DYN\]",
        "HEDGE_CUT_ORIGINAL": r"\[HEDGE_CUT_ORIGINAL\]",
        "HEDGE_PROMOTION": r"\[HEDGE_PROMOTION\]",
        "HEDGE_SCANNER": r"\[HEDGE_SCANNER\]",
    },
    "execution": {
        "EXECUTE": r"\[EXECUTE\]",
        "EXECUTE_BLOCKED": r"\[EXECUTE_BLOCKED\]",
        "EXECUTE_FAILED": r"\[EXECUTE_FAILED\]",
    },
    "scalp": {
        "SCALPING": r"\[SCALPING\]",
        "SCALP_MONITOR": r"\[SCALP_MONITOR\]",
        "SCALP_TRACK": r"\[SCALP_TRACK\]",
    },
    "blocks": {
        "BLOCK": r"\[BLOCK\]",
        "WAIT_BLOCK": r"\[WAIT_BLOCK\]",
        "BASIS_BOYCOTT": r"\[BASIS_BOYCOTT\]",
        "STOCH_BOYCOTT": r"\[STOCH_BOYCOTT\]",
        "SMA200_BOYCOTT": r"\[SMA200_BOYCOTT\]",
        "STRICT_NO_LOSS_BLOCK": r"\[STRICT_NO_LOSS_BLOCK\]",
        "HEDGE_MODE_BLOCK": r"\[HEDGE_MODE_BLOCK\]",
    },
    "monitoring": {
        "SENTIMENT_MGR": r"\[SENTIMENT_MGR\]",
        "SENTIMENT_ORPHAN_FIX": r"\[SENTIMENT_ORPHAN_FIX\]",
        "TRACKER": r"\[TRACKER\]",
        "WATCHDOG": r"\[WATCHDOG\]",
        "WATCHDOG_FETCH": r"\[WATCHDOG_FETCH\]",
        "DATA_FAILURE": r"\[DATA_FAILURE\]",
    },
}

# Tradier _wait_ logs use emoji-based candidate lines
TRADIER_PATTERNS = {
    "entry": {
        "TRADE_OPEN_SUBMITTED": r"\[TRADE\].*OPEN.*SUBMITTED",
        "TRADE_OPEN_COOLDOWN": r"\[TRADE\].*OPEN.*COOLDOWN",
        "TRADE_OPEN_LOCK": r"\[TRADE\].*OPEN.*LOCK",
    },
    "exit": {
        "TRADE_CLOSE_SUBMITTED": r"\[TRADE\].*CLOSE.*SUBMITTED",
        "TRADE_CLOSE_COOLDOWN": r"\[TRADE\].*CLOSE.*COOLDOWN",
        "TRADE_CLOSE_LOCK": r"\[TRADE\].*CLOSE.*LOCK",
    },
    "augment": {
        "TRADE_AUGMENT_SUBMITTED": r"\[TRADE\].*AUGMENT.*SUBMITTED",
        "TRADE_AUGMENT_COOLDOWN": r"\[TRADE\].*AUGMENT.*COOLDOWN",
        "TRADE_AUGMENT_LOCK": r"\[TRADE\].*AUGMENT.*LOCK",
    },
    "execution": {
        "ORDER_DEBUG": r"\[ORDER_DEBUG\]",
        "ORDER_RESULT": r"\[ORDER_RESULT\]",
        "TRADE_SENT": r"\[TRADE\] SENT",
        "SCALP": r"\[SCALP\]",
    },
}


def find_recent_logs(name_pattern, hours=24):
    """Find log files modified within the last N hours. Try server first, fall back to local."""
    global _source, _server_reachable
    if _server_reachable is not False:
        remote_cmd = f'find {LOG_PATH} -name "{name_pattern}" -type f -mmin -{hours * 60} ! -name "*watchdog*" 2>/dev/null | sort -r | head -10'
        try:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", SERVER, remote_cmd], capture_output=True, text=True, timeout=10)
            files = [f.strip() for f in r.stdout.strip().split("\n") if f.strip()]
            if files:
                _server_reachable = True
                return files
            if r.returncode == 0:
                _server_reachable = True  # reachable but no matching files
            else:
                _server_reachable = False
        except (subprocess.TimeoutExpired, Exception):
            _server_reachable = False
    # Fallback: search local logs (multiple directories)
    import glob as globmod
    import os
    cutoff = time.time() - hours * 3600
    local_files = []
    for ldir in LOCAL_LOG_PATHS:
        local_files.extend(globmod.glob(os.path.join(ldir, name_pattern)))
    local_files = [f for f in local_files if os.path.getmtime(f) > cutoff and "watchdog" not in f and "_cron" not in f and os.path.getsize(f) < 50 * 1024 * 1024]
    local_files = sorted(local_files, key=os.path.getmtime, reverse=True)[:10]
    if local_files:
        _source = "local"
    return local_files


def count_patterns(pattern_dict, log_files):
    """Count all patterns via grep. Uses SSH for server files, local shell for local files."""
    if not log_files:
        return {cat: {tag: 0 for tag in tags} for cat, tags in pattern_dict.items()}
    is_local = log_files[0].startswith("/Users/")
    flat = []
    idx_map = {}
    idx = 0
    for cat, tags in pattern_dict.items():
        for tag, regex in tags.items():
            flat.append((cat, tag, regex))
            idx_map[idx] = (cat, tag)
            idx += 1
    log_glob = " ".join(f'"{f}"' for f in log_files)
    grep_lines = []
    for i, (_, tag, regex) in enumerate(flat):
        grep_lines.append(f'echo "PAT{i}:$(grep -cE \'{regex}\' /tmp/_pa_combined.log 2>/dev/null || echo 0)"')
    script = f"cat {log_glob} > /tmp/_pa_combined.log 2>/dev/null\n" + "\n".join(grep_lines) + "\nrm -f /tmp/_pa_combined.log"
    results = {cat: {tag: 0 for tag in tags} for cat, tags in pattern_dict.items()}
    try:
        if is_local:
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120)
        else:
            r = subprocess.run(["ssh", SERVER, "bash -s"], input=script, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if line.startswith("PAT") and ":" in line:
                    prefix, val = line.split(":", 1)
                    i = int(prefix[3:])
                    if i in idx_map:
                        cat, tag = idx_map[i]
                        try:
                            results[cat][tag] = int(val.strip())
                        except ValueError:
                            pass
    except Exception as e:
        print(f"  ERROR counting patterns: {e}")
    return results


def _read_json_file(path, is_local=False):
    """Read a JSON file either locally or via SSH."""
    try:
        if is_local:
            import os
            if not os.path.exists(path):
                return None
            with open(path) as f:
                return json.load(f)
        else:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", SERVER, f"cat {path}"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return json.loads(r.stdout)
    except Exception:
        pass
    return None


def load_positions():
    """Load open crypto positions. Try server first, fall back to local."""
    positions = {}
    use_local = _source == "local"
    base = LOCAL_BINANCE_PATH if use_local else BINANCE_PATH
    for account in CRYPTO_ACCOUNTS:
        for side_file in ["long_positions.json", "short_positions.json"]:
            side_default = "LONG" if "long" in side_file else "SHORT"
            path = f"{base}/{account}/{side_file}"
            data = _read_json_file(path, is_local=use_local)
            if not data:
                continue
            for raw_key, pos in data.items():
                if not isinstance(pos, dict):
                    continue
                amt = float(pos.get("positionAmt", 0) or 0)
                mark = float(pos.get("mark_price", 0) or 0)
                entry = float(pos.get("entry_price", 0) or 0)
                if abs(amt) < 1e-9:
                    continue
                raw_sym = raw_key.split(":", 1)[-1] if ":" in raw_key else raw_key
                clean = raw_sym.replace("_LONG", "").replace("_SHORT", "")
                side = pos.get("position_side", side_default)
                pk = f"{account}:{clean}_{side}"
                usd = abs(amt) * mark
                pnl_pct = 0.0
                if entry > 0:
                    if side == "LONG":
                        pnl_pct = ((mark - entry) / entry) * 100
                    else:
                        pnl_pct = ((entry - mark) / entry) * 100
                positions[pk] = {"symbol": clean, "account": account, "side": side, "qty": amt, "mark": mark, "entry": entry, "usd": usd, "pnl_pct": pnl_pct}
    return positions


def load_stock_positions():
    """Load open stock positions. Try server first, fall back to local."""
    positions = {}
    use_local = _source == "local"
    base = LOCAL_BINANCE_PATH if use_local else BINANCE_PATH
    for account in STOCK_ACCOUNTS:
        for side_file in ["long_positions.json", "short_positions.json"]:
            side_default = "LONG" if "long" in side_file else "SHORT"
            path = f"{base}/{account}/{side_file}"
            data = _read_json_file(path, is_local=use_local)
            if not data:
                continue
            for raw_key, pos in data.items():
                if not isinstance(pos, dict):
                    continue
                qty = abs(float(pos.get("positionAmt", 0) or pos.get("quantity", 0) or 0))
                mark = float(pos.get("mark_price", 0) or pos.get("last_price", 0) or 0)
                entry = float(pos.get("entry_price", 0) or 0)
                if qty < 0.01:
                    continue
                raw_sym = raw_key.split(":", 1)[-1] if ":" in raw_key else raw_key
                clean = raw_sym.replace("_LONG", "").replace("_SHORT", "")
                side = pos.get("position_side", side_default)
                pk = f"{account}:{clean}_{side}"
                usd = qty * mark
                pnl_pct = 0.0
                if entry > 0:
                    if side == "LONG":
                        pnl_pct = ((mark - entry) / entry) * 100
                    else:
                        pnl_pct = ((entry - mark) / entry) * 100
                positions[pk] = {"symbol": clean, "account": account, "side": side, "qty": qty, "mark": mark, "entry": entry, "usd": usd, "pnl_pct": pnl_pct}
    return positions


def print_category(title, data, show_zero=False):
    """Print a category table. Returns total."""
    total = sum(data.values())
    if total == 0 and not show_zero:
        return 0
    print(f"\n  {title} (total: {total:,})")
    print(f"    {'Tag':<35} {'Count':>10}")
    print(f"    {'-'*35} {'-'*10}")
    for tag, count in sorted(data.items(), key=lambda x: -x[1]):
        if count == 0 and not show_zero:
            continue
        print(f"    {tag:<35} {count:>10,}")
    return total


def print_script_section(script_name, results, hours):
    """Print full section for one script."""
    print(f"\n{'='*70}")
    print(f"  {script_name}")
    print(f"{'='*70}")
    grand_total = 0
    for category, data in results.items():
        grand_total += print_category(category.upper(), data)
    calls_min = grand_total / (hours * 60) if hours > 0 else 0
    print(f"\n  GRAND TOTAL: {grand_total:,}  ({calls_min:.1f}/min over {hours}h)")
    return grand_total


def print_positions(title, positions):
    """Print position summary table with PnL."""
    if not positions:
        print(f"\n  {title}: no open positions")
        return
    by_account = defaultdict(list)
    for pk, p in positions.items():
        by_account[p["account"]].append((pk, p))
    total_usd = sum(p["usd"] for p in positions.values())
    n_long = sum(1 for p in positions.values() if p["side"] == "LONG")
    n_short = sum(1 for p in positions.values() if p["side"] == "SHORT")
    print(f"\n  {title}  ({len(positions)} pos: {n_long}L/{n_short}S, ${total_usd:,.0f} total)")
    print(f"    {'KEY':<35} {'SIDE':<6} {'USD':>10} {'PnL%':>8}")
    print(f"    {'-'*35} {'-'*6} {'-'*10} {'-'*8}")
    for account in sorted(by_account):
        acct_pos = sorted(by_account[account], key=lambda x: -x[1]["usd"])
        acct_usd = sum(p["usd"] for _, p in acct_pos)
        acct_longs = sum(1 for _, p in acct_pos if p["side"] == "LONG")
        acct_shorts = sum(1 for _, p in acct_pos if p["side"] == "SHORT")
        print(f"    --- {account.upper()} ({acct_longs}L/{acct_shorts}S ${acct_usd:,.0f}) ---")
        for pk, p in acct_pos[:20]:
            display = pk.split(":", 1)[1] if ":" in pk else pk
            pnl_str = f"{p['pnl_pct']:+.1f}%"
            pnl_color = ""
            print(f"    {display:<35} {p['side']:<6} ${p['usd']:>9,.0f} {pnl_str:>8}")
        if len(acct_pos) > 20:
            print(f"    ... +{len(acct_pos)-20} more")


def main():
    parser = argparse.ArgumentParser(description="Position Analyzer v2 -- real decision-point counts")
    parser.add_argument("--hours", type=int, default=1, help="Hours of logs to analyze (default: 1)")
    parser.add_argument("--no-positions", action="store_true", help="Skip position loading")
    parser.add_argument("--no-stocks", action="store_true", help="Skip tradier stock analysis")
    parser.add_argument("--zeros", action="store_true", help="Show tags with zero count")
    parser.add_argument("--continuous", action="store_true", help="Run continuously on interval")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between runs in continuous mode (default: 300)")
    args = parser.parse_args()
    hours = args.hours
    global _source, _server_reachable
    _source = "server"  # reset each run
    _server_reachable = None
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'#'*70}")
    print(f"  POSITION ANALYZER v2 -- {ts}")
    print(f"  Window: last {hours}h")
    print(f"{'#'*70}")
    t0 = time.time()
    manage_logs = find_recent_logs("ez_manage*.log", hours)
    quick_logs = find_recent_logs("ez_positions_quick*.log", hours)
    tradier_logs = find_recent_logs("tradier_manage*.log", hours) if not args.no_stocks else []
    elapsed_find = time.time() - t0
    src_label = "LOCAL (MacBook)" if _source == "local" else "SERVER (157.180.125.52)"
    print(f"\n  Source: {src_label}")
    print(f"  Log files found in {elapsed_find:.1f}s:")
    for lbl, logs in [("ez_manage", manage_logs), ("ez_quick", quick_logs), ("tradier", tradier_logs)]:
        if logs or lbl != "tradier" or not args.no_stocks:
            print(f"    {lbl + ':':<20} {len(logs)} files")
            for f in logs:
                print(f"      {f.split('/')[-1]}")
    t1 = time.time()
    manage_results = count_patterns(EZ_MANAGE_PATTERNS, manage_logs)
    quick_results = count_patterns(EZ_QUICK_PATTERNS, quick_logs)
    tradier_results = count_patterns(TRADIER_PATTERNS, tradier_logs) if not args.no_stocks else {}
    elapsed_count = time.time() - t1
    print(f"  Pattern counting done in {elapsed_count:.1f}s")
    totals = {}
    totals["ez_manage"] = print_script_section("ez_manage.py (crypto orchestrator)", manage_results, hours)
    totals["ez_quick"] = print_script_section("ez_positions_quick.py (scalp/hedge engine)", quick_results, hours)
    if tradier_results:
        totals["tradier"] = print_script_section("tradier_manage.py (stock orchestrator)", tradier_results, hours)
    # --- Cross-script summary ---
    print(f"\n{'='*70}")
    print(f"  CROSS-SCRIPT SUMMARY ({hours}h)")
    print(f"{'='*70}")
    def cat_total(results, cat):
        return sum(results.get(cat, {}).values())
    m_entry = cat_total(manage_results, "entry")
    m_exit = cat_total(manage_results, "exit")
    m_aug = cat_total(manage_results, "augment")
    m_exec = cat_total(manage_results, "execution")
    m_hedge = cat_total(manage_results, "hedge")
    m_sig = cat_total(manage_results, "signals")
    m_blk = cat_total(manage_results, "blocks")
    q_entry = cat_total(quick_results, "entry")
    q_exit = cat_total(quick_results, "exit")
    q_hedge = cat_total(quick_results, "hedge")
    q_exec = cat_total(quick_results, "execution")
    q_scalp = cat_total(quick_results, "scalp")
    q_blk = cat_total(quick_results, "blocks")
    t_entry = cat_total(tradier_results, "entry") if tradier_results else 0
    t_exit = cat_total(tradier_results, "exit") if tradier_results else 0
    t_aug = cat_total(tradier_results, "augment") if tradier_results else 0
    t_exec = cat_total(tradier_results, "execution") if tradier_results else 0
    print(f"\n    {'Category':<25} {'ez_manage':>12} {'ez_quick':>12} {'tradier':>12} {'TOTAL':>12}")
    print(f"    {'-'*25} {'-'*12} {'-'*12} {'-'*12} {'-'*12}")
    rows = [
        ("Entry candidates", m_entry, q_entry, t_entry),
        ("Exit candidates", m_exit, q_exit, t_exit),
        ("Augment evals", m_aug, 0, t_aug),
        ("Hedge operations", m_hedge, q_hedge, 0),
        ("Scalp operations", 0, q_scalp, 0),
        ("Trade executions", m_exec, q_exec, t_exec),
        ("Signals/webhooks", m_sig, 0, 0),
        ("Blocks/rejections", m_blk, q_blk, 0),
    ]
    for label, m, q, t in rows:
        total = m + q + t
        if total > 0:
            print(f"    {label:<25} {m:>12,} {q:>12,} {t:>12,} {total:>12,}")
    all_total = sum(totals.values())
    print(f"\n    ALL DECISIONS: {all_total:,}  ({all_total/(hours*60):.1f}/min)")
    if not args.no_positions:
        crypto_pos = load_positions()
        print_positions("CRYPTO POSITIONS", crypto_pos)
        if not args.no_stocks:
            stock_pos = load_stock_positions()
            print_positions("STOCK POSITIONS (Tradier)", stock_pos)
    elapsed_total = time.time() - t0
    print(f"\n  Done in {elapsed_total:.1f}s")
    print()


if __name__ == "__main__":
    while True:
        main()
        if not sys.argv or "--continuous" not in sys.argv:
            break
        interval = 300
        for i, a in enumerate(sys.argv):
            if a == "--interval" and i + 1 < len(sys.argv):
                interval = int(sys.argv[i + 1])
        print(f"  Next refresh in {interval}s... (Ctrl+C to stop)")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n  Stopped.")
            break
