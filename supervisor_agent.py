#!/opt/anaconda3/envs/binance_env/bin/python
"""Supervisor Agent — 24/7 autonomous orchestrator for backtesting, analysis, and config tuning.

Delegates between local MacBook (trading + light analysis) and server (heavy backtests with 4-6yr klines).
Maintains a prioritized test queue, applies validated improvements, enforces safety guards.

Survives reboots via launchd (Mac) / systemd (server).

Usage:
  python supervisor_agent.py                  # Full daemon mode
  python supervisor_agent.py --status         # Show current status
  python supervisor_agent.py --report         # Generate and print report
  python supervisor_agent.py --queue          # Show test queue
  python supervisor_agent.py --analyze-today  # Analyze today's trades and recommend
"""
# pylint: disable=W,C,R,I
import argparse
import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ═══ PATH & ENV ═════════════════════════════════════════════════════════════
IS_MAC = platform.system() == "Darwin"
if IS_MAC:
    BASE_PATH = Path("/Users/niels/Documents/binance")
    PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
    LOG_DIR = Path("/Users/niels/logs")
else:
    BASE_PATH = Path("/home/niels/binance")
    PYTHON = "/home/niels/.conda/envs/binance_env/bin/python"
    LOG_DIR = Path("/home/niels/logs")
SERVER = "s1-int"
SERVER_PYTHON = "/home/niels/.conda/envs/binance_env/bin/python"
SERVER_PATH = "/home/niels/binance"
SERVER_SANDBOX = "/home/niels/binance-sandbox"
DATA_DIR = BASE_PATH / "data"
DECISIONS_DIR = DATA_DIR / "decisions"
REPORTS_DIR = DATA_DIR / "backtest_reports"
QUEUE_FILE = DATA_DIR / "supervisor" / "test_queue.json"
STATUS_FILE = DATA_DIR / "supervisor" / "status.json"
HISTORY_FILE = DATA_DIR / "supervisor" / "history.jsonl"
PROHIBITED_FILE = DATA_DIR / "supervisor" / "prohibited_symbols.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)
for d in [REPORTS_DIR, QUEUE_FILE.parent]:
    d.mkdir(parents=True, exist_ok=True)

# ═══ LOGGING ═══════════════════════════════════════════════════════════════
logger = logging.getLogger("supervisor")
logger.setLevel(logging.DEBUG)
_fh = RotatingFileHandler(str(LOG_DIR / "supervisor_agent.log"), maxBytes=50 * 1024 * 1024, backupCount=5)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)
_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_ch)

# ═══ CONSTANTS ═════════════════════════════════════════════════════════════
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc"]
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + STOCK_ACCOUNTS
MARKET_OPEN_UTC = 13 * 60 + 30  # 13:30 UTC = 9:30 ET
MARKET_CLOSE_UTC = 20 * 60       # 20:00 UTC = 16:00 ET
# Protected params — NEVER change these
PROTECTED_PARAMS = {"STRICT_NO_LOSS", "STRICT_NO_LOSS_ACCOUNTS", "HEDGE_MODE", "HEDGE_ACCOUNTS", "MAX_POSITION_SIZE", "MAX_ORDER_VALUE", "ATR_TRAIL_ENABLED", "HARD_STOP_LOSS_MAX_PAIN", "STALE_DATA_HARD_STOP", "STALE_DATA_GAIN_EROSION"}
# Prohibited symbols — symbols that must NEVER be traded
DEFAULT_PROHIBITED = ["LUNAUSDT", "USTCUSDT", "TERRAUSDT"]

# ═══ SAFETY GUARDS ═════════════════════════════════════════════════════════

def load_prohibited_symbols():
    if PROHIBITED_FILE.exists():
        return set(json.loads(PROHIBITED_FILE.read_text()))
    PROHIBITED_FILE.write_text(json.dumps(DEFAULT_PROHIBITED, indent=2))
    return set(DEFAULT_PROHIBITED)

def check_prohibited_violations():
    """Scan symbols.json and active positions for prohibited symbols."""
    violations = []
    prohibited = load_prohibited_symbols()
    symbols_file = BASE_PATH / "symbols.json"
    if symbols_file.exists():
        try:
            symbols = json.loads(symbols_file.read_text())
            for s in symbols:
                if s in prohibited:
                    violations.append(f"PROHIBITED symbol {s} in symbols.json")
        except Exception:
            pass
    return violations

