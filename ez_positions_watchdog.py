"""
ez_positions_watchdog.py — Lightweight watchdog for position data freshness.

ONLY job: check if positions_service (inside ez_manage) is updating position data.
If data is stale for an account, start ez_positions_realtime_{acct}.py as a backup fetcher.
When data is fresh again, stop the backup fetcher (ez_manage took over).

NEVER:
- Creates its own positions_service or _rest_poll_loop
- Makes Binance API calls directly
- Loads position data from disk/Redis
- Touches other accounts' data
"""
import asyncio
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
ACCOUNT_KEYS = getattr(config, 'ACCOUNT_KEYS', ['ang', 'inf', 'men', 'fin', 'flz'])
STALE_THRESHOLD_S = 30.0  # position data older than this = stale
CHECK_INTERVAL_S = 10.0  # how often to check freshness
GRACE_PERIOD_S = 60.0  # after starting realtime, wait this long before checking again
PYTHON = sys.executable

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("ez_positions_watchdog")
logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
fh = RotatingFileHandler(str(logs_dir / "ez_positions_watchdog.log"), maxBytes=2 * 1024 * 1024, backupCount=3)
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(fh)


def _get_position_file_mtime(account_key: str, side: str) -> float:
    f = BASE_PATH / account_key / f"{side}_positions.json"
    try:
        return f.stat().st_mtime if f.exists() else 0.0
    except Exception:
        return 0.0


def _get_freshness(account_key: str) -> float:
    """Returns age in seconds of the freshest position file for this account."""
    now = time.time()
    long_age = now - _get_position_file_mtime(account_key, "long")
    short_age = now - _get_position_file_mtime(account_key, "short")
    return min(long_age, short_age)


def _is_realtime_running(account_key: str) -> bool:
    try:
        result = subprocess.run(["pgrep", "-f", f"ez_positions_realtime_{account_key}"], capture_output=True, text=True, timeout=3)
        return result.returncode == 0
    except Exception:
        return False


def _start_realtime(account_key: str) -> bool:
    script = BASE_PATH / f"ez_positions_realtime_{account_key}.py"
    if not script.exists():
        logger.error(f"[{account_key}] Script not found: {script}")
        return False
    log_path = logs_dir / f"ez_positions_realtime_{account_key}.log"
    try:
        log_file = open(log_path, 'a')
        subprocess.Popen([PYTHON, "-u", str(script)], stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
        logger.info(f"[{account_key}] Started ez_positions_realtime_{account_key}.py")
        return True
    except Exception as e:
        logger.error(f"[{account_key}] Failed to start realtime: {e}")
        return False


def _stop_realtime(account_key: str):
    try:
        subprocess.run(["pkill", "-f", f"ez_positions_realtime_{account_key}"], capture_output=True, timeout=5)
        logger.info(f"[{account_key}] Stopped ez_positions_realtime_{account_key}.py")
    except Exception:
        pass


async def watchdog_loop():
    logger.info(f"[WATCHDOG] Started — monitoring {ACCOUNT_KEYS} every {CHECK_INTERVAL_S}s, stale threshold={STALE_THRESHOLD_S}s")
    last_start_time = {}  # account_key -> timestamp when we last started realtime
    while True:
        try:
            for acct in ACCOUNT_KEYS:
                age = _get_freshness(acct)
                realtime_running = _is_realtime_running(acct)
                grace_active = (time.time() - last_start_time.get(acct, 0)) < GRACE_PERIOD_S
                if age > STALE_THRESHOLD_S and not realtime_running and not grace_active:
                    logger.warning(f"[{acct}] Position data STALE ({age:.0f}s > {STALE_THRESHOLD_S}s) — starting realtime backup fetcher")
                    if _start_realtime(acct):
                        last_start_time[acct] = time.time()
                elif age <= STALE_THRESHOLD_S and realtime_running:
                    logger.info(f"[{acct}] Position data FRESH ({age:.0f}s) — stopping realtime backup (ez_manage handling it)")
                    _stop_realtime(acct)
        except Exception as e:
            logger.error(f"[WATCHDOG] Error in check loop: {e}")
        await asyncio.sleep(CHECK_INTERVAL_S)


async def main():
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _sig(sig, frame):
        logger.info(f"[WATCHDOG] Signal {sig} received — shutting down")
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    task = asyncio.create_task(watchdog_loop())
    await stop.wait()
    task.cancel()
    for acct in ACCOUNT_KEYS:
        if _is_realtime_running(acct):
            _stop_realtime(acct)
    logger.info("[WATCHDOG] Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
