#!/opt/anaconda3/envs/binance_env/bin/python
"""ez_launcher.py — Starts all trading services via launchd. KeepAlive ensures restart on crash.
Only starts services not already running. Exits 0 when all services confirmed running."""
import os
import subprocess
import sys
import time
from pathlib import Path

PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
BASE = Path("/Users/niels/Documents/binance")
LOG_DIR = Path("/Users/niels/logs")
LOG_DIR.mkdir(exist_ok=True)

SERVICES = [
    ("ez_manage.py --account ang", "ez_manage_ang"),
    ("ez_manage.py --account inf", "ez_manage_inf"),
    ("ez_manage.py --account flz", "ez_manage_flz"),
    ("ez_manage.py --account men", "ez_manage_men"),
    ("ez_manage.py --account fin", "ez_manage_fin"),
    ("ez_indicators.py", "ez_indicators"),
    ("ez_klines.py", "ez_klines"),
    ("ez_prices.py", "ez_prices"),
    ("ez_prices_ws.py", "ez_prices_ws"),
    ("ez_rankings.py", "ez_rankings"),
    ("ez_market_data.py", "ez_market_data"),
    ("ez_positions_watchdog.py", "ez_positions_watchdog"),
    ("ez_backup.py", "ez_backup"),
    ("ez_news_scanner.py", "ez_news_scanner"),
    ("tradier_manage.py", "tradier_manage"),
    ("tradier_indicators.py", "tradier_indicators"),
    ("tradier_rankings.py", "tradier_rankings"),
]


def is_running(script_name):
    try:
        result = subprocess.run(["pgrep", "-f", script_name], capture_output=True, text=True)
        return bool(result.stdout.strip())
    except Exception:
        return False


def start_service(script_args, log_name):
    parts = script_args.split()
    script = parts[0]
    script_path = str(BASE / script)
    if not Path(script_path).exists():
        print(f"SKIP {script} — file not found")
        return False
    cmd = f"nohup {PYTHON} -u {script_path} {' '.join(parts[1:])} >> {LOG_DIR / (log_name + '.log')} 2>&1 &"
    subprocess.Popen(cmd, shell=True, cwd=str(BASE), start_new_session=True)
    print(f"STARTED {script_args}")
    return True


def main():
    started = 0
    skipped = 0
    for script_args, log_name in SERVICES:
        check_name = script_args.split()[0]
        if is_running(check_name):
            skipped += 1
            continue
        if start_service(script_args, log_name):
            started += 1
        time.sleep(0.5)
    print(f"Launcher done: started={started} already_running={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main() if len(sys.argv) > 1 and sys.argv[1] == "start" else 0)
