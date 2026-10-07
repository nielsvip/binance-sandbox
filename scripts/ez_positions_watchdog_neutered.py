#!/home/niels/.conda/envs/binance_env/bin/python3

"""

ez_positions_watchdog_neutered.py - STABLE GATEWAY WATCHDOG

==============================================

- FIX: Prevents "Boot Loops" by enforcing a strict 120s grace period on startup.

- FIX: Inherits Conda environment correctly.

- Monitors position updaters (ez_positions.py per account).

- Syncs positions to server binance/{account_key}/.

"""

import asyncio

import json

import logging

import os

import signal

import subprocess

import sys

import time

from datetime import datetime, timezone

from pathlib import Path

from typing import Optional, Dict, List, Tuple, Any

from logging.handlers import RotatingFileHandler

# --- CONFIGURATION ---

STARTUP_GRACE_PERIOD = 90.0   # Give script 90s to initialize before checking files

FILE_STALE_THRESHOLD = 60.0   # Files older than 60s are considered stale (after grace period)

REDIS_STALE_THRESHOLD = 30.0  # Redis older than 30s is considered stale

CHECK_INTERVAL = 5.0          # Check loop interval

RSYNC_INTERVAL = 60.0         # Sync to server every 60s

# Setup logging

def setup_logging():
    logger = logging.getLogger("ez_positions_watchdog")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
    # Console Handler
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    # File Handler
    try:
        logs_dir = Path.home() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(str(logs_dir / "ez_positions_watchdog_neutered.log"), maxBytes=5*1024*1024, backupCount=3, encoding='utf-8', mode='a')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Could not setup file logging: {e}", file=sys.stderr)
    return logger

logger = setup_logging()

