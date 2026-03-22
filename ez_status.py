#!/usr/bin/env python3
"""System Status Monitor — shows what's running and what's missing."""
import subprocess
import sys
import time
import os

# All scripts that SHOULD be running locally
EXPECTED_SCRIPTS = {
    "DATA (SE1)": [
        "ez_prices_ws.py",
        "ez_klines.py",
        "ez_mark_prices.py",
        "ez_prices.py",
        "ez_share_ind.py",
        "ez_indicators.py",
        "ez_market_data.py",
        "ez_crosses.py",
        "ez_rankings.py",
        "ez_news_scanner.py",
        "ez_positions_quick.py",
        "ez_positions_watchdog.py",
    ],
    "MANAGE (SE2)": [
        "ez_manage.py.*ang",
        "ez_manage.py.*inf",
        "ez_manage.py.*flz",
        "ez_manage.py.*men",
        "ez_manage.py.*fin",
    ],
    "TRADIER (SE3)": [
        "tradier_prices.py",
        "tradier_positions.py",
        "tradier_indicators.py",
        "tradier_rankings.py",
        "tradier_manage.py.*trb",
        "tradier_manage.py.*trc",
    ],
    "REALTIME": [
        "ez_positions_realtime_ang.py",
        "ez_positions_realtime_fin.py",
        "ez_positions_realtime_flz.py",
        "ez_positions_realtime_inf.py",
        "ez_positions_realtime_men.py",
    ],
    "INFRA": [
        "ez_mem_watchdog.py",
        "setup_ssh_tunnels.py",
    ],
}

REDIS_PORTS = {"local": 6379, "gateway": 6380, "server": 6381}

def check_process(pattern):
    """Returns (running, pid, uptime_str)."""
    try:
        result = subprocess.run(["pgrep", "-f", f"python.*{pattern}"], capture_output=True, text=True)
        pids = [p for p in result.stdout.strip().split("\n") if p]
        my_pid = str(os.getpid())
        pids = [p for p in pids if p != my_pid]
        if not pids:
            return False, None, None
        pid = pids[0]
        ps_result = subprocess.run(["ps", "-p", pid, "-o", "etime="], capture_output=True, text=True)
        uptime = ps_result.stdout.strip() if ps_result.stdout.strip() else "?"
        return True, pid, uptime
    except Exception:
        return False, None, None

def check_redis(port):
    try:
        result = subprocess.run(["redis-cli", "-p", str(port), "ping"], capture_output=True, text=True, timeout=3)
        return result.stdout.strip() == "PONG"
    except Exception:
        return False

def print_status():
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    total_ok = 0
    total_missing = 0
    missing_list = []
    print(f"\n{'=' * 60}")
    print(f"  SYSTEM STATUS — {now}")
    print(f"{'=' * 60}")
    # Redis
    print(f"\n  REDIS:")
    for name, port in REDIS_PORTS.items():
        ok = check_redis(port)
        icon = "✅" if ok else "❌"
        print(f"    {icon} {name:10s} (localhost:{port})")
        if not ok:
            missing_list.append(f"Redis {name}:{port}")
    # Scripts
    for group, scripts in EXPECTED_SCRIPTS.items():
        print(f"\n  {group}:")
        for pattern in scripts:
            display = pattern.replace(".*", " ")
            running, pid, uptime = check_process(pattern)
            if running:
                print(f"    ✅ {display:40s} pid={pid:>6s}  up={uptime}")
                total_ok += 1
            else:
                print(f"    ❌ {display:40s} NOT RUNNING")
                total_missing += 1
                missing_list.append(display.strip())
    # Summary
    print(f"\n{'=' * 60}")
    print(f"  TOTAL: {total_ok} running, {total_missing} missing")
    if missing_list:
        print(f"\n  ⚠️  MISSING ({total_missing}):")
        for m in missing_list:
            print(f"    → {m}")
    else:
        print(f"\n  ✅ ALL SYSTEMS OPERATIONAL")
    print(f"{'=' * 60}\n")
    return total_missing

def main():
    watch = "--watch" in sys.argv or "-w" in sys.argv
    if watch:
        interval = 30
        for arg in sys.argv[1:]:
            if arg.isdigit():
                interval = int(arg)
        print(f"Watching every {interval}s (Ctrl+C to stop)\n")
        try:
            while True:
                os.system("clear")
                missing = print_status()
                if missing > 0:
                    print(f"  🔔 {missing} service(s) DOWN!")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        missing = print_status()
        sys.exit(1 if missing > 0 else 0)

if __name__ == "__main__":
    main()
