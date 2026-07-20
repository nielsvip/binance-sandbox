#!/usr/bin/env python3
"""Mac-side liveness heartbeat for the S1 failover monitor (2026-07-20 USER: "if macbook is
not running ez_/tradier_, s1 should immediately kick in and take over live trading").
Writes data/mac_live_heartbeat.json (this machine's view of which live accounts are actually
running, verified by pgrep on the CHILD process, not the run_with_watchdog.sh wrapper — the
wrapper surviving while its child is dead was the exact blind spot in the 2026-06-26/27
STALE_INDICATORS incident). Run every 1 min via cron; a separate cron line rsyncs the file to
S1 immediately after. Read by s1_failover_monitor.sh on S1 — see that script for what happens
on a stale/missing heartbeat. This script ONLY observes and writes; it never starts, stops, or
touches any trading process.
"""
import json
import subprocess
import time
from datetime import datetime, timezone, time as dt_time
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None
BASE_PATH = "/Users/niels/Documents/binance"
OUT_PATH = f"{BASE_PATH}/data/mac_live_heartbeat.json"
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc"]


def _pgrep_alive(pattern: str) -> bool:
    try:
        result = subprocess.run(["pgrep", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except Exception:
        return False


def _market_open_now() -> bool:
    if ZoneInfo is None:
        return False
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    return dt_time(9, 30) <= now_et.time() <= dt_time(16, 0)


def _push_to_s1():
    for dest in ("s1-int:/home/niels/binance/data/mac_live_heartbeat.json",
                 "s1-int:/home/niels/binance-sandbox/data/mac_live_heartbeat.json"):
        try:
            subprocess.run(
                ["rsync", "-az", "--timeout=15", OUT_PATH, dest],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        except Exception:
            pass


def main():
    market_open = _market_open_now()
    accounts = {}
    for acct in CRYPTO_ACCOUNTS:
        accounts[acct] = _pgrep_alive(f"python -u ez_manage.py --account {acct}")
    for acct in STOCK_ACCOUNTS:
        accounts[acct] = _pgrep_alive(f"python -u tradier_manage.py --accounts {acct}")
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "epoch": time.time(),
        "market_open_et": market_open,
        "accounts": accounts,
    }
    with open(OUT_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)
    _push_to_s1()


if __name__ == "__main__":
    main()
