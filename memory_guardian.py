#!/usr/bin/env python3
"""
Memory Guardian — Prevents macOS from running out of memory and crashing.

3-tier escalation to keep Terminal.app (Claude agents) AND browsers alive:
  Tier 1: Throttle local backtests (renice + SIGSTOP) + kill non-essential apps
  Tier 2: Kill local backtests entirely, offload to server 204.168.181.211
  Tier 3: Kill ALL trading scripts + restart (browsers NEVER killed)

Terminal.app and browsers are NEVER killed.

Usage:
    python3 memory_guardian.py              # Run the guardian daemon
    python3 memory_guardian.py --restore    # Restore Claude sessions after reboot
    python3 memory_guardian.py --status     # Show current memory status
    python3 memory_guardian.py --dry-run    # Show what would happen without acting
"""

import subprocess
import json
import os
import sys
import time
import signal
import logging
import re
from pathlib import Path
from datetime import datetime

# --- Configuration ---
CHECK_INTERVAL_SECONDS = 5  # Check every 5s, not 10
# Tier 1: kill non-essential apps (Slack, Discord, etc) when available drops below this %
TIER1_THRESHOLD_PCT = 20.0  # ~7.2GB on 36GB — early warning
# Tier 2: kill browsers + non-essential trading scripts
TIER2_THRESHOLD_PCT = 15.0  # ~5.4GB — getting dangerous
# Tier 3: kill EVERYTHING except Terminal + system essentials
TIER3_THRESHOLD_PCT = 10.0  # ~3.6GB — emergency, save Terminal at all costs
# Also trigger on macOS memory pressure levels
PRESSURE_TIER1 = 2  # warn
PRESSURE_TIER2 = 4  # critical
# Consecutive checks required before acting — FAST response
CONSECUTIVE_CHECKS_TIER1 = 2  # 10s before tier 1
CONSECUTIVE_CHECKS_TIER2 = 1  # 5s before tier 2 (was 2 = 20s, TOO SLOW)
CONSECUTIVE_CHECKS_TIER3 = 1  # IMMEDIATE — 5s is already too long at <10%
# Cooldowns (seconds) — shorter to allow re-escalation
COOLDOWN_TIER1 = 120   # 2 min between non-essential app kills
COOLDOWN_TIER2 = 180   # 3 min between browser kills
COOLDOWN_TIER3 = 120   # 2 min between emergency kills
# Swap threshold — if swap is this high, escalate immediately regardless of %
SWAP_EMERGENCY_GB = 8.0  # 8GB swap = system is dying
# NEVER KILL these — Terminal has Claude agents, browsers must stay open, Finder/system are essential
NEVER_KILL = {"Terminal", "Finder", "loginwindow", "SystemUIServer", "WindowServer", "Dock", "System Events", "Google Chrome", "Microsoft Edge", "Opera", "Firefox", "Safari"}
ANTIGRAVITY_APPS = {"Antigravity", "Antigravity IDE"}
# Background language-server / indexer helpers that balloon unbounded while indexing this huge
# repo (2026-06-03: language_server_macos_arm grew to 5.4GB → jetsam SIGKILLed live ez_manage
# workers at only ~480MB RSS, far below their ceiling). These are NOT the editor UI — they hold
# no unsaved documents and re-spawn on demand, so trimming one frees GBs with zero work lost.
# The aggregate free_pct tiers below never catch this because macOS reports the rest of RAM as
# reclaimable; a single runaway active process is invisible to that metric. Cap it per-process.
RUNAWAY_INDEXER_PATTERNS = ["language_server_macos_arm"]
RUNAWAY_INDEXER_RSS_CAP_MB = 3000  # SIGTERM an indexer above this; it re-spawns small
COOLDOWN_INDEXER = 120  # min seconds between indexer trims
# Trading scripts that run in iTerm2 (matched by process command line)
TRADING_SCRIPT_PATTERNS = ["ez_manage", "ez_positions", "ez_prices", "ez_prices_ws", "ez_klines", "ez_mark_prices", "ez_share_ind", "ez_indicators", "ez_market_data", "ez_indicators_merger", "ez_crosses", "ez_rankings", "ez_news_scanner", "ez_copilot", "ez_backup", "ez_positions_watchdog", "trade_analytics", "pa.py", "tradier_manage", "tradier_positions", "tradier_prices", "tradier_indicators", "tradier_rankings"]
# Backtest scripts — throttle/kill these FIRST before touching anything else
BACKTEST_PATTERNS = ["backtest_v5", "backtest_v4", "backtest_v3", "backtest_ablation", "backtest_sweep", "backtest_marathon", "backtest_continuous", "backtest_deep", "backtest_full", "ablation_backtest", "ablation_v2"]
# Server to offload backtests to when memory is tight (2026-05-28 S2 DEAD → S1)
BACKTEST_SERVER = "s1-int"
BACKTEST_SERVER_PATH = "/home/niels/binance"
# Non-essential apps to kill (browsers are NEVER killed)
KILL_PRIORITY_APPS = ["Ollama", "Surfshark", "WhatsApp", "Slack", "Discord", "Spotify", "FileZilla", "TradingView", "Trade the Future.", "Comet", "Resilio Sync", "Google Drive", "ChatGPT", "ChatGPT Atlas", "Claude"]
# System agents to never touch
SYSTEM_AGENT_KEYWORDS = {"Agent", "UIServer", "Server", "Manager", "Center", "Notification", "Control", "Dispatch", "Spotlight", "XProtect", "liquiddetection", "imagent", "identityservice", "sociallayer", "Keychain", "PressAndHold", "UIKit", "WiFi", "CoreServices", "CoreLocation", "BackgroundTask", "Escrow", "WindowManager", "WallpaperAgent", "UniversalControl", "IMAutomatic", "Software Update", "TextInput", "AirPlay", "AXVisual", "Screen Time", "FolderActions", "ARDAgent", "SSMenuAgent"}
# Paths
WORKDIR = "/Users/niels/Documents/binance"
STATE_FILE = os.path.expanduser("~/.claude/memory_guardian_sessions.json")
LOG_FILE = os.path.expanduser("~/logs/memory_guardian.log")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger("memory_guardian")


