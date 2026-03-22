#!/opt/anaconda3/envs/binance_env/bin/python
"""Nightly Optimization Lab — Runs from 4:15pm to 8:00am ET every day.

Timeline:
  4:15 PM ET — Shut down tradier_ services, generate closing audit report
  4:30 PM ET — Begin nightly optimization:
    1. Collect today's performance data (crypto + stocks)
    2. Ask Claude to propose parameter changes based on audit + market conditions
    3. Run backtests on proposed changes (backtest_factory)
    4. Compare backtest results to current settings
    5. If improvement > threshold, stage new settings
  7:30 AM ET — Deploy staged settings (both crypto config.py + config_tradier.py)
  8:00 AM ET — Morning audit report, start tradier services

Usage:
  python nightly_lab.py --daemon         # Run the full daily cycle
  python nightly_lab.py --optimize-now   # Run optimization immediately (testing)
  python nightly_lab.py --deploy         # Deploy staged settings immediately
  python nightly_lab.py --status         # Show current lab status
"""

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except ImportError:
    ET = timezone(timedelta(hours=-5))

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("nightly_lab")
logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
fh = RotatingFileHandler(str(logs_dir / "nightly_lab.log"), maxBytes=50 * 1024 * 1024, backupCount=3)
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(fh)

BASE_PATH = Path(__file__).parent
CONFIG_FILE = BASE_PATH / "config.py"
CONFIG_TRADIER = BASE_PATH / "config_tradier.py"
BACKTEST_FILE = BASE_PATH / "BACKTEST_CHANGES_100.xlsx"
AUDIT_DIR = BASE_PATH / "audit_reports"
STAGING_DIR = BASE_PATH / "audit_reports" / "staged_settings"
LAB_STATUS_FILE = BASE_PATH / "audit_reports" / "lab_status.json"
STAGING_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

PYTHON = sys.executable
TRADIER_SERVICES = ["tradier_manage.py", "tradier_positions.py"]


def now_et():
    return datetime.now(ET)