class NeuteredPositionWatchdog:
    def __init__(self):
        self._shutdown_requested = False
        self.base_dir = Path("/home/niels/binance")
        # Determine script location
        script_dir = Path(__file__).parent.absolute()
        if (script_dir / "ez_positions.py").exists():
            self.base_dir = script_dir
        self.fetcher_script = self.base_dir / "ez_positions.py"
        self.python_executable = sys.executable # Use THIS python to run children
        self.accounts = ["ang", "fin", "flz", "men", "inf"]
        # State Tracking
        self.process_start_times: Dict[str, float] = {} # When did WE start it?
        self.process_pids: Dict[str, int] = {}          # What is the PID?
        self.last_rsync: Dict[str, float] = {}
        self.last_file_mtime: Dict[str, Dict[str, float]] = {}
        # Server Sync Config
        self.server_host = "niels@157.180.125.52"
        self.server_base = "/home/niels/binance"
        self.ssh_identity_file = Path.home() / ".ssh" / "id_rsa"
        self.rsync_semaphore = asyncio.Semaphore(1)
        logger.info(f"Watchdog initialized. Base: {self.base_dir}")
        logger.info(f"Grace Period: {STARTUP_GRACE_PERIOD}s | Stale Threshold: {FILE_STALE_THRESHOLD}s")
    def find_process_pid(self, account_key: str) -> Optional[int]:
        """Find PID of ez_positions.py running for a specific account."""
        try:
            # Look for: python.*ez_positions.py.*--account.*{account_key}
            cmd = ["pgrep", "-f", f"ez_positions.py.*--account.*{account_key}"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                pids = result.stdout.strip().split('\n')
                if pids:
                    # Return the newest PID (usually the last one)
                    return int(pids[-1])
        except Exception:
            pass
        return None
    def kill_process(self, pid: int, account_key: str):
        """Force kill a process."""
        try:
            os.kill(pid, signal.SIGKILL)
            logger.warning(f"[{account_key}] 💀 Killed PID {pid}")
        except ProcessLookupError:
            pass # Already dead
        except Exception as e:
            logger.error(f"[{account_key}] Error killing PID {pid}: {e}")
    async def start_fetcher(self, account_key: str):
        """Start ez_positions.py for an account."""
        try:
            # 1. Kill existing first to ensure clean slate
            old_pid = self.find_process_pid(account_key)
            if old_pid:
                self.kill_process(old_pid, account_key)
                await asyncio.sleep(1)
            # 2. Prepare logs
            logs_dir = Path.home() / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_file = open(logs_dir / "ez_positions.log", "a")
            # 3. Launch using current sys.executable to inherit environment
            cmd = [ self.python_executable, "-u", str(self.fetcher_script), "--account", account_key ]
            logger.info(f"[{account_key}] 🚀 Starting fetcher...")
            proc = subprocess.Popen( cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=str(self.base_dir), start_new_session=True, env=os.environ.copy() # CRITICAL: Pass current env (conda) to child )
            # 4. Update State
            self.process_pids[account_key] = proc.pid
            self.process_start_times[account_key] = time.time()
            logger.info(f"[{account_key}] ✅ Started with PID {proc.pid}")
        except Exception as e:
            logger.error(f"[{account_key}] ❌ Failed to start: {e}")
    # async def get_remote_last_updated(self, account_key: str, side: str) -> float:
    #         """Fetches the 'last_updated' field from the remote JSON file via SSH."""
    #         remote_path = f"{self.server_base}/{account_key}/{side}_positions.json"
    #         cmd = [
    #             "ssh", "-o", "StrictHostKeyChecking=no", "-i", str(self.ssh_identity_file),
    #             self.server_host,
    #             f"python3 -c \"import json, os; print(json.load(open('{remote_path}'))['last_updated']) if os.path.exists('{remote_path}') else print(0)\""
    #         ]
    #         try:
    #             proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=5)
    #             if proc.returncode == 0:
    #                 return float(proc.stdout.strip() or 0)
    #         except Exception:
    #             pass
    #         return 0.0
    def check_file_freshness(self, account_key: str) -> float:
        """Returns age of newest position file in seconds."""
        account_dir = self.base_dir / account_key
        newest_mtime = 0.0
        found_files = False
        for side in ["long", "short"]:
            fpath = account_dir / f"{side}_positions.json"
            if fpath.exists():
                found_files = True
                try:
                    mtime = fpath.stat().st_mtime
                    if mtime > newest_mtime:
                        newest_mtime = mtime
                except: pass
        if not found_files:
            return 999999.0 # No files exist
        return time.time() - newest_mtime
    # async def sync_to_server(self, account_key: str):
    #     """Rsync files to central server."""
    #     # Simple debounce
    #     now = time.time()
    #     if now - self.last_rsync.get(account_key, 0) < RSYNC_INTERVAL:
    #         return
    #     async with self.rsync_semaphore:
    #         try:
    #             # Check if files changed
    #             account_dir = self.base_dir / account_key
    #             should_sync = False
    #             for side in ["long", "short"]:
    #                 fpath = account_dir / f"{side}_positions.json"
    #                 if fpath.exists():
    #                     mtime = fpath.stat().st_mtime
    #                     last = self.last_file_mtime.get(account_key, {}).get(side, 0)
    #                     if mtime > last:
    #                         should_sync = True
    #                         if account_key not in self.last_file_mtime: self.last_file_mtime[account_key] = {}
    #                         self.last_file_mtime[account_key][side] = mtime
    #             if not should_sync:
    #                 return
    #             # Ensure remote dir
    #             server_target = f"{self.server_host}:{self.server_base}/{account_key}/"
    #             mkdir_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-i", str(self.ssh_identity_file), self.server_host, f"mkdir -p {self.server_base}/{account_key}"]
    #             await asyncio.to_thread(subprocess.run, mkdir_cmd, capture_output=True, timeout=10)
    #             # Rsync
    #             cmd = [
    #                 "rsync", "-avz", 
    #                 "-e", f"ssh -o StrictHostKeyChecking=no -i {self.ssh_identity_file}",
    #                 "--exclude=*.tmp", "--exclude=*.lock", "--exclude=backups*",
    #                 str(account_dir) + "/", # Sync contents of dir
    #                 server_target
    #             ]
    #             proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=60)
    #             if proc.returncode == 0:
    #                 logger.info(f"[{account_key}] ☁️ Synced to server")
    #                 self.last_rsync[account_key] = now
    #             else:
    #                 logger.warning(f"[{account_key}] Rsync failed: {proc.stderr[:100]}")
    #         except Exception as e:
    #             logger.error(f"[{account_key}] Sync error: {e}")
    async def get_remote_timestamp(self, account_key: str, side: str) -> str:
        """Fetches the 'last_updated' string from the first key of the remote JSON."""
        remote_path = f"{self.server_base}/{account_key}/{side}_positions.json"
        # Python snippet to get the first key's 'last_updated' value
        remote_cmd = ( f"python3 -c \"import json, os; " f"f='{remote_path}'; " f"data=json.load(open(f)) if os.path.exists(f) else {{}}; " f"first=next(iter(data.values())) if data else {{}}; " f"print(first.get('last_updated', '0000-00-00 00:00:00'))\"" )
        cmd = [ "ssh", "-o", "StrictHostKeyChecking=no", "-i", str(self.ssh_identity_file), self.server_host, remote_cmd ]
        try:
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=8)
            if proc.returncode == 0:
                return proc.stdout.strip()
        except Exception:
            pass
        return "0000-00-00 00:00:00"
    async def sync_to_server(self, account_key: str):
        """Rsync files only if local dictionary contains newer timestamps."""
        now = time.time()
        if now - self.last_rsync.get(account_key, 0) < RSYNC_INTERVAL:
            return
        async with self.rsync_semaphore:
            try:
                account_dir = self.base_dir / account_key
                should_sync = False
                for side in ["long", "short"]:
                    fpath = account_dir / f"{side}_positions.json"
                    if not fpath.exists():
                        continue
                    # 1. Extract local timestamp from first dictionary entry
                    local_ts = "0000-00-00 00:00:00"
                    try:
                        with open(fpath, 'r') as f:
                            data = json.load(f)
                            if data:
                                # Get the first value in the dict (the position object)
                                first_entry = next(iter(data.values()))
                                # Preference: last_updated, fallback: mark_price_last_updated
                                local_ts = first_entry.get('last_updated') or first_entry.get('mark_price_last_updated', '0000-00-00 00:00:00')
                    except Exception as e:
                        logger.error(f"[{account_key}] Local JSON read error: {e}")
                        continue
                    # 2. Extract remote timestamp via SSH
                    remote_ts = await self.get_remote_timestamp(account_key, side)
                    # 3. String comparison (works for ISO datetimes: "2023-12" > "2023-11")
                    if local_ts > remote_ts:
                        logger.info(f"[{account_key}] {side} is newer (Local: {local_ts} > Remote: {remote_ts})")
                        should_sync = True
                    elif local_ts < remote_ts:
                        logger.warning(f"[{account_key}] {side} local data is BEHIND server. Local: {local_ts} | Remote: {remote_ts}")
                if not should_sync:
                    # Even if we don't sync, we update last_rsync to avoid hammering the SSH check
                    self.last_rsync[account_key] = now
                    return
                # 4. Perform Rsync
                server_target = f"{self.server_host}:{self.server_base}/{account_key}/"
                # Use -u (update) flag as a secondary safety: skip files that are newer on receiver
                cmd = [ "rsync", "-avzu", "-e", f"ssh -o StrictHostKeyChecking=no -i {self.ssh_identity_file}", "--exclude=*.tmp", "--exclude=*.lock", str(account_dir) + "/", server_target ]
                proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=60)
                if proc.returncode == 0:
                    logger.info(f"[{account_key}] ☁️ Sync successful")
                    self.last_rsync[account_key] = now
                else:
                    logger.warning(f"[{account_key}] Rsync failed: {proc.stderr[:100]}")
            except Exception as e:
                logger.error(f"[{account_key}] Sync error: {e}")
    async def monitor_account(self, account_key: str):
        """Main monitoring logic for a single account."""
        while not self._shutdown_requested:
            try:
                now = time.time()
                # 1. Identify Process
                pid = self.find_process_pid(account_key)
                # 2. Check if we need to start it (No PID)
                if not pid:
                    logger.warning(f"[{account_key}] Process not running. Starting...")
                    await self.start_fetcher(account_key)
                    await asyncio.sleep(5)
                    continue
                # 3. Check Uptime / Grace Period
                # If we tracked the start time, use it. If not (restarted externally), assume it just started to be safe.
                start_time = self.process_start_times.get(account_key, now)
                uptime = now - start_time
                # If we found a PID but don't have it in memory, adopt it
                if account_key not in self.process_pids or self.process_pids[account_key] != pid:
                    self.process_pids[account_key] = pid
                    self.process_start_times[account_key] = now # Assume new if PID changed
                    uptime = 0 # Reset uptime logic
                # 4. GRACE PERIOD CHECK
                if uptime < STARTUP_GRACE_PERIOD:
                    logger.info(f"[{account_key}] ⏳ Startup Grace Period ({uptime:.0f}s/{STARTUP_GRACE_PERIOD}s) - PID {pid} OK")
                    await self.sync_to_server(account_key)
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue
                # 5. HEALTH CHECK (Only after grace period)
                file_age = self.check_file_freshness(account_key)
                is_healthy = True
                fail_reason = ""
                if file_age > FILE_STALE_THRESHOLD:
                    is_healthy = False
                    fail_reason = f"Files stale ({file_age:.0f}s)"
                # 6. ACT
                if is_healthy:
                    logger.info(f"[{account_key}] ✅ Healthy (PID {pid}, Files {file_age:.0f}s old)")
                    await self.sync_to_server(account_key)
                else:
                    logger.error(f"[{account_key}] ❌ UNHEALTHY: {fail_reason}. Uptime: {uptime:.0f}s. RESTARTING.")
                    await self.start_fetcher(account_key) # This will kill old PID and start new
            except Exception as e:
                logger.error(f"[{account_key}] Monitor loop error: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(CHECK_INTERVAL)
    async def run(self):
        logger.info("--- NEUTERED WATCHDOG STARTING ---")
        tasks = []
        for acct in self.accounts:
            tasks.append(asyncio.create_task(self.monitor_account(acct)))
        await asyncio.gather(*tasks)

async def main():
    watchdog = NeuteredPositionWatchdog()
    def signal_handler():
        logger.info("Shutdown signal received")
        watchdog._shutdown_requested = True
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, signal_handler)
    loop.add_signal_handler(signal.SIGTERM, signal_handler)
    await watchdog.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
