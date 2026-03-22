#!/usr/bin/env python3
"""ez_mem_watchdog — macOS memory watchdog for trading processes.
Monitors system memory pressure. When usage gets dangerous, kills
all ez_ and tradier_ Python processes BEFORE macOS force-kills
everything (which closes all Claude terminals and agents).
Then restarts them cleanly via iTerm2 AppleScript.

The watchdog itself uses <5MB RAM and runs forever.

Usage:
  python ez_mem_watchdog.py                   # Default: kill at 82%
  python ez_mem_watchdog.py --threshold 78    # Kill earlier
  python ez_mem_watchdog.py --no-restart      # Kill only, manual restart
  python ez_mem_watchdog.py --dry-run         # Log only, no kills

Install as LaunchAgent (survives reboots):
  python ez_mem_watchdog.py --install
"""
import os
import plistlib
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Thresholds
MEM_WARN_PCT = 85
MEM_KILL_PCT = 92
MEM_EMERGENCY_PCT = 96
CHECK_INTERVAL = 10          # seconds
RESTART_COOLDOWN = 120       # min seconds between kill→restart cycles
RESTART_DELAY = 8            # seconds to wait before restart after kill

LOG_FILE = Path.home() / "logs" / "ez_mem_watchdog.log"
WORKDIR = Path.home() / "Documents" / "binance"
PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"

# Processes to kill when memory is critical (trading processes)
KILL_PATTERNS = [
    "ez_manage.py", "ez_positions_quick.py", "ez_positions_service.py",
    "ez_positions.py", "ez_positions_watchdog.py",
    "ez_indicators.py", "ez_indicators_merger.py", "ez_market_data.py",
    "ez_rankings.py", "ez_prices.py", "ez_prices_ws.py",
    "ez_klines.py", "ez_mark_prices.py", "ez_share_ind.py",
    "ez_crosses.py", "ez_double.py", "ez_gain_protector.py",
    "ez_gap_filler.py", "ez_loss_mitigator.py", "ez_news_scanner.py",
    "ez_breakout_agent.py", "ez_klines_htf.py", "ez_metric_sweep.py",
    "tradier_manage.py", "tradier_indicators.py", "tradier_rankings.py",
    "tradier_prices.py", "tradier_positions.py", "tradier_webhook_bridge.py",
    "continuous_param_optimizer.py",
]
# NEVER kill these
NEVER_KILL = ["claude", "Claude", "watchdog", "mem_watchdog"]


def log(msg):
    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    line = f"{ts} | {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_mem_pressure():
    """Get memory usage % from vm_stat. Returns (used_pct, swap_mb)."""
    try:
        r = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        stats = {}
        for line in r.stdout.strip().split("\n")[1:]:
            parts = line.split(":")
            if len(parts) == 2:
                try:
                    stats[parts[0].strip()] = int(parts[1].strip().rstrip("."))
                except ValueError:
                    pass
        ps = 16384
        free = stats.get("Pages free", 0) * ps
        active = stats.get("Pages active", 0) * ps
        inactive = stats.get("Pages inactive", 0) * ps
        spec = stats.get("Pages speculative", 0) * ps
        wired = stats.get("Pages wired down", 0) * ps
        comp = stats.get("Pages occupied by compressor", 0) * ps
        total = free + active + inactive + spec + wired + comp
        used = active + wired + comp
        pct = int(used / total * 100) if total > 0 else 0
    except Exception:
        pct = 0
    try:
        r = subprocess.run(["sysctl", "vm.swapusage"], capture_output=True, text=True, timeout=5)
        parts = r.stdout.split("used = ")
        swap_mb = float(parts[1].split("M")[0]) if len(parts) > 1 else 0
    except Exception:
        swap_mb = 0
    used_gb = used / (1024 ** 3) if total > 0 else 0
    return pct, swap_mb, used_gb


def get_trading_procs():
    """Get trading Python processes sorted by memory (heaviest first)."""
    procs = []
    try:
        r = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
        for line in r.stdout.strip().split("\n")[1:]:
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            cmd = parts[10]
            if "python" not in cmd.lower():
                continue
            if any(nk in cmd for nk in NEVER_KILL):
                continue
            if any(kp in cmd for kp in KILL_PATTERNS):
                procs.append({"pid": int(parts[1]), "cpu": float(parts[2]), "mem": float(parts[3]), "rss_mb": int(parts[5]) / 1024 if parts[5].isdigit() else 0, "cmd": cmd.split("/")[-1][:60]})
    except Exception:
        pass
    procs.sort(key=lambda x: x["mem"], reverse=True)
    return procs