def safety_check_config_change(param, value):
    """Returns (safe: bool, reason: str) for a proposed config change."""
    if param in PROTECTED_PARAMS:
        return False, f"PROTECTED param: {param} cannot be changed"
    if "STOP_LOSS" in param.upper() and str(value).lower() not in ("false", "0", "none", "disabled"):
        return False, f"Enabling stop loss {param}={value} is PROHIBITED"
    if param == "MIN_GAIN_TO_BUY_AGGRESSIVELY":
        try:
            v = float(value)
            if v < 2.5:
                return False, f"MIN_GAIN_TO_BUY_AGGRESSIVELY={v} below minimum 2.5"
        except (ValueError, TypeError):
            pass
    if "GAIN" in param.upper() and "MIN" not in param.upper():
        try:
            v = float(value)
            if v < 0:
                return False, f"Negative gain threshold {param}={v} would close losers"
        except (ValueError, TypeError):
            pass
    return True, "OK"

# ═══ SERVER COMMUNICATION ═════════════════════════════════════════════════

def ssh_cmd(cmd, timeout=30):
    """Run command on server via SSH. Returns (success, stdout)."""
    try:
        result = subprocess.run(["ssh", SERVER, cmd], capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)

def server_load():
    """Get server CPU load and running processes."""
    ok, out = ssh_cmd("uptime; echo '---'; ps aux | grep 'python.*backtest' | grep -v grep | wc -l; echo '---'; nproc")
    if not ok:
        return None
    parts = out.split("---")
    return {"uptime": parts[0].strip() if len(parts) > 0 else "?", "backtest_count": int(parts[1].strip()) if len(parts) > 1 else 0, "cores": int(parts[2].strip()) if len(parts) > 2 else 16}

def server_start_backtest(script, args="", workers=15):
    """Start a backtest on the server in background."""
    cmd = f"cd {SERVER_SANDBOX} && nohup {SERVER_PYTHON} {script} {args} --workers {workers} --resume > /home/niels/logs/{Path(script).stem}.log 2>&1 & echo $!"
    ok, pid = ssh_cmd(cmd, timeout=15)
    if ok and pid.strip().isdigit():
        logger.info(f"[SERVER] Started {script} with PID {pid.strip()}")
        return int(pid.strip())
    # Try without --workers and --resume
    cmd = f"cd {SERVER_SANDBOX} && nohup {SERVER_PYTHON} {script} {args} > /home/niels/logs/{Path(script).stem}.log 2>&1 & echo $!"
    ok, pid = ssh_cmd(cmd, timeout=15)
    if ok and pid.strip().isdigit():
        logger.info(f"[SERVER] Started {script} (no workers flag) with PID {pid.strip()}")
        return int(pid.strip())
    logger.error(f"[SERVER] Failed to start {script}: {pid}")
    return None

def server_is_running(script_name):
    """Check if a script is running on the server."""
    ok, out = ssh_cmd(f"pgrep -f '{script_name}'")
    return ok and bool(out.strip())

# ═══ TRADE ANALYSIS ═══════════════════════════════════════════════════════