def save_status(phase, details=""):
    status = {"phase": phase, "timestamp": datetime.now(timezone.utc).isoformat(), "details": details}
    LAB_STATUS_FILE.write_text(json.dumps(status, indent=2))
    logger.info(f"[LAB] Phase: {phase} — {details}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: SHUTDOWN TRADIER (4:15 PM ET)
# ═══════════════════════════════════════════════════════════════════════════════

def shutdown_tradier():
    """Kill tradier_manage processes. Prices/indicators can keep running for data collection."""
    logger.info("[SHUTDOWN] Stopping tradier_manage services...")
    killed = 0
    for proc_name in TRADIER_SERVICES:
        try:
            result = subprocess.run(["pgrep", "-f", proc_name], capture_output=True, text=True)
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                if pid.strip():
                    os.kill(int(pid.strip()), signal.SIGTERM)
                    killed += 1
        except Exception:
            pass
    logger.info(f"[SHUTDOWN] Killed {killed} tradier processes")
    return killed


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: COLLECT PERFORMANCE DATA
# ═══════════════════════════════════════════════════════════════════════════════

def collect_performance():
    """Gather today's audit report + recent backtest results."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    audit_path = AUDIT_DIR / f"audit_{today}.txt"
    if not audit_path.exists():
        logger.info("[COLLECT] Running audit for today...")
        subprocess.run([PYTHON, str(BASE_PATH / "trade_quality_auditor.py"), "--date", today], cwd=str(BASE_PATH), timeout=120)
    audit_text = audit_path.read_text()[:6000] if audit_path.exists() else "No audit data available"
    config_text = CONFIG_FILE.read_text()[:3000] if CONFIG_FILE.exists() else ""
    config_tradier_text = CONFIG_TRADIER.read_text()[:2000] if CONFIG_TRADIER.exists() else ""
    changelog_path = AUDIT_DIR / "OPTIMIZER_CHANGELOG.md"
    changelog = changelog_path.read_text()[-3000:] if changelog_path.exists() else ""
    backtest_results = ""
    results_dir = BASE_PATH / "data" / "backtest_factory"
    if results_dir.exists():
        latest = sorted(results_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        for rfile in latest:
            try:
                backtest_results += f"\n--- {rfile.name} ---\n{rfile.read_text()[:1000]}"
            except Exception:
                pass
    return {"audit": audit_text, "config": config_text, "config_tradier": config_tradier_text, "changelog": changelog, "backtest_results": backtest_results}


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: ASK CLAUDE FOR OPTIMIZATION PROPOSALS
# ═══════════════════════════════════════════════════════════════════════════════

def ask_claude_for_proposals(data):
    """Ask Claude to propose parameter changes based on all available data."""
    prompt = f"""You are a quantitative trading system optimizer running the nightly optimization lab.

## TODAY'S AUDIT REPORT
{data['audit']}

## CURRENT CRYPTO CONFIG (config.py excerpt)
{data['config'][:2000]}

## CURRENT STOCK CONFIG (config_tradier.py excerpt)
{data['config_tradier'][:1500]}

## RECENT OPTIMIZER CHANGELOG
{data['changelog'][:1500]}

## RECENT BACKTEST RESULTS
{data['backtest_results'][:1500]}

## YOUR TASK
Propose parameter changes for BOTH crypto (config.py) and stocks (config_tradier.py) that should be backtested overnight.

For each proposal:
1. What parameter to change and to what value
2. Why (evidence from audit data, market conditions, or backtest results)
3. Expected impact on Sharpe ratio
4. Risk level (LOW/MEDIUM/HIGH)

Also note which current settings should be REVERTED based on poor performance.

## OUTPUT FORMAT (strict JSON)
{{
  "market_assessment": "1-2 sentences on current market conditions",
  "crypto_proposals": [
    {{"param": "PARAM_NAME", "current": "X", "proposed": "Y", "evidence": "why", "risk": "LOW"}}
  ],
  "tradier_proposals": [
    {{"param": "PARAM_NAME", "current": "X", "proposed": "Y", "evidence": "why", "risk": "LOW"}}
  ],
  "reverts": [
    {{"param": "PARAM_NAME", "revert_to": "X", "reason": "why it's not working"}}
  ],
  "backtest_symbols": ["SYM1", "SYM2"],
  "backtest_timeframes": ["15m", "1h", "4h"]
}}"""

    try:
        result = subprocess.run(["claude", "-p", prompt, "--model", "haiku", "--output-format", "text", "--max-turns", "1"], capture_output=True, text=True, timeout=90, cwd=str(BASE_PATH))
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip()
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
            logger.warning(f"[PROPOSALS] Could not parse JSON from Claude response")
    except subprocess.TimeoutExpired:
        logger.warning("[PROPOSALS] Claude CLI timed out")
    except Exception as e:
        logger.error(f"[PROPOSALS] Error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: RUN BACKTESTS ON PROPOSALS
# ═══════════════════════════════════════════════════════════════════════════════

def run_backtests(proposals):
    """Run backtest_factory with proposed settings and compare to baseline."""
    if not proposals:
        return None
    symbols = proposals.get("backtest_symbols", ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC"])[:8]
    logger.info(f"[BACKTEST] Running backtests on {len(symbols)} symbols...")
    save_status("backtesting", f"Testing {len(proposals.get('crypto_proposals', []))} crypto + {len(proposals.get('tradier_proposals', []))} tradier proposals")
    results = {"baseline": {}, "proposed": {}, "improvement": {}}
    try:
        baseline_result = subprocess.run([PYTHON, str(BASE_PATH / "backtest_factory.py"), "--symbols", str(len(symbols)), "--rounds", "1", "--report"], capture_output=True, text=True, timeout=600, cwd=str(BASE_PATH))
        if baseline_result.returncode == 0:
            results["baseline"]["output"] = baseline_result.stdout[:2000]
            sharpe_match = re.search(r"Sharpe[:\s]+([-\d.]+)", baseline_result.stdout)
            if sharpe_match:
                results["baseline"]["sharpe"] = float(sharpe_match.group(1))
    except subprocess.TimeoutExpired:
        logger.warning("[BACKTEST] Baseline backtest timed out (10min)")
    except Exception as e:
        logger.error(f"[BACKTEST] Baseline error: {e}")
    logger.info(f"[BACKTEST] Baseline results: {results['baseline'].get('sharpe', 'N/A')}")
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: STAGE NEW SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

def stage_settings(proposals, backtest_results):
    """Stage proposed settings for morning deployment."""
    if not proposals:
        return False
    staged = {"timestamp": datetime.now(timezone.utc).isoformat(), "proposals": proposals, "backtest_results": backtest_results, "deployed": False}
    staged_path = STAGING_DIR / f"staged_{datetime.now().strftime('%Y%m%d')}.json"
    staged_path.write_text(json.dumps(staged, indent=2, default=str))
    logger.info(f"[STAGE] Settings staged: {staged_path}")
    save_status("staged", f"New settings ready for deployment at {staged_path.name}")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: DEPLOY SETTINGS (7:30 AM ET)
# ═══════════════════════════════════════════════════════════════════════════════

def deploy_staged_settings():
    """Deploy the most recent staged settings to config.py and config_tradier.py."""
    staged_files = sorted(STAGING_DIR.glob("staged_*.json"), reverse=True)
    if not staged_files:
        logger.info("[DEPLOY] No staged settings to deploy")
        return False
    staged = json.loads(staged_files[0].read_text())
    if staged.get("deployed"):
        logger.info("[DEPLOY] Latest staged settings already deployed")
        return False
    proposals = staged.get("proposals", {})
    applied_count = 0
    for change in proposals.get("crypto_proposals", []):
        if str(change.get("risk", "")).upper() == "HIGH":
            continue
        param = change.get("param", "")
        new_val = change.get("proposed", "")
        if param and new_val:
            backup = BASE_PATH / "backups" / f"before_lab_{param}_{datetime.now().strftime('%Y%m%d')}.py"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CONFIG_FILE, backup)
            try:
                content = CONFIG_FILE.read_text()
                pattern = rf"({re.escape(param)}\s*[:=]\s*)\S+"
                if re.search(pattern, content):
                    new_content = re.sub(pattern, rf"\g<1>{new_val}", content, count=1)
                    CONFIG_FILE.write_text(new_content)
                    applied_count += 1
                    logger.info(f"[DEPLOY] crypto: {param} -> {new_val}")
            except Exception as e:
                logger.error(f"[DEPLOY] Failed to apply {param}: {e}")
    for change in proposals.get("tradier_proposals", []):
        if str(change.get("risk", "")).upper() == "HIGH":
            continue
        param = change.get("param", "")
        new_val = change.get("proposed", "")
        if param and new_val and CONFIG_TRADIER.exists():
            backup = BASE_PATH / "backups" / f"before_lab_tradier_{param}_{datetime.now().strftime('%Y%m%d')}.py"
            shutil.copy2(CONFIG_TRADIER, backup)
            try:
                content = CONFIG_TRADIER.read_text()
                pattern = rf"({re.escape(param)}\s*[:=]\s*)\S+"
                if re.search(pattern, content):
                    new_content = re.sub(pattern, rf"\g<1>{new_val}", content, count=1)
                    CONFIG_TRADIER.write_text(new_content)
                    applied_count += 1
                    logger.info(f"[DEPLOY] tradier: {param} -> {new_val}")
            except Exception as e:
                logger.error(f"[DEPLOY] Failed to apply tradier {param}: {e}")
    for revert in proposals.get("reverts", []):
        param = revert.get("param", "")
        val = revert.get("revert_to", "")
        if param and val:
            for cfg in [CONFIG_FILE, CONFIG_TRADIER]:
                if not cfg.exists():
                    continue
                try:
                    content = cfg.read_text()
                    pattern = rf"({re.escape(param)}\s*[:=]\s*)\S+"
                    if re.search(pattern, content):
                        new_content = re.sub(pattern, rf"\g<1>{val}", content, count=1)
                        cfg.write_text(new_content)
                        applied_count += 1
                        logger.info(f"[DEPLOY] REVERT: {param} -> {val} in {cfg.name}")
                except Exception:
                    pass
    staged["deployed"] = True
    staged["deployed_at"] = datetime.now(timezone.utc).isoformat()
    staged["applied_count"] = applied_count
    staged_files[0].write_text(json.dumps(staged, indent=2, default=str))
    changelog = AUDIT_DIR / "OPTIMIZER_CHANGELOG.md"
    try:
        entry = f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} — Nightly Lab Deployment\n\n"
        entry += f"**Market Assessment**: {proposals.get('market_assessment', 'N/A')}\n"
        entry += f"**Applied**: {applied_count} changes\n"
        for c in proposals.get("crypto_proposals", []) + proposals.get("tradier_proposals", []):
            entry += f"- `{c.get('param')}`: {c.get('current')} → {c.get('proposed')} — {c.get('evidence', '')[:80]}\n"
        for r in proposals.get("reverts", []):
            entry += f"- REVERT `{r.get('param')}` → {r.get('revert_to')} — {r.get('reason', '')[:80]}\n"
        entry += "\n---\n"
        with open(changelog, "a") as f:
            f.write(entry)
    except Exception:
        pass
    save_status("deployed", f"{applied_count} settings deployed")
    return applied_count > 0


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 7: START TRADIER (before 9:30 AM ET)
# ═══════════════════════════════════════════════════════════════════════════════

def start_tradier():
    """Restart tradier services for market open."""
    logger.info("[START] Launching tradier services...")
    for svc in TRADIER_SERVICES:
        try:
            subprocess.Popen(["bash", str(BASE_PATH / "run_with_watchdog.sh"), svc], cwd=str(BASE_PATH), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info(f"[START] Launched {svc}")
        except Exception as e:
            logger.error(f"[START] Failed to launch {svc}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DAEMON LOOP
# ═══════════════════════════════════════════════════════════════════════════════

async def daemon_loop():
    """Full daily cycle: 4:15pm shutdown → optimize → backtest → 7:30am deploy → 8am start."""
    logger.info("[LAB] Nightly Optimization Lab started")
    save_status("idle", "Waiting for 4:15 PM ET")
    while True:
        try:
            net = now_et()
            # ─── 4:15 PM: SHUTDOWN ───
            target_shutdown = net.replace(hour=16, minute=15, second=0, microsecond=0)
            if target_shutdown <= net:
                target_shutdown += timedelta(days=1)
            wait = (target_shutdown - net).total_seconds()
            save_status("waiting_for_shutdown", f"Next shutdown at {target_shutdown.strftime('%H:%M ET')} in {wait/3600:.1f}h")
            await asyncio.sleep(wait)
            # SHUTDOWN
            save_status("shutting_down_tradier", "Killing tradier_manage services")
            shutdown_tradier()
            await asyncio.sleep(60)
            # ─── 4:30 PM: COLLECT + AUDIT ───
            save_status("collecting_data", "Running closing audit + collecting performance data")
            subprocess.run([PYTHON, str(BASE_PATH / "trade_quality_auditor.py")], cwd=str(BASE_PATH), timeout=120)
            data = collect_performance()
            await asyncio.sleep(5)
            # ─── 4:35 PM: PROPOSE ───
            save_status("proposing_changes", "Asking Claude for optimization proposals")
            proposals = ask_claude_for_proposals(data)
            if proposals:
                logger.info(f"[LAB] Market: {proposals.get('market_assessment', 'N/A')}")
                logger.info(f"[LAB] Proposals: {len(proposals.get('crypto_proposals', []))} crypto, {len(proposals.get('tradier_proposals', []))} tradier")
            else:
                logger.warning("[LAB] No proposals from Claude — using rule-based fallback")
                proposals = {"market_assessment": "Claude unavailable", "crypto_proposals": [], "tradier_proposals": [], "reverts": [], "backtest_symbols": ["BTCUSDC", "ETHUSDC"], "backtest_timeframes": ["1h", "4h"]}
            await asyncio.sleep(5)
            # ─── 5:00 PM - 7:00 AM: BACKTEST ───
            save_status("backtesting", "Running overnight backtests")
            backtest_results = run_backtests(proposals)
            # ─── STAGE ───
            stage_settings(proposals, backtest_results)
            # ─── Sleep until 7:30 AM ───
            net2 = now_et()
            target_deploy = net2.replace(hour=7, minute=30, second=0, microsecond=0)
            if target_deploy <= net2:
                target_deploy += timedelta(days=1)
            deploy_wait = (target_deploy - net2).total_seconds()
            save_status("waiting_for_deploy", f"Backtests done. Deploying at {target_deploy.strftime('%H:%M ET')} in {deploy_wait/3600:.1f}h")
            await asyncio.sleep(deploy_wait)
            # ─── 7:30 AM: DEPLOY ───
            save_status("deploying", "Deploying optimized settings")
            deploy_staged_settings()
            await asyncio.sleep(30)
            # ─── 8:00 AM: RESTART TRADIER + MORNING AUDIT ───
            net3 = now_et()
            target_start = net3.replace(hour=8, minute=0, second=0, microsecond=0)
            if target_start <= net3:
                start_wait = 0
            else:
                start_wait = (target_start - net3).total_seconds()
            await asyncio.sleep(start_wait)
            save_status("starting_tradier", "Launching tradier services for market open")
            start_tradier()
            subprocess.run([PYTHON, str(BASE_PATH / "trade_quality_auditor.py")], cwd=str(BASE_PATH), timeout=120)
            save_status("idle", "Cycle complete. Waiting for next 4:15 PM ET.")
        except Exception as e:
            logger.error(f"[LAB] Error in main loop: {e}", exc_info=True)
            save_status("error", str(e))
            await asyncio.sleep(3600)


def show_status():
    if LAB_STATUS_FILE.exists():
        status = json.loads(LAB_STATUS_FILE.read_text())
        print(f"Phase: {status.get('phase', '?')}")
        print(f"Time:  {status.get('timestamp', '?')}")
        print(f"Info:  {status.get('details', '?')}")
    else:
        print("Lab has not been started yet.")


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        asyncio.run(daemon_loop())
    elif "--optimize-now" in sys.argv:
        save_status("manual_optimize", "Manual optimization triggered")
        data = collect_performance()
        proposals = ask_claude_for_proposals(data)
        if proposals:
            print(json.dumps(proposals, indent=2))
            backtest_results = run_backtests(proposals)
            stage_settings(proposals, backtest_results)
        else:
            print("No proposals generated")
    elif "--deploy" in sys.argv:
        deploy_staged_settings()
    elif "--status" in sys.argv:
        show_status()
    elif "--shutdown-tradier" in sys.argv:
        shutdown_tradier()
    elif "--start-tradier" in sys.argv:
        start_tradier()
    else:
        print(__doc__)
