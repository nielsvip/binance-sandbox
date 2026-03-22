#!/usr/bin/env python3
"""ez_launcher.py — Single-process launcher for all local trading services.
Launches each script ONCE as a background daemon. No iTerm tabs. No focus stealing.
Checks for existing processes before launching. Provides live status dashboard.

Usage:
  python ez_launcher.py start          # Launch all services (skips already running)
  python ez_launcher.py stop           # Stop all services
  python ez_launcher.py restart        # Stop then start
  python ez_launcher.py status         # Show what's running (live dashboard)
  python ez_launcher.py status --once  # Show status once and exit
"""
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

WORKDIR = Path.home() / "Documents" / "binance"
PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
LOGDIR = Path.home() / "logs"
LAUNCHER_LOG = LOGDIR / "ez_launcher.log"

# ===== PROCESS DEFINITIONS =====
# Each entry: (script, args, category)
# Categories: infra (prices/data), indicators, trading, stocks, bridge
PROCESSES = [
    # Infrastructure (launch first, order matters)
    ("ez_prices_ws.py", [], "infra"),
    ("ez_prices.py", [], "infra"),
    ("ez_klines.py", [], "infra"),
    ("ez_mark_prices.py", [], "infra"),
    # Indicators
    ("ez_share_ind.py", [], "indicators"),
    ("ez_indicators.py", [], "indicators"),
    ("ez_market_data.py", [], "indicators"),
    ("ez_crosses.py", [], "indicators"),
    ("ez_rankings.py", [], "indicators"),
    ("ez_news_scanner.py", [], "indicators"),
    # Trading (crypto)
    ("ez_manage.py", ["ang"], "trading"),
    ("ez_manage.py", ["inf"], "trading"),
    ("ez_manage.py", ["men"], "trading"),
    ("ez_manage.py", ["fin"], "trading"),
    ("ez_manage.py", ["flz"], "trading"),
    ("ez_positions_quick.py", ["--account", "ang"], "trading"),
    ("ez_positions.py", ["--account", "ang"], "trading"),
    ("ez_positions.py", ["--account", "inf"], "trading"),
    ("ez_loss_mitigator.py", [], "trading"),
    # Stocks
    ("tradier_prices.py", [], "stocks"),
    ("tradier_indicators.py", [], "stocks"),
    ("tradier_rankings.py", [], "stocks"),
    ("tradier_manage.py", ["--accounts", "trb"], "stocks"),
    ("tradier_manage.py", ["--accounts", "trc"], "stocks"),
    # Bridges / rsync (bash scripts)
    ("rsync_market_data_to_server_continuous.sh", [], "bridge"),
]