def analyze_trades(days_back=1):
    """Analyze recent trade decisions for problems."""
    now = datetime.now(timezone.utc)
    results = {"accounts": {}, "mass_closes": [], "premature_exits": [], "exit_reasons": Counter(), "total_trades": 0, "total_closes": 0, "total_opens": 0, "winning_closes": 0, "losing_closes": 0}
    for d in range(days_back):
        date_str = (now - timedelta(days=d)).strftime("%Y%m%d")
        for acct in ALL_ACCOUNTS:
            fpath = DECISIONS_DIR / f"decisions_{acct}_{date_str}.jsonl"
            if not fpath.exists():
                continue
            acct_data = results["accounts"].setdefault(acct, {"opens": 0, "closes": 0, "holds": 0, "win_closes": 0, "loss_closes": 0, "premature_exits": [], "close_timestamps": []})
            for line in fpath.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    d_rec = json.loads(line)
                except Exception:
                    continue
                action = (d_rec.get("action", "") or "").upper()
                results["total_trades"] += 1
                if "OPEN" in action or action in ("BUY", "SELL"):
                    acct_data["opens"] += 1
                    results["total_opens"] += 1
                elif "CLOSE" in action or "EXIT" in action or "REDUCE" in action:
                    acct_data["closes"] += 1
                    results["total_closes"] += 1
                    trade = d_rec.get("trade", {})
                    gain = trade.get("gain_pct", d_rec.get("gain_pct", None))
                    reason = d_rec.get("reason_text", d_rec.get("reason", ""))
                    ts = d_rec.get("timestamp", "")
                    if gain is not None and isinstance(gain, (int, float)):
                        if gain > 0:
                            acct_data["win_closes"] += 1
                            results["winning_closes"] += 1
                        else:
                            acct_data["loss_closes"] += 1
                            results["losing_closes"] += 1
                        if 0 < gain < 3.0 and "CLOSE" in action:
                            reason_short = str(reason).split("_")[0:3]
                            reason_key = "_".join(reason_short) if reason_short else "UNKNOWN"
                            results["exit_reasons"][reason_key] += 1
                            acct_data["premature_exits"].append({"symbol": d_rec.get("trade", {}).get("symbol", d_rec.get("position_key", "?")), "gain": gain, "reason": str(reason)[:80], "timestamp": ts})
                    if ts:
                        acct_data["close_timestamps"].append(ts)
                elif "HOLD" in action:
                    acct_data["holds"] += 1
    # Detect mass-close events (>10 closes within 5 min)
    for acct, adata in results["accounts"].items():
        timestamps = sorted(adata.get("close_timestamps", []))
        if len(timestamps) < 10:
            continue
        for i in range(len(timestamps) - 9):
            try:
                t_start = datetime.fromisoformat(timestamps[i].replace("Z", "+00:00"))
                t_end = datetime.fromisoformat(timestamps[i + 9].replace("Z", "+00:00"))
                if (t_end - t_start).total_seconds() < 300:
                    count = 1
                    for j in range(i + 10, len(timestamps)):
                        try:
                            t_j = datetime.fromisoformat(timestamps[j].replace("Z", "+00:00"))
                            if (t_j - t_start).total_seconds() < 300:
                                count += 1
                            else:
                                break
                        except Exception:
                            break
                    results["mass_closes"].append({"account": acct, "start": timestamps[i], "count": 10 + count, "window": "5min"})
                    break
            except Exception:
                continue
    # Top premature exit reasons
    results["top_premature_reasons"] = results["exit_reasons"].most_common(10)
    return results

# ═══ TEST QUEUE ═══════════════════════════════════════════════════════════

DEFAULT_QUEUE = [
    {"id": "hedge_strategies", "script": "backtest_hedge_strategies.py", "location": "server_sandbox", "args": "--massive --start 2023-01-01 --end 2025-12-31", "priority": 1, "status": "running", "workers": 15, "description": "988-config hedge strategy sweep"},
    {"id": "master_revalidation", "script": "backtest_master_revalidation.py", "location": "server", "args": "", "priority": 2, "status": "queued", "workers": 15, "description": "30 symbols × 6 TFs × 2M combos parameter revalidation"},
    {"id": "sba_v2", "script": "backtest_sba_mq.py", "location": "server", "args": "", "priority": 3, "status": "queued", "workers": 15, "description": "260-config symbol bundle ablation v2"},
    {"id": "wt_15m_deep", "script": "backtest_wt_15m_deep.py", "location": "local", "args": "", "priority": 4, "status": "queued", "workers": 4, "description": "WT parameter deep sweep (ESA/Channel/Signal)"},
    {"id": "wt_ultimate_stocks", "script": "backtest_wt_ultimate.py", "location": "local", "args": "--market stocks", "priority": 5, "status": "queued", "workers": 4, "description": "WT ultimate sweep for stocks"},
    {"id": "v4_sweep_tradier", "script": "backtest_v4_sweep_tradier.py", "location": "server", "args": "", "priority": 6, "status": "queued", "workers": 15, "description": "V4 stock config sweep 128 symbols"},
    {"id": "tradier_stoploss_analysis", "script": "backtest_tradier_stoploss.py", "location": "server", "args": "", "priority": 7, "status": "queued", "workers": 15, "description": "Tradier exit strategy analysis (NO stop loss, profit-taking only)"},
    {"id": "rankings_optimize", "script": "backtest_rankings_optimize.py", "location": "server", "args": "", "priority": 8, "status": "queued", "workers": 15, "description": "Ranking algorithm parameter optimization"},
]

def load_queue():
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text())
    save_queue(DEFAULT_QUEUE)
    return DEFAULT_QUEUE