def kill_all_trading(reason, dry_run=False):
    """Kill all trading processes. Returns count killed."""
    procs = get_trading_procs()
    if not procs:
        log(f"[KILL] {reason} — no trading processes to kill")
        return 0
    total_mem = sum(p["mem"] for p in procs)
    log(f"[KILL] {reason} — killing {len(procs)} processes ({total_mem:.1f}% total MEM)")
    killed = 0
    for p in procs:
        if dry_run:
            log(f"  [DRY] Would kill {p['pid']} ({p['cpu']:.0f}%CPU {p['mem']:.1f}%MEM) {p['cmd']}")
            killed += 1
            continue
        try:
            os.kill(p["pid"], signal.SIGTERM)
            log(f"  TERM {p['pid']} ({p['cpu']:.0f}%CPU {p['mem']:.1f}%MEM) {p['cmd']}")
            killed += 1
        except (ProcessLookupError, PermissionError):
            pass
    if not dry_run:
        time.sleep(3)
        for p in procs:
            try:
                os.kill(p["pid"], 0)
                os.kill(p["pid"], signal.SIGKILL)
                log(f"  KILL {p['pid']} (survived SIGTERM)")
            except (ProcessLookupError, PermissionError):
                pass
    log(f"[KILL] Done — {killed} processes {'would be ' if dry_run else ''}killed")
    return killed


def kill_duplicates(dry_run=False):
    """Find and kill duplicate trading processes (same script running multiple times).
    Keep the NEWEST instance, kill the older ones."""
    procs = get_trading_procs()
    by_script = {}
    for p in procs:
        # Extract script name from command
        cmd = p["cmd"]
        script = None
        for pat in KILL_PATTERNS:
            if pat in cmd:
                script = pat
                break
        if not script:
            continue
        # For ez_manage.py and ez_positions*, group by script+account
        if "manage" in script or "positions" in script:
            # Try to extract account arg
            parts = cmd.split()
            for i, part in enumerate(parts):
                if part in ("ang", "inf", "men", "fin", "flz", "trb", "trc"):
                    script = f"{script}_{part}"
                    break
                if part == "--account" and i + 1 < len(parts):
                    script = f"{script}_{parts[i+1]}"
                    break
        by_script.setdefault(script, []).append(p)
    killed = 0
    for script, instances in by_script.items():
        if len(instances) <= 1:
            continue
        # Sort by PID (highest = newest), keep newest, kill rest
        instances.sort(key=lambda x: x["pid"], reverse=True)
        keeper = instances[0]
        dupes = instances[1:]
        log(f"[DUPES] {script}: {len(instances)} instances, keeping PID {keeper['pid']}, killing {len(dupes)} older")
        for d in dupes:
            if dry_run:
                log(f"  [DRY] Would kill dupe {d['pid']} ({d['cpu']:.0f}%CPU {d['mem']:.1f}%MEM)")
            else:
                try:
                    os.kill(d["pid"], signal.SIGTERM)
                    log(f"  TERM dupe {d['pid']} ({d['cpu']:.0f}%CPU {d['mem']:.1f}%MEM)")
                    killed += 1
                except (ProcessLookupError, PermissionError):
                    pass
    if killed:
        log(f"[DUPES] Killed {killed} duplicate processes")
    return killed


def restart_via_iterm():
    """Kill all trading scripts and relaunch the full iTerm system via start_everything.command."""
    log(f"[RESTART] Full iTerm system restart in {RESTART_DELAY}s...")
    time.sleep(RESTART_DELAY)
    se_path = str(WORKDIR / "start_everything.command")
    try:
        subprocess.Popen(["bash", se_path], cwd=str(WORKDIR), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"[RESTART] Launched {se_path}")
    except Exception as e:
        log(f"[RESTART] Failed to launch start_everything.command: {e}")

