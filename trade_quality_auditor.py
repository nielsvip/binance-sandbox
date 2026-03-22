#!/opt/anaconda3/envs/binance_env/bin/python
"""Trade Quality Auditor — Daily breakdown comparing RECOMMENDED vs ACTUAL trades.
Uses actual trade history from data/history/ (crypto) and data/tradier/history/ (stocks).
Cross-references against BACKTEST_CHANGES_100.xlsx rules.
Explains trades that did NOT happen (blocked by what reason in what function).

Usage:
  python trade_quality_auditor.py                  # Today's audit
  python trade_quality_auditor.py --date 20260318  # Specific date
  python trade_quality_auditor.py --daemon          # Run daily at 1pm ET
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import openpyxl
except ImportError:
    openpyxl = None
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("trade_auditor")
logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
fh = RotatingFileHandler(str(logs_dir / "trade_quality_auditor.log"), maxBytes=50 * 1024 * 1024, backupCount=3)
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(fh)

BASE_PATH = Path(__file__).parent
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc"]
DECISIONS_DIR = BASE_PATH / "data" / "decisions"
CRYPTO_HISTORY_DIR = BASE_PATH / "data" / "history"
TRADIER_HISTORY_DIR = BASE_PATH / "data" / "tradier" / "history"
BACKTEST_FILE = BASE_PATH / "BACKTEST_CHANGES_100.xlsx"
REPORT_DIR = BASE_PATH / "audit_reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def sf(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def load_backtest_rules():
    rules = []
    if not openpyxl or not BACKTEST_FILE.exists():
        return rules
    try:
        wb = openpyxl.load_workbook(str(BACKTEST_FILE), read_only=True)
        ws = wb[wb.sheetnames[0]]
        headers = None
        for row in ws.iter_rows(values_only=True):
            if headers is None:
                headers = [str(c).strip() if c else "" for c in row]
                continue
            rule = dict(zip(headers, [c for c in row]))
            if rule.get("Change_ID"):
                rules.append(rule)
        logger.info(f"Loaded {len(rules)} backtest rules")
    except Exception as e:
        logger.error(f"Failed to load backtest rules: {e}")
    return rules


# ═══════════════════════════════════════════════════════════════════════════════
# ACTUAL TRADES — from JSONL history files (the ground truth)
# ═══════════════════════════════════════════════════════════════════════════════

def load_actual_trades(history_dir, accounts, date_str, system_name):
    """Load actual executed trades from history JSONL files for a given date."""
    target_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    trades = []
    for acct in accounts:
        acct_dir = history_dir / acct
        if not acct_dir.exists():
            continue
        for fpath in acct_dir.glob("*.jsonl"):
            pk_base = fpath.stem
            try:
                with open(fpath) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = str(d.get("ts", ""))
                        if target_date not in ts:
                            continue
                        d["_account"] = acct
                        d["_system"] = system_name
                        d["_position_key"] = f"{acct}:{pk_base}"
                        d["_symbol"] = pk_base.rsplit("_", 1)[0] if "_" in pk_base else pk_base
                        d["_side"] = "LONG" if pk_base.endswith("_LONG") else "SHORT" if pk_base.endswith("_SHORT") else "?"
                        trades.append(d)
            except Exception as e:
                logger.error(f"Error reading {fpath}: {e}")
    trades.sort(key=lambda x: str(x.get("ts", "")))
    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# RECOMMENDED TRADES — from decisions JSONL (what the system wanted to do)
# ═══════════════════════════════════════════════════════════════════════════════

def load_decisions(date_str, accounts):
    decisions = []
    for acct in accounts:
        fpath = DECISIONS_DIR / f"decisions_{acct}_{date_str}.jsonl"
        if not fpath.exists():
            continue
        try:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        d["_account"] = acct
                        decisions.append(d)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Error reading {fpath}: {e}")
    return decisions


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCKED TRADES — from log files (what was recommended but stopped)
# ═══════════════════════════════════════════════════════════════════════════════

BLOCK_PATTERNS = [
    (r"\[STRICT_NO_LOSS_BLOCK\]\[(\w+)\]\s+(\S+):\s+Blocking\s+(\w+)\s+\(([^)]+)\)", "STRICT_NO_LOSS_BLOCK", "ez_positions_quick.py:process_position"),
    (r"\[LS_RATIO_HARD_BLOCK\]\s+(\S+):\s+Blocking\s+(\w+)\s+open\.\s+Ratio.*?(\d+\.\d+)", "LS_RATIO_HARD_BLOCK", "ez_manage.py:execute_trade_action"),
    (r"\[EMERGENCY_BRAKE\]\s+(\w+):\s+(\d+)\s+EXECUTED trades", "EMERGENCY_BRAKE", "ez_positions_quick.py:check_entry_candidates"),
    (r"\[TREND_ENTRY_BLOCK\]\s+(\S+)\s+(\w+)\s+blocked:\s+htf_score=(-?\d+)", "TREND_ENTRY_BLOCK", "ez_positions_quick.py:worker"),
    (r"\[SUBSTITUTION_NO_LOSS_BLOCK\]\s+(\S+)", "SUBSTITUTION_NO_LOSS_BLOCK", "ez_manage.py:execute_trade_action"),
    (r"\[RATIO_GATE\]\s+(\S+):\s+Blocking\s+(\w+)", "RATIO_GATE", "ez_manage.py:execute_trade_action"),
    (r"UNCALCULATED_STOCH", "UNCALCULATED_STOCH", "ez_positions_quick.py:process_position"),
    (r"STALE_INDICATORS", "STALE_INDICATORS", "ez_positions_quick.py:process_position"),
    (r"\[REDUCE_COOLDOWN\]\s+(\S+)", "REDUCE_COOLDOWN", "ez_positions_quick.py:process_position"),
    (r"DC15M_FORCE_EXIT_BLOCKED", "DC15M_FORCE_EXIT_BLOCKED", "ez_manage.py"),
    (r"EMERGENCY_DEEP_LOSS_BLOCKED_NO_LOSS", "EMERGENCY_DEEP_LOSS_BLOCKED", "ez_positions_quick.py:process_position"),
    (r"EMERGENCY_DC1H_BLOCKED_NO_LOSS", "EMERGENCY_DC1H_BLOCKED", "ez_positions_quick.py:process_position"),
    (r"Outside Market", "OUTSIDE_MARKET_HOURS", "tradier_manage.py:is_regular_trading_hours"),
    (r"Stale \(Age:(\d+)s\)", "STALE_INDICATORS_TRADIER", "tradier_manage.py:is_data_fresh"),
    (r"SwingBudget.*?(\d+)\+(\d+)>(\d+)", "BUDGET_EXCEEDED", "tradier_manage.py:process_position"),
]


def load_blocked_trades(date_str, accounts):
    """Scan log files for blocked/rejected trade attempts."""
    target_date_prefix = f"18 "  # day of month for log matching
    blocks = defaultdict(lambda: {"count": 0, "reasons": defaultdict(int), "function": "", "examples": []})
    for acct in accounts:
        log_path = logs_dir / f"ez_manage_{acct}_app.log"
        if not log_path.exists():
            continue
        try:
            with open(log_path) as f:
                for line in f:
                    for pattern, block_name, function in BLOCK_PATTERNS:
                        if block_name in line or re.search(pattern, line):
                            blocks[block_name]["count"] += 1
                            blocks[block_name]["function"] = function
                            blocks[block_name]["reasons"][acct] += 1
                            if len(blocks[block_name]["examples"]) < 3:
                                blocks[block_name]["examples"].append(line.strip()[:120])
                            break
        except Exception:
            continue
    # Tradier logs
    for log_name in ["tradier_manage_wait_trb.log", "tradier_manage_wait_trc.log"]:
        log_path = logs_dir / log_name
        if not log_path.exists():
            continue
        try:
            with open(log_path) as f:
                for line in f:
                    for pattern, block_name, function in BLOCK_PATTERNS:
                        if block_name in ("OUTSIDE_MARKET_HOURS", "STALE_INDICATORS_TRADIER", "BUDGET_EXCEEDED") and re.search(pattern, line):
                            acct_key = "trb" if "trb" in log_name else "trc"
                            blocks[block_name]["count"] += 1
                            blocks[block_name]["function"] = function
                            blocks[block_name]["reasons"][acct_key] += 1
                            break
        except Exception:
            continue
    return dict(blocks)


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST RULE EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_trade_against_rules(trade, rules):
    violations = []
    snap = trade.get("indicators", {}) or trade.get("snapshot", {}) or {}
    pk = str(trade.get("_position_key", ""))
    is_long = "_LONG" in pk
    is_short = "_SHORT" in pk
    trade_type = str(trade.get("type", trade.get("action", "")))
    is_entry = trade_type in ("OPEN", "AUGMENT", "REENTRY")
    for rule in rules:
        param = str(rule.get("Parameter", ""))
        new_val = rule.get("New_Value", "")
        cid = str(rule.get("Change_ID", ""))
        if param == "ENTRY_VOL_MIN_RATIO" and is_entry:
            vol = sf(snap.get("relative_volume_1h", snap.get("rel_vol")))
            thr = sf(new_val, 1.3)
            if 0 < vol < thr:
                violations.append(f"{cid}: VOL={vol:.2f} < {thr} → would block entry (low volume)")
        elif param == "SHORT_RSI_MIN_1H" and is_short and is_entry:
            rsi = sf(snap.get("rsi_1h"))
            thr = sf(new_val, 40)
            if 0 < rsi < thr:
                violations.append(f"{cid}: SHORT RSI_1H={rsi:.0f} < {thr} → would block (shorting oversold)")
        elif param == "LONG_STOCH_CHASE_BLOCK" and is_long and is_entry:
            k1h = sf(snap.get("stoch_k_1h", snap.get("k_1h")))
            if k1h > 70:
                violations.append(f"{cid}: LONG K_1H={k1h:.0f} > 70 → would block (chasing overbought)")
        elif param == "ENTRY_ATR_PCT_MIN" and is_entry:
            atr = sf(snap.get("atr_pct_1h"))
            thr = sf(new_val, 1.5)
            if 0 < atr < thr:
                violations.append(f"{cid}: ATR%={atr:.2f} < {thr} → would block (low volatility)")
    return violations


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def _load_previous_report(date_str):
    """Load the previous audit report for comparison."""
    report_dir = BASE_PATH / "audit_reports"
    all_reports = sorted(report_dir.glob("audit_*.txt"), reverse=True)
    current_name = f"audit_{date_str}.txt"
    for rp in all_reports:
        if rp.name != current_name:
            try:
                return rp.name, rp.read_text()[:4000]
            except Exception:
                continue
    return None, None


def generate_report(date_str, crypto_actual, tradier_actual, decisions, blocked, rules):
    r = []
    r.append(f"{'=' * 90}")
    r.append(f"  TRADE QUALITY AUDIT — {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}")
    r.append(f"{'=' * 90}")
    r.append("")
    # ─── SECTION 0: CONCLUSIONS (from previous report comparison) ───
    prev_name, prev_text = _load_previous_report(date_str)
    changelog_path = BASE_PATH / "audit_reports" / "OPTIMIZER_CHANGELOG.md"
    r.append("## 0. CONCLUSIONS & CHANGE EFFECTS")
    r.append("")
    if prev_text:
        r.append(f"  Comparing to previous report: {prev_name}")
        prev_total = 0
        curr_total = len(crypto_actual)
        m = re.search(r"TOTAL:\s+(\d+)\s+actual trades", prev_text)
        if m:
            prev_total = int(m.group(1))
        delta = curr_total - prev_total
        r.append(f"  Previous: {prev_total} actual trades | Current: {curr_total} actual trades | Delta: {delta:+d}")
        prev_blocks = 0
        m2 = re.search(r"Total blocks:\s+(\d+)", prev_text)
        if m2:
            prev_blocks = int(m2.group(1))
        curr_blocks = sum(d["count"] for d in blocked.values()) if blocked else 0
        r.append(f"  Previous blocks: {prev_blocks} | Current blocks: {curr_blocks} | Delta: {curr_blocks - prev_blocks:+d}")
    else:
        r.append("  No previous report found for comparison.")
    if changelog_path.exists():
        try:
            cl = changelog_path.read_text()
            recent_entries = cl.strip().split("---")[-3:]
            r.append("")
            r.append("  Recent optimizer changes:")
            for entry in recent_entries:
                lines = [l.strip() for l in entry.strip().split("\n") if l.strip() and not l.startswith("##")]
                for line in lines[:3]:
                    r.append(f"    {line}")
        except Exception:
            pass
    r.append("")
    # ─── SECTION 1: ACTUAL EXECUTED TRADES (CRYPTO) ───
    r.append("## 1. ACTUAL CRYPTO TRADES (from data/history/)")
    r.append("")
    crypto_pnl_by_acct = defaultdict(lambda: {"trades": 0, "value": 0.0, "opens": 0, "closes": 0, "augments": 0, "reduces": 0})
    crypto_by_reason = defaultdict(lambda: {"count": 0, "value": 0.0})
    crypto_by_symbol = defaultdict(lambda: {"count": 0, "value": 0.0})
    for t in crypto_actual:
        acct = t["_account"]
        ttype = str(t.get("type", "")).upper()
        value = sf(t.get("value", 0))
        reason = str(t.get("reason", ""))[:50]
        symbol = t["_symbol"]
        side = t["_side"]
        crypto_pnl_by_acct[acct]["trades"] += 1
        if ttype == "OPEN":
            crypto_pnl_by_acct[acct]["opens"] += 1
        elif ttype == "CLOSE":
            crypto_pnl_by_acct[acct]["closes"] += 1
        elif ttype == "AUGMENT":
            crypto_pnl_by_acct[acct]["augments"] += 1
        elif ttype == "REDUCE":
            crypto_pnl_by_acct[acct]["reduces"] += 1
            crypto_pnl_by_acct[acct]["value"] -= value
            crypto_by_symbol[f"{symbol}_{side}"]["value"] -= value
        else:
            crypto_pnl_by_acct[acct]["value"] -= value
        crypto_by_reason[reason]["count"] += 1
        crypto_by_reason[reason]["value"] += value
        crypto_by_symbol[f"{symbol}_{side}"]["count"] += 1
    for acct in CRYPTO_ACCOUNTS:
        s = crypto_pnl_by_acct.get(acct)
        if not s or s["trades"] == 0:
            continue
        r.append(f"  {acct.upper():>4}: {s['trades']:>4} trades | {s['opens']} opens | {s['augments']} augs | {s['reduces']} reds | {s['closes']} closes")
    r.append(f"  TOTAL: {len(crypto_actual)} actual trades across {len(crypto_pnl_by_acct)} accounts")
    r.append("")
    r.append("  Top exit reasons (by count):")
    for reason, data in sorted(crypto_by_reason.items(), key=lambda x: -x[1]["count"])[:15]:
        r.append(f"    {data['count']:>4}x  ${data['value']:>8.1f}  {reason}")
    r.append("")
    r.append("  Top symbols (by trade count):")
    for sym, data in sorted(crypto_by_symbol.items(), key=lambda x: -x[1]["count"])[:15]:
        r.append(f"    {data['count']:>4}x  {sym}")
    r.append("")
    # ─── SECTION 2: ACTUAL EXECUTED TRADES (TRADIER) ───
    r.append("## 2. ACTUAL TRADIER TRADES (from data/tradier/history/)")
    r.append("")
    tradier_by_acct = defaultdict(lambda: {"trades": 0, "opens": 0, "closes": 0, "augments": 0, "reduces": 0})
    tradier_by_symbol = defaultdict(lambda: {"count": 0, "buy_value": 0.0, "sell_value": 0.0})
    for t in tradier_actual:
        acct = t["_account"]
        ttype = str(t.get("type", "")).upper()
        value = sf(t.get("value", 0))
        symbol = t["_symbol"]
        side = t["_side"]
        reason = str(t.get("reason", ""))[:60]
        tradier_by_acct[acct]["trades"] += 1
        if ttype == "OPEN":
            tradier_by_acct[acct]["opens"] += 1
        elif ttype == "CLOSE":
            tradier_by_acct[acct]["closes"] += 1
        elif ttype == "AUGMENT":
            tradier_by_acct[acct]["augments"] += 1
        elif ttype == "REDUCE":
            tradier_by_acct[acct]["reduces"] += 1
        tradier_by_symbol[f"{symbol}_{side}"]["count"] += 1
        if ttype in ("OPEN", "AUGMENT"):
            tradier_by_symbol[f"{symbol}_{side}"]["buy_value"] += value
        else:
            tradier_by_symbol[f"{symbol}_{side}"]["sell_value"] += value
        r.append(f"  {t.get('ts','')[:19]}  {acct}  {ttype:<8} {symbol:<6} {side:<6} qty={sf(t.get('qty')):>6.0f} @${sf(t.get('price')):>9.2f} ${value:>8.1f} | {reason}")
    r.append("")
    for acct in STOCK_ACCOUNTS:
        s = tradier_by_acct.get(acct)
        if not s:
            continue
        r.append(f"  {acct.upper()}: {s['trades']} trades | {s['opens']} opens | {s['augments']} augs | {s['reduces']} reds | {s['closes']} closes")
    r.append(f"  TOTAL: {len(tradier_actual)} actual tradier trades")
    r.append("")
    # ─── SECTION 3: RECOMMENDED vs ACTUAL ───
    r.append("## 3. RECOMMENDED vs ACTUAL (decisions that did NOT execute)")
    r.append("")
    decision_actions = defaultdict(int)
    for d in decisions:
        action = str(d.get("action", ""))
        decision_actions[action] += 1
    actual_actions = defaultdict(int)
    for t in crypto_actual:
        actual_actions[str(t.get("type", "")).upper()] += 1
    r.append(f"  Decisions (recommended): {len(decisions)}")
    r.append(f"  Actual trades (executed): {len(crypto_actual)}")
    r.append(f"  Execution rate: {len(crypto_actual)/max(len(decisions),1)*100:.1f}%")
    r.append(f"  Recommended breakdown: {dict(sorted(decision_actions.items(), key=lambda x: -x[1]))}")
    r.append(f"  Executed breakdown:    {dict(sorted(actual_actions.items(), key=lambda x: -x[1]))}")
    r.append("")
    # ─── SECTION 4: BLOCKED TRADES ANALYSIS ───
    r.append("## 4. BLOCKED TRADES — Why trades did NOT happen")
    r.append("")
    if not blocked:
        r.append("  No blocked trade data found in logs")
    else:
        for block_name, data in sorted(blocked.items(), key=lambda x: -x[1]["count"]):
            pct = data["count"] / max(len(decisions), 1) * 100
            acct_breakdown = ", ".join(f"{a}:{c}" for a, c in sorted(data["reasons"].items(), key=lambda x: -x[1]))
            r.append(f"  {block_name}: {data['count']} blocks ({pct:.1f}% of decisions)")
            r.append(f"    Function: {data['function']}")
            r.append(f"    Accounts: {acct_breakdown}")
            if data["examples"]:
                r.append(f"    Example: {data['examples'][0]}")
            r.append("")
    # ─── SECTION 5: BACKTEST RULE VIOLATIONS ───
    r.append("## 5. BACKTEST RULE VIOLATIONS (trades that SHOULD have been blocked)")
    r.append("")
    if not rules:
        r.append("  No backtest rules loaded (BACKTEST_CHANGES_100.xlsx not found or openpyxl missing)")
    else:
        violation_count = 0
        violation_by_rule = defaultdict(int)
        for t in crypto_actual:
            viols = evaluate_trade_against_rules(t, rules)
            if viols:
                violation_count += 1
                for v in viols:
                    cid = v.split(":")[0]
                    violation_by_rule[cid] += 1
        r.append(f"  {violation_count} trades violated backtest rules ({violation_count/max(len(crypto_actual),1)*100:.1f}%)")
        for cid, count in sorted(violation_by_rule.items(), key=lambda x: -x[1]):
            matching_rule = next((ru for ru in rules if str(ru.get("Change_ID", "")) == cid), {})
            param = matching_rule.get("Parameter", "?")
            logic = str(matching_rule.get("Logic", ""))[:80]
            r.append(f"    {cid} ({param}): {count} violations — {logic}")
    r.append("")
    # ─── SECTION 6: IMPROVEMENT SUGGESTIONS ───
    r.append("## 6. IMPROVEMENT SUGGESTIONS")
    r.append("")
    if blocked:
        total_blocks = sum(d["count"] for d in blocked.values())
        top_blocker = max(blocked.items(), key=lambda x: x[1]["count"])
        r.append(f"  Total blocks: {total_blocks}")
        r.append(f"  #1 blocker: {top_blocker[0]} ({top_blocker[1]['count']} blocks in {top_blocker[1]['function']})")
        if "STALE" in top_blocker[0]:
            r.append(f"    → ACTION: Indicator freshness is the top bottleneck. Speed up indicator cycles or relax staleness threshold.")
        if "NO_LOSS" in top_blocker[0]:
            r.append(f"    → ACTION: NO_LOSS is blocking exits. This is BY DESIGN — verify L/S ratio is hedging these positions.")
        if "RATIO" in top_blocker[0]:
            r.append(f"    → ACTION: L/S ratio imbalance blocking opens. Rebalance by opening opposite-side positions.")
        if "BRAKE" in top_blocker[0]:
            r.append(f"    → ACTION: Emergency brake = too many trades/hour. Check for spam loops in reentry or reduce logic.")
        if "BUDGET" in top_blocker[0]:
            r.append(f"    → ACTION: Tradier budget exceeded. Increase SwingBudget or reduce position sizes.")
    r.append("")
    r.append(f"Report generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    return "\n".join(r)


def run_audit(date_str=None):
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    logger.info(f"Running trade quality audit for {date_str}")
    rules = load_backtest_rules()
    crypto_actual = load_actual_trades(CRYPTO_HISTORY_DIR, CRYPTO_ACCOUNTS, date_str, "crypto")
    tradier_actual = load_actual_trades(TRADIER_HISTORY_DIR, STOCK_ACCOUNTS, date_str, "tradier")
    decisions = load_decisions(date_str, CRYPTO_ACCOUNTS)
    blocked = load_blocked_trades(date_str, CRYPTO_ACCOUNTS)
    report = generate_report(date_str, crypto_actual, tradier_actual, decisions, blocked, rules)
    report_path = REPORT_DIR / f"audit_{date_str}.txt"
    with open(report_path, "w") as f:
        f.write(report)
    logger.info(f"Report written to {report_path}")
    print(report)
    return report_path


REPORT_TIMES_ET = [(8, 30, "morning"), (13, 0, "midday"), (16, 30, "closing")]


def _get_et_now():
    if ZoneInfo:
        return datetime.now(ZoneInfo("America/New_York"))
    return datetime.now(timezone(timedelta(hours=-5)))


def _next_report_time():
    now_et = _get_et_now()
    for hour, minute, label in REPORT_TIMES_ET:
        target = now_et.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target > now_et:
            return target, label
    tomorrow = now_et + timedelta(days=1)
    h, m, label = REPORT_TIMES_ET[0]
    return tomorrow.replace(hour=h, minute=m, second=0, microsecond=0), label


async def daemon_loop():
    """Run audit 3x daily: 8am, 1pm, 4:30pm ET. After each report, trigger auto_optimizer."""
    logger.info("[AUDITOR_DAEMON] Started — 3 reports/day: 8:00am, 1:00pm, 4:30pm ET")
    while True:
        try:
            target, label = _next_report_time()
            now_et = _get_et_now()
            wait_seconds = (target - now_et).total_seconds()
            logger.info(f"[AUDITOR_DAEMON] Next: {label} report at {target.strftime('%H:%M ET')} in {wait_seconds / 3600:.1f}h")
            await asyncio.sleep(max(wait_seconds, 1))
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            report_path = run_audit(today)
            logger.info(f"[AUDITOR_DAEMON] {label} report done: {report_path}")
            try:
                import subprocess
                optimizer = str(BASE_PATH / "auto_optimizer.py")
                if Path(optimizer).exists():
                    logger.info(f"[AUDITOR_DAEMON] Launching auto_optimizer for {report_path}")
                    subprocess.Popen([sys.executable, optimizer, "--report", str(report_path)], cwd=str(BASE_PATH))
            except Exception as opt_err:
                logger.warning(f"[AUDITOR_DAEMON] auto_optimizer launch failed: {opt_err}")
        except Exception as e:
            logger.error(f"[AUDITOR_DAEMON] Error: {e}")
            await asyncio.sleep(600)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        asyncio.run(daemon_loop())
    elif "--date" in sys.argv:
        idx = sys.argv.index("--date")
        date_str = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        run_audit(date_str)
    else:
        run_audit()