def log(msg):
    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    line = f"{ts} | {msg}"
    print(line, flush=True)
    try:
        LOGDIR.mkdir(parents=True, exist_ok=True)
        with open(LAUNCHER_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def proc_key(script, args):
    """Unique key for a process (script + account name)."""
    # Extract the meaningful arg (account name, not flags)
    for a in reversed(args):
        if not a.startswith("-"):
            return f"{script}_{a}"
    return script


def find_running(script, args):
    """Find PIDs of a specific script+args combo. Returns list of (pid, cpu, mem, elapsed)."""
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
        matches = []
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            cmd = parts[10]
            pid = int(parts[1])
            cpu = float(parts[2])
            mem = float(parts[3])
            # Match script name
            if script not in cmd:
                continue
            if "python" not in cmd and not script.endswith(".sh"):
                continue
            # Match args (if any) — use word boundary to avoid "men" matching "environment"
            if args:
                meaningful_arg = args[-1]
                # Check the arg appears as a standalone word (not substring)
                cmd_parts = cmd.split()
                if meaningful_arg not in cmd_parts:
                    continue
            matches.append((pid, cpu, mem))
        return matches
    except Exception:
        return []


def is_running(script, args):
    """Check if exactly this script+args is already running."""
    return len(find_running(script, args)) > 0


def launch_one(script, args, category):
    """Launch a single process in its own iTerm tab. Tab auto-closes when process dies.
    Returns True if launched, False if already running."""
    key = proc_key(script, args)
    existing = find_running(script, args)
    if existing:
        log(f"  SKIP {key} (already running, PID {existing[0][0]})")
        return False
    script_path = WORKDIR / script
    if not script_path.exists():
        log(f"  MISS {key} (file not found)")
        return False
    # Build the command that runs in the iTerm tab
    if script.endswith(".sh"):
        run_cmd = f"cd '{WORKDIR}' && bash '{script_path}' {' '.join(args)}"
    else:
        run_cmd = f"cd '{WORKDIR}' && {PYTHON} -u '{script_path}' {' '.join(args)}"
    # The ; exit makes the tab close when the process dies — no empty zombies
    full_cmd = f"{run_cmd}; exit"
    # Tab title: short readable name
    tab_title = key.replace('.py', '').replace('.sh', '')
    try:
        applescript = f'''
tell application "iTerm2"
    tell first window
        create tab with default profile
        tell current session of current tab
            set name to "{tab_title}"
            write text "{full_cmd}"
        end tell
    end tell
end tell'''
        subprocess.run(["osascript", "-e", applescript], timeout=5, capture_output=True)
        log(f"  START {key}")
        return True
    except Exception as e:
        # Fallback: background daemon if iTerm not available
        log(f"  iTerm fail for {key}, launching background: {e}")
        logfile = LOGDIR / f"{script.replace('.py', '').replace('.sh', '')}{'_' + args[-1] if args else ''}.log"
        try:
            if script.endswith(".sh"):
                cmd = ["bash", str(script_path)] + args
            else:
                cmd = [PYTHON, "-u", str(script_path)] + args
            with open(logfile, "a") as lf:
                subprocess.Popen(cmd, cwd=str(WORKDIR), stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
            log(f"  START {key} (background)")
            return True
        except Exception as e2:
            log(f"  FAIL {key}: {e2}")
            return False


def start_all(categories=None):
    """Launch all processes (skipping already running ones)."""
    log("===== STARTING SERVICES =====")
    launched, skipped = 0, 0
    prev_cat = None
    for script, args, cat in PROCESSES:
        if categories and cat not in categories:
            continue
        if cat != prev_cat:
            if prev_cat == "infra":
                log("  Waiting 3s for infra to initialize...")
                time.sleep(3)
            elif prev_cat == "indicators":
                log("  Waiting 2s for indicators...")
                time.sleep(2)
            prev_cat = cat
        if launch_one(script, args, cat):
            launched += 1
            time.sleep(0.3)  # Small gap between launches
        else:
            skipped += 1
    log(f"===== DONE: {launched} launched, {skipped} skipped =====")


def stop_all():
    """Stop all trading processes."""
    log("===== STOPPING ALL SERVICES =====")
    killed = 0
    for script, args, cat in PROCESSES:
        pids = find_running(script, args)
        for pid, cpu, mem in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
    if killed:
        time.sleep(3)
        # SIGKILL survivors
        for script, args, cat in PROCESSES:
            pids = find_running(script, args)
            for pid, cpu, mem in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    log(f"===== STOPPED {killed} processes =====")


def show_status(once=False):
    """Show live dashboard of all processes."""
    try:
        while True:
            lines = []
            lines.append(f"\033[2J\033[H")  # Clear screen
            lines.append(f"{'='*72}")
            lines.append(f"  EZ TRADING SYSTEM STATUS — {datetime.now().strftime('%H:%M:%S')}")
            lines.append(f"{'='*72}")
            prev_cat = None
            total_cpu, total_mem, running, dead = 0, 0, 0, 0
            for script, args, cat in PROCESSES:
                if cat != prev_cat:
                    cat_label = {"infra": "INFRASTRUCTURE", "indicators": "INDICATORS", "trading": "CRYPTO TRADING", "stocks": "STOCK TRADING", "bridge": "BRIDGES/RSYNC"}
                    lines.append(f"\n  \033[1m{cat_label.get(cat, cat.upper())}\033[0m")
                    prev_cat = cat
                key = proc_key(script, args)
                pids = find_running(script, args)
                if pids:
                    pid, cpu, mem = pids[0]
                    total_cpu += cpu
                    total_mem += mem
                    running += 1
                    if len(pids) > 1:
                        status = f"\033[33m⚠ DUPES({len(pids)})\033[0m PID {pid} CPU={cpu:.0f}% MEM={mem:.1f}%"
                    elif cpu > 80:
                        status = f"\033[33m● HOT\033[0m      PID {pid} CPU={cpu:.0f}% MEM={mem:.1f}%"
                    else:
                        status = f"\033[32m● RUNNING\033[0m  PID {pid} CPU={cpu:.0f}% MEM={mem:.1f}%"
                else:
                    dead += 1
                    status = f"\033[31m○ STOPPED\033[0m"
                lines.append(f"    {key:<40s} {status}")
            lines.append(f"\n{'─'*72}")
            lines.append(f"  \033[1mTOTAL: {running} running, {dead} stopped | CPU={total_cpu:.0f}% MEM={total_mem:.1f}%\033[0m")
            lines.append(f"{'='*72}")
            print("\n".join(lines), flush=True)
            if once:
                return
            time.sleep(5)
    except KeyboardInterrupt:
        pass


def main():
    LOGDIR.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) < 2:
        print("Usage: python ez_launcher.py [start|stop|restart|status]")
        print("  start          Launch all services (skips already running)")
        print("  stop           Stop all services")
        print("  restart        Stop then start")
        print("  status         Live dashboard (Ctrl+C to exit)")
        print("  status --once  Show status once")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "start":
        start_all()
    elif cmd == "stop":
        stop_all()
    elif cmd == "restart":
        stop_all()
        time.sleep(3)
        start_all()
    elif cmd == "status":
        show_status(once="--once" in sys.argv)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