def restart_lightweight():
    """Alias for restart_via_iterm."""
    restart_via_iterm()
    return
    # === BELOW IS DEAD CODE (kept for reference) ===
    log(f"[RESTART] Lightweight restart in {RESTART_DELAY}s (no iTerm tabs)...")
    time.sleep(RESTART_DELAY)
    logdir = Path.home() / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    # Infrastructure scripts (order matters: prices first, then indicators, then trading)
    infra_scripts = [
        "ez_prices_ws.py",
        "ez_prices.py",
        "ez_klines.py",
        "ez_mark_prices.py",
        "ez_share_ind.py",
        "ez_indicators.py",
        "ez_market_data.py",
        "ez_crosses.py",
        "ez_rankings.py",
        "ez_news_scanner.py",
    ]
    # Trading scripts (launched after infra is up)
    trading_scripts = [
        ("ez_manage.py", ["ang"]),
        ("ez_manage.py", ["inf"]),
        ("ez_manage.py", ["men"]),
        ("ez_manage.py", ["fin"]),
        ("ez_manage.py", ["flz"]),
        ("ez_positions_quick.py", ["--account", "ang"]),
        ("ez_positions.py", ["--account", "ang"]),
        ("ez_loss_mitigator.py", []),
    ]
    # Stock scripts
    stock_scripts = [
        ("tradier_prices.py", []),
        ("tradier_indicators.py", []),
        ("tradier_rankings.py", []),
        ("tradier_manage.py", ["trb"]),
        ("tradier_manage.py", ["trc"]),
    ]
    launched = 0
    # Launch infra
    for script in infra_scripts:
        script_path = WORKDIR / script
        if not script_path.exists():
            continue
        logfile = logdir / f"{script.replace('.py', '')}.log"
        try:
            subprocess.Popen([PYTHON, "-u", str(script_path)], cwd=str(WORKDIR), stdout=open(logfile, "a"), stderr=subprocess.STDOUT, start_new_session=True)
            launched += 1
        except Exception as e:
            log(f"  FAIL {script}: {e}")
    log(f"[RESTART] Launched {launched} infra scripts, waiting 5s...")
    time.sleep(5)
    # Launch trading
    for script, args in trading_scripts:
        script_path = WORKDIR / script
        if not script_path.exists():
            continue
        acct = args[-1] if args else "main"
        logfile = logdir / f"{script.replace('.py', '')}_{acct}.log"
        try:
            subprocess.Popen([PYTHON, "-u", str(script_path)] + args, cwd=str(WORKDIR), stdout=open(logfile, "a"), stderr=subprocess.STDOUT, start_new_session=True)
            launched += 1
        except Exception as e:
            log(f"  FAIL {script} {args}: {e}")
    # Launch stocks
    for script, args in stock_scripts:
        script_path = WORKDIR / script
        if not script_path.exists():
            continue
        acct = args[0] if args else "main"
        logfile = logdir / f"{script.replace('.py', '')}_{acct}.log"
        try:
            subprocess.Popen([PYTHON, "-u", str(script_path)] + args, cwd=str(WORKDIR), stdout=open(logfile, "a"), stderr=subprocess.STDOUT, start_new_session=True)
            launched += 1
        except Exception as e:
            log(f"  FAIL {script} {args}: {e}")
    log(f"[RESTART] Done — launched {launched} processes as background daemons (zero iTerm tabs)")


def install_launch_agent():
    """Install as macOS LaunchAgent (auto-start on login, survives reboots)."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.ez.mem-watchdog.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": "com.ez.mem-watchdog",
        "ProgramArguments": [PYTHON, str(Path(__file__).resolve())],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG_FILE),
        "StandardErrorPath": str(LOG_FILE),
        "WorkingDirectory": str(WORKDIR),
    }
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)
    log(f"[INSTALL] LaunchAgent installed at {plist_path}")
    log(f"[INSTALL] Will auto-start on login and stay alive forever")
    print(f"Installed: {plist_path}")
    print("Will auto-start on login. To uninstall: launchctl unload ~/Library/LaunchAgents/com.ez.mem-watchdog.plist")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="macOS Memory Watchdog")
    parser.add_argument("--threshold", type=int, default=MEM_KILL_PCT, help=f"Kill at this %% (default {MEM_KILL_PCT}) — IGNORED, use --threshold-gb")
    parser.add_argument("--threshold-gb", type=int, default=90, help="Kill when used memory exceeds this many GB (default 90)")
    parser.add_argument("--interval", type=int, default=CHECK_INTERVAL, help=f"Check every N seconds (default {CHECK_INTERVAL})")
    parser.add_argument("--no-restart", action="store_true", help="Kill only, don't auto-restart")
    parser.add_argument("--dry-run", action="store_true", help="Log only, don't actually kill")
    parser.add_argument("--install", action="store_true", help="Install as macOS LaunchAgent")
    args = parser.parse_args()
    if args.install:
        install_launch_agent()
        return
    mem_kill_gb = args.threshold_gb
    log(f"🛡️ [WATCHDOG] Started | kill={mem_kill_gb}GB warn={MEM_WARN_PCT}% | interval={args.interval}s | dry_run={args.dry_run}")
    last_kill_time = 0
    last_warn_time = 0
    while True:
        try:
            mem_pct, swap_mb, used_gb = get_mem_pressure()
            procs = get_trading_procs()
            n = len(procs)
            total_cpu = sum(p["cpu"] for p in procs)
            total_mem = sum(p["mem"] for p in procs)
            now = time.time()
            if used_gb >= mem_kill_gb:
                log(f"🚨 MEMORY {used_gb:.1f}GB >= {mem_kill_gb}GB | swap={swap_mb:.0f}MB | {n} procs {total_cpu:.0f}%CPU {total_mem:.1f}%MEM")
                if now - last_kill_time > RESTART_COOLDOWN:
                    kill_all_trading(f"MEM_{used_gb:.0f}GB", dry_run=args.dry_run)
                    last_kill_time = now
                    if not args.no_restart and not args.dry_run:
                        restart_via_iterm()
            elif mem_pct >= MEM_WARN_PCT and now - last_warn_time > 120:
                log(f"[WARN] mem={mem_pct}% ({used_gb:.1f}GB) swap={swap_mb:.0f}MB | {n} procs {total_cpu:.0f}%CPU {total_mem:.1f}%MEM")
                last_warn_time = now
            # Always check for duplicates (every 60s) — duplicates = memory leak
            if now - getattr(main, '_last_dupe_check', 0) > 60:
                kill_duplicates(dry_run=args.dry_run)
                main._last_dupe_check = now
        except Exception as e:
            log(f"[ERROR] {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