def get_memory_stats():
    """Get current memory statistics using vm_stat.

    macOS keeps 'free' near zero by design (aggressive caching).
    Real available = free + inactive + purgeable (what macOS can reclaim without swapping).
    We also track compressor/swap pressure as the real danger signal.
    """
    try:
        result = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")
        page_size = 16384  # macOS ARM default
        stats = {}
        for line in lines[1:]:
            if ":" in line:
                key, val = line.split(":", 1)
                val = val.strip().rstrip(".")
                try:
                    stats[key.strip()] = int(val)
                except ValueError:
                    pass
        free_pages = stats.get("Pages free", 0)
        inactive_pages = stats.get("Pages inactive", 0)
        purgeable_pages = stats.get("Pages purgeable", 0)
        speculative_pages = stats.get("Pages speculative", 0)
        compressor_pages = stats.get("Pages occupied by compressor", 0)
        wired_pages = stats.get("Pages wired down", 0)
        active_pages = stats.get("Pages active", 0)
        swapouts = stats.get("Swapouts", 0)
        total_bytes = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout.strip())
        total_gb = total_bytes / (1024 ** 3)
        # Available = free + inactive + purgeable (macOS can reclaim these without swapping)
        available_pages = free_pages + inactive_pages + purgeable_pages
        available_gb = (available_pages * page_size) / (1024 ** 3)
        free_pct = (available_pages * page_size / total_bytes) * 100
        # "Used" = wired + active + compressor (not reclaimable without killing apps)
        used_pages = wired_pages + active_pages + compressor_pages
        used_gb = (used_pages * page_size) / (1024 ** 3)
        used_pct = (used_pages * page_size / total_bytes) * 100
        # Get swap usage
        swap_gb = 0.0
        try:
            swap_result = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=3)
            for part in swap_result.stdout.split():
                if part.endswith("M"):
                    # "used = 1234.56M" pattern
                    pass
            # Parse "total = X  used = Y  free = Z"
            swap_parts = swap_result.stdout.strip()
            if "used" in swap_parts:
                used_match = re.search(r'used\s*=\s*([\d.]+)([MG])', swap_parts)
                if used_match:
                    val = float(used_match.group(1))
                    swap_gb = val / 1024 if used_match.group(2) == 'M' else val
        except Exception:
            pass
        return {"total_gb": total_gb, "available_gb": available_gb, "free_pct": free_pct, "used_gb": used_gb, "used_pct": used_pct, "compressor_gb": (compressor_pages * page_size) / (1024 ** 3), "swapouts": swapouts, "swap_gb": swap_gb, "free_pages": free_pages, "inactive_pages": inactive_pages, "purgeable_pages": purgeable_pages}
    except Exception as e:
        logger.error(f"Failed to get memory stats: {e}")
        return None


def get_memory_pressure_level():
    """Get macOS memory pressure level (1=normal, 2=warn, 4=critical)."""
    try:
        result = subprocess.run(["memory_pressure", "-S"], capture_output=True, text=True, timeout=10)
        output = result.stdout.lower()
        if "critical" in output:
            return 4
        elif "warn" in output:
            return 2
        return 1
    except Exception:
        return 1


def get_trading_script_memory():
    """Get memory usage of each trading script, sorted by RSS descending. Returns list of (pid, rss_mb, cmdline)."""
    try:
        result = subprocess.run(["ps", "-eo", "pid,rss,command"], capture_output=True, text=True, timeout=10)
        scripts = []
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid, rss_kb, cmd = int(parts[0]), int(parts[1]), parts[2]
            if pid == os.getpid():
                continue
            if "memory_guardian" in cmd:
                continue
            # Match trading scripts
            for pattern in TRADING_SCRIPT_PATTERNS:
                if pattern in cmd and ("python" in cmd or "bash" in cmd):
                    rss_mb = rss_kb / 1024
                    scripts.append((pid, rss_mb, cmd))
                    break
        scripts.sort(key=lambda x: x[1], reverse=True)
        return scripts
    except Exception as e:
        logger.error(f"Failed to get trading script memory: {e}")
        return []