def save_queue(queue):
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))

def next_queued_test(location=None):
    """Get next queued test, optionally filtered by location."""
    queue = load_queue()
    for t in sorted(queue, key=lambda x: x.get("priority", 99)):
        if t["status"] == "queued":
            if location and t.get("location", "").startswith(location):
                return t
            elif not location:
                return t
    return None

def update_test_status(test_id, status, details=""):
    queue = load_queue()
    for t in queue:
        if t["id"] == test_id:
            t["status"] = status
            if details:
                t["last_update"] = details
            t["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_queue(queue)

# ═══ CONFIG APPLICATION ═══════════════════════════════════════════════════

def apply_config_change(config_file, param, new_value, reason=""):
    """Safely apply a config change with backup and safety checks."""
    safe, msg = safety_check_config_change(param, new_value)
    if not safe:
        logger.warning(f"[SAFETY] Blocked config change: {msg}")
        return False, msg
    config_path = BASE_PATH / config_file
    if not config_path.exists():
        return False, f"Config file not found: {config_file}"
    # Check LOCKED_FILES.md
    locked_path = BASE_PATH / "LOCKED_FILES.md"
    if locked_path.exists():
        locked_content = locked_path.read_text()
        if config_file in locked_content:
            return False, f"File {config_file} is LOCKED"
    # Backup
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    backup = BASE_PATH / "backups" / f"before_supervisor_{param}_{ts}.py"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, backup)
    # Apply
    content = config_path.read_text()
    # Handle dataclass format: PARAM: type = value
    pattern = rf"(^\s*{re.escape(param)}:\s*\w+\s*=\s*)\S+"
    if re.search(pattern, content, re.MULTILINE):
        new_content = re.sub(pattern, rf"\g<1>{new_value}", content, count=1, flags=re.MULTILINE)
    else:
        # Handle simple format: PARAM = value
        pattern2 = rf"({re.escape(param)}\s*=\s*)\S+"
        if re.search(pattern2, content):
            new_content = re.sub(pattern2, rf"\g<1>{new_value}", content, count=1)
        else:
            return False, f"Param {param} not found in {config_file}"
    config_path.write_text(new_content)
    # Verify
    verify_content = config_path.read_text()
    if str(new_value) not in verify_content:
        # Restore backup
        shutil.copy2(backup, config_path)
        return False, f"Verification failed — restored backup"
    # Log to history
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "param": param, "value": str(new_value), "config": config_file, "reason": reason, "backup": str(backup)}
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    logger.info(f"[APPLY] {config_file}: {param} = {new_value} (reason: {reason})")
    return True, "Applied"

# ═══ REPORT GENERATION ════════════════════════════════════════════════════