def extract_script_name(cmdline):
    """Extract the script filename from a command line like '/opt/.../python -u ez_manage.py --accounts ang'."""
    match = re.search(r'((?:ez_|tradier_)\w+\.py|pa\.py|trade_analytics\.py)', cmdline)
    return match.group(1) if match else None


def restart_script(pid, cmdline):
    """Kill a script by PID — the watchdog wrapper will auto-restart it."""
    script_name = extract_script_name(cmdline) or f"PID {pid}"
    logger.warning(f"TIER 1: Killing {script_name} (PID {pid}, will auto-restart via watchdog)")
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        # Check if it died
        try:
            os.kill(pid, 0)
            # Still alive, force kill
            os.kill(pid, signal.SIGKILL)
            logger.warning(f"Force-killed {script_name} (PID {pid})")
        except OSError:
            logger.info(f"{script_name} terminated cleanly")
    except OSError as e:
        logger.error(f"Failed to kill {script_name} (PID {pid}): {e}")
    notify(f"Restarted {script_name} (high memory)", "Tier 1 — Script Restart")


def get_running_apps():
    """Get list of running GUI apps."""
    try:
        result = subprocess.run(["ps", "-eo", "comm"], capture_output=True, text=True, timeout=5)
        seen = set()
        apps = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if "/Applications/" in line or ".app/" in line:
                parts = line.split(".app/")
                if parts:
                    app_name = parts[0].split("/")[-1]
                    if app_name and app_name not in seen:
                        seen.add(app_name)
                        apps.append(app_name)
        return apps
    except Exception as e:
        logger.error(f"Failed to get running apps: {e}")
        return []


def kill_app(app_name):
    """Gracefully quit an app, force-kill if it doesn't respond."""
    try:
        subprocess.run(["osascript", "-e", f'tell application "{app_name}" to quit'], capture_output=True, text=True, timeout=8)
        logger.info(f"Sent quit to {app_name}")
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["pkill", "-f", app_name], capture_output=True, timeout=5)
            logger.warning(f"Force-killed {app_name}")
        except Exception: pass
    except Exception as e: logger.error(f"Failed to quit {app_name}: {e}")

def ask_permission_to_kill(app_name, timeout=15):
    applescript = f'display dialog "Memory is critically low. Can Memory Guardian close {app_name} to prevent system crash?" buttons {{"No", "Yes"}} default button "No" with icon caution giving up after {timeout}'
    try:
        result = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True, timeout=timeout + 5)
        if "button returned:Yes" in result.stdout: return True
    except Exception as e: logger.error(f"Failed to show confirmation dialog for {app_name}: {e}")
    return False

def notify(message, title="Memory Guardian"):
    """Send macOS notification."""
    try:
        subprocess.run(["osascript", "-e", f'display notification "{message}" with title "{title}" sound name "Glass"'], capture_output=True, timeout=5)
    except Exception:
        pass