def generate_report():
    """Generate comprehensive 12h status report."""
    now = datetime.now(timezone.utc)
    report_lines = []
    report_lines.append("=" * 70)
    report_lines.append(f"  SUPERVISOR AGENT REPORT — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    report_lines.append("=" * 70)
    # 1. Trade analysis
    analysis = analyze_trades(days_back=1)
    report_lines.append("\n── TRADE ANALYSIS (last 24h) ──")
    report_lines.append(f"Total decisions: {analysis['total_trades']}")
    report_lines.append(f"Opens: {analysis['total_opens']} | Closes: {analysis['total_closes']}")
    if analysis['total_closes'] > 0:
        wr = analysis['winning_closes'] / analysis['total_closes'] * 100
        report_lines.append(f"Win rate on closes: {wr:.1f}% ({analysis['winning_closes']}W / {analysis['losing_closes']}L)")
    report_lines.append("\nPer-account breakdown:")
    for acct in ALL_ACCOUNTS:
        adata = analysis["accounts"].get(acct)
        if not adata:
            continue
        acct_wr = (adata["win_closes"] / adata["closes"] * 100) if adata["closes"] > 0 else 0
        premature = len(adata.get("premature_exits", []))
        report_lines.append(f"  {acct:4s}: opens={adata['opens']:3d} closes={adata['closes']:3d} WR={acct_wr:5.1f}% premature_exits={premature}")
    if analysis["mass_closes"]:
        report_lines.append(f"\n*** MASS CLOSE EVENTS DETECTED ***")
        for mc in analysis["mass_closes"]:
            report_lines.append(f"  {mc['account']}: {mc['count']} closes in {mc['window']} starting {mc['start']}")
    if analysis["top_premature_reasons"]:
        report_lines.append("\nTop premature exit reasons (<3% gain):")
        for reason, count in analysis["top_premature_reasons"][:10]:
            report_lines.append(f"  {count:4d}x  {reason}")
    # 2. Safety checks
    report_lines.append("\n── SAFETY CHECKS ──")
    violations = check_prohibited_violations()
    if violations:
        report_lines.append("*** VIOLATIONS FOUND ***")
        for v in violations:
            report_lines.append(f"  {v}")
    else:
        report_lines.append("All clear — no prohibited symbols detected")
    # 3. Test queue status
    report_lines.append("\n── TEST QUEUE ──")
    queue = load_queue()
    for t in sorted(queue, key=lambda x: x.get("priority", 99)):
        status_icon = {"running": ">>", "queued": "..", "completed": "OK", "failed": "XX"}.get(t["status"], "??")
        loc = t.get("location", "?")[:6]
        report_lines.append(f"  [{status_icon}] P{t.get('priority', '?')} {t['id']:30s} ({loc}) {t['status']:10s} {t.get('description', '')[:40]}")
    # 4. Server status
    report_lines.append("\n── SERVER STATUS ──")
    sload = server_load()
    if sload:
        report_lines.append(f"Uptime: {sload['uptime']}")
        report_lines.append(f"Running backtests: {sload['backtest_count']} processes on {sload['cores']} cores")
    else:
        report_lines.append("Server unreachable")
    # 5. Local status
    report_lines.append("\n── LOCAL STATUS ──")
    try:
        result = subprocess.run(["uptime"], capture_output=True, text=True)
        report_lines.append(f"Uptime: {result.stdout.strip()}")
    except Exception:
        pass
    try:
        result = subprocess.run(["bash", "-c", "ps aux | grep 'python.*ez_\\|python.*tradier_' | grep -v grep | wc -l"], capture_output=True, text=True)
        report_lines.append(f"Trading services: {result.stdout.strip()}")
    except Exception:
        pass
    try:
        result = subprocess.run(["bash", "-c", "ps aux | grep 'python.*backtest' | grep -v grep | wc -l"], capture_output=True, text=True)
        report_lines.append(f"Local backtests: {result.stdout.strip()}")
    except Exception:
        pass
    # 6. Recent config changes
    report_lines.append("\n── RECENT CONFIG CHANGES ──")
    if HISTORY_FILE.exists():
        lines = HISTORY_FILE.read_text().strip().split("\n")
        recent = lines[-10:] if len(lines) > 10 else lines
        for line in recent:
            try:
                entry = json.loads(line)
                report_lines.append(f"  {entry['timestamp'][:16]} {entry['config']:20s} {entry['param']} = {entry['value']}")
            except Exception:
                pass
    else:
        report_lines.append("  No changes applied yet")
    report_lines.append("\n" + "=" * 70)
    report_lines.append("  END OF REPORT")
    report_lines.append("=" * 70)
    report_text = "\n".join(report_lines)
    # Save report
    report_file = REPORTS_DIR / f"supervisor_{now.strftime('%Y%m%d_%H%M')}.txt"
    report_file.write_text(report_text)
    # Keep last 28 reports (14 days)
    reports = sorted(REPORTS_DIR.glob("supervisor_*.txt"), reverse=True)
    for old in reports[28:]:
        old.unlink()
    return report_text

# ═══ MAIN DAEMON LOOP ════════════════════════════════════════════════════

class SupervisorDaemon:
    def __init__(self):
        self._running = True
        self._last_analysis = 0
        self._last_report = 0
        self._last_queue_check = 0
        self._last_safety_check = 0
        self._last_server_check = 0
        self._last_autotuner_check = 0

    def stop(self, *args):
        logger.info("Shutdown signal received")
        self._running = False

    def run(self):
        logger.info("=" * 60)
        logger.info("SUPERVISOR AGENT STARTING — 24/7 autonomous orchestrator")
        logger.info("=" * 60)
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        # Initial report
        try:
            report = generate_report()
            logger.info("Initial report generated")
        except Exception as e:
            logger.error(f"Initial report failed: {e}")
        while self._running:
            try:
                now = time.time()
                now_utc = datetime.now(timezone.utc)
                minute_of_day = now_utc.hour * 60 + now_utc.minute
                is_market_hours = MARKET_OPEN_UTC <= minute_of_day <= MARKET_CLOSE_UTC and now_utc.weekday() < 5
                # Every 60s: safety check
                if now - self._last_safety_check >= 60:
                    self._last_safety_check = now
                    violations = check_prohibited_violations()
                    if violations:
                        logger.critical(f"SAFETY VIOLATIONS: {violations}")
                # Every 5 min: check server processes, manage queue
                if now - self._last_server_check >= 300:
                    self._last_server_check = now
                    self._manage_server_queue()
                # Every 2h: analyze recent trades
                if now - self._last_analysis >= 7200:
                    self._last_analysis = now
                    try:
                        analysis = analyze_trades(days_back=1)
                        if analysis["mass_closes"]:
                            logger.critical(f"MASS CLOSE DETECTED: {analysis['mass_closes']}")
                        if analysis["top_premature_reasons"]:
                            top_reason, top_count = analysis["top_premature_reasons"][0]
                            logger.info(f"Top premature exit: {top_reason} ({top_count}x)")
                    except Exception as e:
                        logger.error(f"Analysis error: {e}")
                # Every 4h: check auto-tuner recommendations and apply if validated
                if now - self._last_autotuner_check >= 14400:
                    self._last_autotuner_check = now
                    if not is_market_hours:
                        self._check_and_apply_recommendations()
                # Every 12h: full report
                if now - self._last_report >= 43200:
                    self._last_report = now
                    try:
                        report = generate_report()
                        logger.info(f"12h report generated ({len(report)} chars)")
                    except Exception as e:
                        logger.error(f"Report generation failed: {e}")
                # Every 10 min during off-hours: manage local backtests
                if not is_market_hours and now - self._last_queue_check >= 600:
                    self._last_queue_check = now
                    self._manage_local_queue()
                time.sleep(30)
            except Exception as e:
                logger.error(f"Daemon loop error: {e}", exc_info=True)
                time.sleep(60)
        logger.info("Supervisor daemon stopped")

    def _manage_server_queue(self):
        """Check server backtests, start next if current finished."""
        queue = load_queue()
        server_tests = [t for t in queue if t.get("location", "").startswith("server")]
        running = [t for t in server_tests if t["status"] == "running"]
        for t in running:
            if not server_is_running(t["script"]):
                logger.info(f"[QUEUE] Server test {t['id']} finished")
                update_test_status(t["id"], "completed", f"Finished at {datetime.now(timezone.utc).isoformat()}")
                running.remove(t)
        if not running:
            next_test = next_queued_test(location="server")
            if next_test:
                logger.info(f"[QUEUE] Starting next server test: {next_test['id']} ({next_test['description']})")
                loc = next_test.get("location", "server")
                script_path = next_test["script"]
                if loc == "server_sandbox":
                    script_path = f"{SERVER_SANDBOX}/{next_test['script']}"
                elif loc == "server":
                    script_path = f"{SERVER_PATH}/{next_test['script']}"
                pid = server_start_backtest(script_path, next_test.get("args", ""), next_test.get("workers", 15))
                if pid:
                    update_test_status(next_test["id"], "running", f"PID {pid}")
                else:
                    update_test_status(next_test["id"], "failed", "Could not start")

    def _manage_local_queue(self):
        """Start local backtests during off-market hours with nice priority."""
        try:
            result = subprocess.run(["bash", "-c", "ps aux | grep 'python.*backtest' | grep -v grep | wc -l"], capture_output=True, text=True)
            running_count = int(result.stdout.strip())
        except Exception:
            running_count = 0
        if running_count >= 2:
            return
        # Check queue for completed local tests
        queue = load_queue()
        for t in queue:
            if t.get("location") == "local" and t["status"] == "running":
                try:
                    result = subprocess.run(["pgrep", "-f", t["script"]], capture_output=True, text=True)
                    if not result.stdout.strip():
                        update_test_status(t["id"], "completed", f"Finished at {datetime.now(timezone.utc).isoformat()}")
                except Exception:
                    pass
        next_test = next_queued_test(location="local")
        if next_test:
            script_path = str(BASE_PATH / next_test["script"])
            args = next_test.get("args", "")
            cmd = f"nice -n 15 {PYTHON} {script_path} {args}"
            logger.info(f"[LOCAL] Starting: {next_test['id']} ({next_test['description']})")
            try:
                subprocess.Popen(cmd, shell=True, stdout=open(str(LOG_DIR / f"{next_test['id']}.log"), "a"), stderr=subprocess.STDOUT, start_new_session=True)
                update_test_status(next_test["id"], "running")
            except Exception as e:
                logger.error(f"[LOCAL] Failed to start {next_test['id']}: {e}")
                update_test_status(next_test["id"], "failed", str(e))

    def _check_and_apply_recommendations(self):
        """Read auto-tuner recommendations and apply HIGH-confidence ones outside market hours."""
        rec_file = DATA_DIR / "auto_tuner" / "recommended_config.json"
        if not rec_file.exists():
            return
        try:
            recs = json.loads(rec_file.read_text())
        except Exception:
            return
        recommendations = recs.get("recommendations", [])
        high_recs = [r for r in recommendations if r.get("confidence") == "HIGH"]
        if not high_recs:
            return
        logger.info(f"[AUTOTUNER] Found {len(high_recs)} HIGH confidence recommendations")
        for rec in high_recs:
            param = rec.get("param", "")
            new_val = rec.get("recommended", "")
            reason = rec.get("reason", "")
            if not param or not new_val:
                continue
            # Determine config file
            config_file = "config_tradier.py" if any(x in param.upper() for x in ["TRADIER", "TRC_", "TRB_", "STOCK"]) else "config.py"
            safe, msg = safety_check_config_change(param, new_val)
            if not safe:
                logger.warning(f"[AUTOTUNER] Blocked: {msg}")
                continue
            success, result = apply_config_change(config_file, param, new_val, f"auto-tuner HIGH: {reason}")
            if success:
                logger.info(f"[AUTOTUNER] Applied: {param} = {new_val}")
            else:
                logger.warning(f"[AUTOTUNER] Failed to apply {param}: {result}")

# ═══ SAVE STATUS ═══════════════════════════════════════════════════════════

def save_status(phase, details=""):
    status = {"phase": phase, "timestamp": datetime.now(timezone.utc).isoformat(), "details": details, "pid": os.getpid()}
    STATUS_FILE.write_text(json.dumps(status, indent=2))

# ═══ MAIN ═════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Supervisor Agent — 24/7 autonomous backtesting orchestrator")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--report", action="store_true", help="Generate and print report")
    parser.add_argument("--queue", action="store_true", help="Show test queue")
    parser.add_argument("--analyze-today", action="store_true", help="Analyze today's trades")
    args = parser.parse_args()
    if args.status:
        if STATUS_FILE.exists():
            print(STATUS_FILE.read_text())
        else:
            print("No status file — supervisor not running")
        return
    if args.report:
        print(generate_report())
        return
    if args.queue:
        queue = load_queue()
        print(f"\n{'═' * 60}")
        print(f" TEST QUEUE ({len(queue)} items)")
        print(f"{'═' * 60}")
        for t in sorted(queue, key=lambda x: x.get("priority", 99)):
            status_icon = {"running": ">>", "queued": "..", "completed": "OK", "failed": "XX"}.get(t["status"], "??")
            print(f"  [{status_icon}] P{t.get('priority', '?')} {t['id']:30s} {t['status']:10s} {t.get('location', '?'):15s} {t.get('description', '')}")
        return
    if args.analyze_today:
        analysis = analyze_trades(days_back=1)
        print(f"\nTotal trades: {analysis['total_trades']}")
        print(f"Opens: {analysis['total_opens']} | Closes: {analysis['total_closes']}")
        if analysis['total_closes'] > 0:
            wr = analysis['winning_closes'] / analysis['total_closes'] * 100
            print(f"Win rate: {wr:.1f}%")
        if analysis["mass_closes"]:
            print(f"\n*** MASS CLOSES ***")
            for mc in analysis["mass_closes"]:
                print(f"  {mc['account']}: {mc['count']} in {mc['window']} @ {mc['start']}")
        if analysis["top_premature_reasons"]:
            print(f"\nTop premature exit reasons (<3%):")
            for reason, count in analysis["top_premature_reasons"][:10]:
                print(f"  {count:4d}x  {reason}")
        return
    # Daemon mode
    save_status("starting", "Supervisor daemon initializing")
    daemon = SupervisorDaemon()
    save_status("running", f"PID {os.getpid()}")
    daemon.run()
    save_status("stopped", "Clean shutdown")


if __name__ == "__main__":
    main()