def get_active_claude_sessions():
    """Find active Claude Code sessions with live processes."""
    sessions = []
    sessions_dir = os.path.expanduser("~/.claude/sessions/")
    if not os.path.exists(sessions_dir):
        return sessions
    try:
        result = subprocess.run(["pgrep", "-af", "claude"], capture_output=True, text=True, timeout=5)
        claude_pids = set()
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.strip().split(None, 1)
            if len(parts) >= 2 and ("claude" in parts[1].lower() and "memory_guardian" not in parts[1]):
                claude_pids.add(int(parts[0]))
    except Exception:
        claude_pids = set()
    for fname in os.listdir(sessions_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(sessions_dir, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
            pid = data.get("pid")
            session_id = data.get("sessionId")
            cwd = data.get("cwd", "")
            if pid and session_id:
                try:
                    os.kill(pid, 0)
                except OSError:
                    continue
                if claude_pids and pid not in claude_pids:
                    continue
                sessions.append({"pid": pid, "sessionId": session_id, "cwd": cwd, "startedAt": data.get("startedAt"), "name": data.get("name", "")})
        except Exception:
            pass
    return sessions


def save_session_state(sessions):
    """Save active Claude sessions for post-reboot restore."""
    state = {"saved_at": datetime.utcnow().isoformat(), "sessions": sessions}
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    logger.info(f"Saved {len(sessions)} Claude sessions to {STATE_FILE}")
    # Also rsync the JSONL files to local backup (fast, hardlinked)
    try:
        subprocess.run(["/bin/bash", os.path.join(WORKDIR, "backup_claude_sessions.sh")], capture_output=True, timeout=60)
        logger.info("Backed up JSONL session files to local disk")
    except Exception as e:
        logger.error(f"Failed to backup JSONL files: {e}")


def get_backtest_processes():
    """Get running backtest processes, sorted by RSS descending. Returns list of (pid, rss_mb, cmdline)."""
    try:
        result = subprocess.run(["ps", "-eo", "pid,rss,command"], capture_output=True, text=True, timeout=10)
        procs = []
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid, rss_kb, cmd = int(parts[0]), int(parts[1]), parts[2]
            if pid == os.getpid():
                continue
            for pattern in BACKTEST_PATTERNS:
                if pattern in cmd and "python" in cmd:
                    procs.append((pid, rss_kb / 1024, cmd))
                    break
        procs.sort(key=lambda x: x[1], reverse=True)
        return procs
    except Exception as e:
        logger.error(f"Failed to get backtest processes: {e}")
        return []


def throttle_backtests(dry_run=False):
    """Throttle local backtests: renice to lowest priority + SIGSTOP to pause them."""
    procs = get_backtest_processes()
    if not procs:
        return False
    throttled = []
    for pid, rss_mb, cmd in procs:
        script_name = extract_script_name(cmd) or cmd[:60]
        if dry_run:
            print(f"[DRY RUN] Would throttle {script_name} (PID {pid}, {rss_mb:.0f}MB) — renice +20 + SIGSTOP")
            throttled.append(script_name)
            continue
        try:
            subprocess.run(["renice", "+20", "-p", str(pid)], capture_output=True, timeout=5)
            os.kill(pid, signal.SIGSTOP)
            logger.warning(f"Throttled backtest {script_name} (PID {pid}, {rss_mb:.0f}MB) — paused with SIGSTOP")
            throttled.append(script_name)
        except Exception as e:
            logger.error(f"Failed to throttle {script_name} (PID {pid}): {e}")
    if throttled:
        notify(f"Paused {len(throttled)} backtests to free memory", "Tier 1 — Backtest Throttle")
    return len(throttled) > 0


def resume_backtests():
    """Resume any SIGSTOP'd backtest processes."""
    procs = get_backtest_processes()
    resumed = 0
    for pid, rss_mb, cmd in procs:
        try:
            os.kill(pid, signal.SIGCONT)
            subprocess.run(["renice", "0", "-p", str(pid)], capture_output=True, timeout=5)
            resumed += 1
        except Exception:
            pass
    if resumed:
        logger.info(f"Resumed {resumed} paused backtests")
    return resumed


def kill_local_backtests(dry_run=False):
    """Kill all local backtest processes to free memory."""
    procs = get_backtest_processes()
    if not procs:
        return False
    killed = []
    for pid, rss_mb, cmd in procs:
        script_name = extract_script_name(cmd) or cmd[:60]
        if dry_run:
            print(f"[DRY RUN] Would kill backtest {script_name} (PID {pid}, {rss_mb:.0f}MB)")
            killed.append(script_name)
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            logger.warning(f"Killed backtest {script_name} (PID {pid}, {rss_mb:.0f}MB)")
            killed.append(script_name)
        except Exception as e:
            logger.error(f"Failed to kill backtest {script_name} (PID {pid}): {e}")
    if killed:
        notify(f"Killed {len(killed)} local backtests — use server instead", "Tier 2 — Backtest Offload")
        logger.warning(f"Backtests killed. Offload to: ssh {BACKTEST_SERVER} 'cd {BACKTEST_SERVER_PATH} && python3 <script>'")
    return len(killed) > 0


def tier1_restart_hog(dry_run=False):
    """Tier 1: Find the top memory-abusing trading script and restart it (watchdog auto-relaunches).

    Live trading workers (ez_manage, tradier_manage) are EXCLUDED — their watchdog
    (run_with_watchdog.sh) already does preemptive RSS recycle at 1.5GB MAX_RSS_KB.
    Pre-2026-05-02 this guardian killed ez_manage workers at 600–900MB RSS (well below
    the watchdog ceiling), causing a kill→restart→reallocate→kill cycle that produced
    5 OOM events in 30min on 2026-05-02. Skip them; let the watchdog own RSS recycling.
    """
    NEVER_RESTART_VIA_TIER1 = ("ez_manage.py", "tradier_manage.py")
    scripts = [s for s in get_trading_script_memory() if not any(p in s[2] for p in NEVER_RESTART_VIA_TIER1)]
    if not scripts:
        logger.info("TIER 1: No restart-eligible trading scripts found (live workers excluded)")
        return False
    top_pid, top_mb, top_cmd = scripts[0]
    script_name = extract_script_name(top_cmd) or "unknown"
    logger.warning(f"TIER 1: Top memory hog (eligible): {script_name} using {top_mb:.0f}MB (PID {top_pid})")
    if top_mb < 200:
        logger.info(f"TIER 1: Top eligible script only using {top_mb:.0f}MB — not worth restarting")
        return False
    if dry_run:
        print(f"[DRY RUN] Would restart {script_name} (PID {top_pid}, {top_mb:.0f}MB)")
        return True
    restart_script(top_pid, top_cmd)
    return True


def tier2_restart_all_trading(dry_run=False):
    """Tier 2: Kill ALL ez_/tradier_ scripts, quit iTerm2, restart via start_everything commands."""
    logger.warning("=== TIER 2: FULL TRADING RESTART ===")
    # Save Claude sessions first (they're in Terminal, not iTerm)
    sessions = get_active_claude_sessions()
    if sessions:
        save_session_state(sessions)
        logger.info(f"Saved {len(sessions)} Claude sessions as precaution")
    if dry_run:
        print("[DRY RUN] Would kill all ez_/tradier_ scripts and restart iTerm2")
        return True
    # Kill all trading python processes
    logger.info("Killing all ez_ and tradier_ python processes...")
    subprocess.run(["pkill", "-9", "-f", "python.*ez_"], capture_output=True, timeout=10)
    subprocess.run(["pkill", "-9", "-f", "python.*tradier_"], capture_output=True, timeout=10)
    subprocess.run(["pkill", "-9", "-f", "python.*pa.py"], capture_output=True, timeout=10)
    subprocess.run(["pkill", "-9", "-f", "python.*trade_analytics"], capture_output=True, timeout=10)
    subprocess.run(["pkill", "-9", "-f", "bash.*run_with_watchdog"], capture_output=True, timeout=10)
    subprocess.run(["pkill", "-9", "-f", "bash.*rsync_market_data"], capture_output=True, timeout=10)
    subprocess.run(["pkill", "-9", "-f", "bash.*rsync_from_gateway"], capture_output=True, timeout=10)
    time.sleep(3)
    # Quit iTerm2
    logger.info("Quitting iTerm2...")
    kill_app("iTerm2")
    time.sleep(5)
    # Wait for memory to settle
    stats = get_memory_stats()
    if stats:
        logger.info(f"Memory after cleanup: {stats['available_gb']:.1f}GB ({stats['free_pct']:.1f}%)")
    # Restart via start_everything commands
    logger.info("Restarting trading scripts via start_everything_1.command and start_everything_3.command...")
    se1 = os.path.join(WORKDIR, "start_everything_1.command")
    se3 = os.path.join(WORKDIR, "start_everything_3.command")
    if os.path.exists(se1):
        subprocess.Popen(["bash", se1], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Launched start_everything_1.command")
    time.sleep(5)
    if os.path.exists(se3):
        subprocess.Popen(["bash", se3], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Launched start_everything_3.command")
    notify("Killed all trading scripts, restarting iTerm2", "Tier 2 — Full Restart")
    return True


def tier3_emergency_cleanup(dry_run=False):
    """Tier 3: Kill non-essential apps + all backtests + trading restart. Browsers NEVER killed."""
    logger.warning("=== TIER 3: EMERGENCY CLEANUP (browsers protected) ===")
    if dry_run:
        running_apps = get_running_apps()
        print(f"[DRY RUN] Would kill priority apps: {[a for a in KILL_PRIORITY_APPS if a in running_apps]}")
        print(f"[DRY RUN] Would kill all backtests + restart trading")
        print(f"[DRY RUN] Browsers are PROTECTED — will NOT be killed")
        return True
    running_apps = get_running_apps()
    killed = []
    for app in KILL_PRIORITY_APPS:
        if app in running_apps:
            kill_app(app)
            killed.append(app)
            time.sleep(0.5)
    for app in running_apps:
        if app in NEVER_KILL or app in ANTIGRAVITY_APPS or app in set(killed) or app == "iTerm2" or app == "iTerm": continue
        if any(keyword in app for keyword in SYSTEM_AGENT_KEYWORDS): continue
        kill_app(app)
        killed.append(app)
        time.sleep(0.3)
    kill_local_backtests()
    logger.info(f"TIER 3: Killed {len(killed)} non-essential apps (browsers preserved): {killed}")
    notify(f"Killed {len(killed)} apps + backtests (browsers safe)", "Tier 3 — Emergency")
    stats = get_memory_stats()
    if stats and stats["free_pct"] < TIER3_THRESHOLD_PCT:
        for app in ANTIGRAVITY_APPS:
            if app in running_apps:
                if ask_permission_to_kill(app):
                    kill_app(app)
                    logger.warning(f"User approved killing {app} due to critical memory")
                    notify(f"Killed {app} (user confirmed)", "Emergency Cleanup")
                else: logger.info(f"User declined (or timed out) killing {app}. Keeping it running.")
    return True


def session_has_conversation(session_id):
    """Check if a session has actual conversation data."""
    projects_dir = os.path.expanduser("~/.claude/projects/")
    if not os.path.exists(projects_dir):
        return False
    for project in os.listdir(projects_dir):
        jsonl_path = os.path.join(projects_dir, project, f"{session_id}.jsonl")
        if os.path.exists(jsonl_path) and os.path.getsize(jsonl_path) >= 500:
            return True
    return False


def restore_sessions():
    """Restore Claude Code sessions after reboot by opening Terminal tabs."""
    if not os.path.exists(STATE_FILE):
        print("No saved sessions found.")
        return
    with open(STATE_FILE) as f:
        state = json.load(f)
    sessions = state.get("sessions", [])
    if not sessions:
        print("No sessions to restore.")
        return
    saved_at = state.get("saved_at", "unknown")
    print(f"Found {len(sessions)} saved sessions from {saved_at}")
    restorable = []
    for session in sessions:
        sid = session["sessionId"]
        if session_has_conversation(sid):
            restorable.append(session)
        else:
            print(f"  Skipping {sid[:12]}... — empty/closed session")
    if not restorable:
        print("All saved sessions were already closed. Nothing to restore.")
        os.rename(STATE_FILE, STATE_FILE + f".restored_{datetime.utcnow().strftime('%Y%m%d%H%M')}")
        return
    print(f"Restoring {len(restorable)} of {len(sessions)} sessions")
    for i, session in enumerate(restorable):
        sid = session["sessionId"]
        cwd = session.get("cwd", os.path.expanduser("~"))
        name = session.get("name", "")
        resume_flag = f"--resume {sid}"
        label = f"{name} — {sid[:12]}..." if name else f"{sid[:12]}..."
        print(f"  [{i+1}] {label} ({cwd})")
        applescript = f'''
        tell application "Terminal"
            activate
            if (count of windows) = 0 then
                do script "cd {cwd} && claude {resume_flag}"
            else
                tell application "System Events" to keystroke "t" using command down
                delay 0.5
                do script "cd {cwd} && claude {resume_flag}" in front window
            end if
        end tell
        '''
        try:
            subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True, timeout=10)
            time.sleep(1.5)
        except Exception as e:
            print(f"  FAILED: {e}")
    backup = STATE_FILE + f".restored_{datetime.utcnow().strftime('%Y%m%d%H%M')}"
    os.rename(STATE_FILE, backup)
    print(f"\nDone! Restored {len(restorable)} sessions.")


def show_status():
    """Show current memory status and tier thresholds."""
    stats = get_memory_stats()
    if not stats:
        print("Failed to get memory stats")
        return
    pressure = get_memory_pressure_level()
    pressure_label = {1: "NORMAL", 2: "WARNING", 4: "CRITICAL"}.get(pressure, "UNKNOWN")
    print(f"=== Memory Guardian Status ===")
    print(f"Total RAM:        {stats['total_gb']:.1f} GB")
    print(f"Used (non-reclaimable): {stats['used_gb']:.1f} GB ({stats['used_pct']:.1f}%)")
    print(f"Available (reclaimable): {stats['available_gb']:.1f} GB ({stats['free_pct']:.1f}%)")
    print(f"Compressor:       {stats['compressor_gb']:.1f} GB")
    print(f"Swap outs:        {stats['swapouts']}")
    print(f"Memory Pressure:  {pressure_label}")
    print()
    print(f"Tier 1 threshold: {TIER1_THRESHOLD_PCT}% — throttle backtests + kill non-essential apps")
    print(f"Tier 2 threshold: {TIER2_THRESHOLD_PCT}% — kill backtests, offload to server")
    print(f"Tier 3 threshold: {TIER3_THRESHOLD_PCT}% — kill non-essential apps + restart trading (browsers SAFE)")
    t1 = "TRIGGERED" if stats["free_pct"] < TIER1_THRESHOLD_PCT else "OK"
    t2 = "TRIGGERED" if stats["free_pct"] < TIER2_THRESHOLD_PCT else "OK"
    t3 = "TRIGGERED" if stats["free_pct"] < TIER3_THRESHOLD_PCT else "OK"
    print(f"Current:          Tier1={t1}  Tier2={t2}  Tier3={t3}")
    print()
    # Backtest processes
    backtests = get_backtest_processes()
    if backtests:
        print(f"Backtest processes ({len(backtests)} running):")
        for pid, mb, cmd in backtests:
            name = extract_script_name(cmd) or cmd[:60]
            print(f"  {mb:7.0f} MB  PID {pid:>6}  {name}")
    else:
        print("No local backtest processes running")
    print(f"Backtest server: ssh {BACKTEST_SERVER}")
    print()
    # Trading script memory
    scripts = get_trading_script_memory()
    if scripts:
        print(f"Trading scripts by memory ({len(scripts)} running):")
        for pid, mb, cmd in scripts[:15]:
            name = extract_script_name(cmd) or cmd[:60]
            print(f"  {mb:7.0f} MB  PID {pid:>6}  {name}")
    # Claude sessions
    sessions = get_active_claude_sessions()
    print(f"\nActive Claude sessions: {len(sessions)}")
    for s in sessions:
        print(f"  {s['sessionId'][:12]}... pid={s['pid']} cwd={s['cwd']}")
    # Running apps
    apps = get_running_apps()
    print(f"\nRunning GUI apps ({len(apps)}):")
    for app in sorted(apps):
        protected = " [NEVER KILL]" if app in NEVER_KILL else ""
        print(f"  {app}{protected}")


def trim_runaway_indexer(dry_run=False):
    """SIGTERM any background language-server/indexer process whose RSS exceeds the cap.
    Returns the number trimmed. Independent of the free_pct tiers — a single runaway active
    process does not move the aggregate metric but still triggers jetsam against live workers."""
    trimmed = 0
    try:
        result = subprocess.run(["ps", "-eo", "pid,rss,command"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid, rss_kb, cmd = int(parts[0]), int(parts[1]), parts[2]
            if not any(pattern in cmd for pattern in RUNAWAY_INDEXER_PATTERNS):
                continue
            rss_mb = rss_kb / 1024
            if rss_mb < RUNAWAY_INDEXER_RSS_CAP_MB:
                continue
            if dry_run:
                print(f"[DRY RUN] Would trim runaway indexer PID {pid} ({rss_mb:.0f}MB > {RUNAWAY_INDEXER_RSS_CAP_MB}MB cap)")
                trimmed += 1
                continue
            logger.warning(f"RUNAWAY INDEXER: PID {pid} at {rss_mb:.0f}MB > {RUNAWAY_INDEXER_RSS_CAP_MB}MB cap — SIGTERM (re-spawns small, no editor work lost)")
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(2)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
                trimmed += 1
            except OSError as e:
                logger.error(f"Failed to trim indexer PID {pid}: {e}")
        if trimmed and not dry_run:
            notify(f"Trimmed {trimmed} runaway indexer(s) >{RUNAWAY_INDEXER_RSS_CAP_MB}MB to protect live trading", "Indexer RSS Cap")
    except Exception as e:
        logger.error(f"trim_runaway_indexer failed: {e}")
    return trimmed


def run_daemon():
    """Main daemon loop with 3-tier escalation. Protects Terminal + browsers at all costs."""
    logger.info(f"Memory Guardian started. Tiers: {TIER1_THRESHOLD_PCT}%/{TIER2_THRESHOLD_PCT}%/{TIER3_THRESHOLD_PCT}% | Swap emergency: {SWAP_EMERGENCY_GB}GB")
    logger.info(f"NEVER KILL: {NEVER_KILL}")
    logger.info(f"Backtest offload server: {BACKTEST_SERVER}")
    consecutive_low = 0
    last_tier1_time = 0
    last_tier2_time = 0
    last_tier3_time = 0
    last_indexer_time = 0
    backtests_paused = False
    while True:
        try:
            stats = get_memory_stats()
            if not stats:
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue
            now_idx = time.time()
            if (now_idx - last_indexer_time) >= COOLDOWN_INDEXER and trim_runaway_indexer():
                last_indexer_time = now_idx
            pressure = get_memory_pressure_level()
            free_pct = stats["free_pct"]
            swap_gb = stats.get("swap_gb", 0)
            now = time.time()
            # Periodic logging (~5 min)
            if int(now) % 300 < CHECK_INTERVAL_SECONDS:
                logger.info(f"Memory: {stats['available_gb']:.1f}GB available ({free_pct:.1f}%), used={stats['used_gb']:.1f}GB ({stats['used_pct']:.0f}%), swap={swap_gb:.1f}GB, pressure={'CRIT' if pressure >= 4 else 'WARN' if pressure >= 2 else 'OK'}")
            # Swap emergency — macOS never releases swap once allocated, so a bare absolute
            # threshold latches permanently and thrashes TIER2/3 while RAM is healthy.
            # Require corroboration from real scarcity (free% or pressure) before escalating.
            swap_emergency = swap_gb >= SWAP_EMERGENCY_GB and (free_pct < TIER1_THRESHOLD_PCT or pressure >= PRESSURE_TIER1)
            if swap_emergency:
                logger.warning(f"SWAP EMERGENCY: {swap_gb:.1f}GB swap in use! Escalating immediately.")
            # Check thresholds
            if free_pct < TIER1_THRESHOLD_PCT or pressure >= PRESSURE_TIER1 or swap_emergency:
                consecutive_low += 1
                logger.warning(f"LOW MEMORY: {stats['available_gb']:.1f}GB ({free_pct:.1f}%), swap={swap_gb:.1f}GB, pressure={pressure}, count={consecutive_low}")
                # TIER 3: EMERGENCY — kill non-essential apps + backtests + restart trading (browsers SAFE)
                if (free_pct < TIER3_THRESHOLD_PCT or pressure >= PRESSURE_TIER2 or swap_emergency) and consecutive_low >= CONSECUTIVE_CHECKS_TIER3 and (now - last_tier3_time) >= COOLDOWN_TIER3:
                    sessions = get_active_claude_sessions()
                    if sessions:
                        save_session_state(sessions)
                    tier3_emergency_cleanup()
                    last_tier3_time = now
                    backtests_paused = False
                    time.sleep(10)
                    recheck = get_memory_stats()
                    if recheck and recheck["free_pct"] < TIER3_THRESHOLD_PCT:
                        logger.warning(f"STILL CRITICAL after tier 3: {recheck['available_gb']:.1f}GB — restarting all trading scripts")
                        tier2_restart_all_trading()
                        last_tier2_time = now
                    consecutive_low = 0
                    time.sleep(15)
                    continue
                # TIER 2: Kill local backtests + non-essential apps (browsers SAFE)
                if (free_pct < TIER2_THRESHOLD_PCT or (swap_gb > SWAP_EMERGENCY_GB / 2 and pressure >= PRESSURE_TIER1)) and consecutive_low >= CONSECUTIVE_CHECKS_TIER2 and (now - last_tier2_time) >= COOLDOWN_TIER2:
                    sessions = get_active_claude_sessions()
                    if sessions:
                        save_session_state(sessions)
                    # Kill non-essential apps (NOT browsers)
                    running_apps = get_running_apps()
                    killed = []
                    for app in KILL_PRIORITY_APPS:
                        if app in running_apps:
                            kill_app(app)
                            killed.append(app)
                    if killed:
                        logger.warning(f"TIER 2: Killed {len(killed)} non-essential apps: {killed}")
                    # Kill all local backtests — they should run on server instead
                    kill_local_backtests()
                    backtests_paused = False
                    last_tier2_time = now
                    time.sleep(8)
                    recheck = get_memory_stats()
                    if recheck and recheck["free_pct"] < TIER2_THRESHOLD_PCT:
                        logger.warning(f"STILL LOW after tier 2: {recheck['available_gb']:.1f}GB — restarting trading")
                        tier2_restart_all_trading()
                    consecutive_low = 0
                    time.sleep(15)
                    continue
                # TIER 1: Throttle backtests (pause with SIGSTOP) + kill non-essential apps + restart top hog
                if consecutive_low >= CONSECUTIVE_CHECKS_TIER1 and (now - last_tier1_time) >= COOLDOWN_TIER1:
                    # First: pause any running backtests
                    if throttle_backtests():
                        backtests_paused = True
                    # Kill non-essential apps
                    running_apps = get_running_apps()
                    for app in KILL_PRIORITY_APPS:
                        if app in running_apps:
                            kill_app(app)
                            logger.info(f"TIER 1: Killed non-essential {app}")
                    # Restart top memory hog trading script
                    if tier1_restart_hog():
                        last_tier1_time = now
                    consecutive_low = 0
                    time.sleep(10)
                    continue
            else:
                if consecutive_low > 0:
                    logger.info(f"Memory recovered: {stats['available_gb']:.1f}GB ({free_pct:.1f}%), swap={swap_gb:.1f}GB")
                # Resume paused backtests when memory is healthy
                if backtests_paused and free_pct >= TIER1_THRESHOLD_PCT + 5:
                    resumed = resume_backtests()
                    if resumed:
                        logger.info(f"Memory healthy ({free_pct:.1f}%) — resumed {resumed} paused backtests")
                        backtests_paused = False
                consecutive_low = 0
            time.sleep(CHECK_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("Memory Guardian stopped by user")
            if backtests_paused:
                resume_backtests()
            break
        except Exception as e:
            logger.error(f"Guardian loop error: {e}")
            time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    if "--restore" in sys.argv:
        restore_sessions()
    elif "--status" in sys.argv:
        show_status()
    elif "--dry-run" in sys.argv:
        stats = get_memory_stats()
        if stats:
            print(f"Memory: {stats['available_gb']:.1f}GB available ({stats['free_pct']:.1f}%)")
        print("\n--- Backtest processes ---")
        backtests = get_backtest_processes()
        if backtests:
            for pid, mb, cmd in backtests:
                print(f"  {mb:.0f}MB  PID {pid}  {extract_script_name(cmd) or cmd[:60]}")
        else:
            print("  No local backtests running")
        print(f"\n--- Runaway indexer cap (>{RUNAWAY_INDEXER_RSS_CAP_MB}MB) ---")
        if trim_runaway_indexer(dry_run=True) == 0:
            print("  No runaway indexer above cap")
        print(f"\n--- Tier 1: Throttle backtests + kill non-essential apps ---")
        throttle_backtests(dry_run=True)
        tier1_restart_hog(dry_run=True)
        print(f"\n--- Tier 2: Kill backtests + offload to {BACKTEST_SERVER} ---")
        kill_local_backtests(dry_run=True)
        print(f"\n--- Tier 3: Emergency cleanup (browsers SAFE) ---")
        tier3_emergency_cleanup(dry_run=True)
        print(f"\n--- Full trading restart ---")
        tier2_restart_all_trading(dry_run=True)
    else:
        run_daemon()
