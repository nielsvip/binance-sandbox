import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from contextvars import ContextVar
from datetime import datetime
from datetime import time as dt_time
from datetime import timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

from config import Config
from utils import (construct_position_key, get_simple_redis_manager, load_environment_from_gpg, orjson_default, safe_fetch_float)

load_environment_from_gpg(None)
import ez_positions_service

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("ez_positions_watchdog")
import os
from pathlib import Path

logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
file_handler = RotatingFileHandler(str(logs_dir / "ez_positions_watchdog.log"), maxBytes=1*1024*1024, backupCount=3, encoding='utf-8', mode='a')
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)
def _open_rotating_log(path: Path, max_bytes: int = 20 * 1024 * 1024) -> object:
    """Open log file for subprocess stdout, rotating (truncating) if over max_bytes."""
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
            path.write_text('\n'.join(lines[-2000:]) + '\n', encoding='utf-8')
            logger.info(f"[WATCHDOG] Rotated {path.name} (>{max_bytes//1024//1024}MB)")
    except Exception:
        pass
    return open(path, 'a')
class PositionWatchdog:
    def __init__(self):
        self._shutdown_requested = False
        script_dir = Path(__file__).parent.absolute()
        if (script_dir / "ez_positions.py").exists():
            self.base_dir = script_dir
        else:
            self.base_dir = Path("/home/niels/binance")
        self.conda_sh_path = self._find_conda_sh()
        self.realtime_scripts = {
            #'inf': self.base_dir / "ez_positions_realtime_inf.py",
            'ang': self.base_dir / "ez_positions_realtime_ang.py",
            'inf': self.base_dir / "ez_positions_realtime_inf.py",
            'flz': self.base_dir / "ez_positions_realtime_flz.py",
            'men': self.base_dir / "ez_positions_realtime_men.py",
            'fin': self.base_dir / "ez_positions_realtime_fin.py",  }
        self.backup_script = self.base_dir / "ez_positions_backup.py"
        self.backup_account_script = self.base_dir / "ez_positions_backup_account.py"
        self.active_script = self.base_dir / "ez_positions_active.py"  # Track active script too
        self.quick_script = self.base_dir / "ez_positions_quick.py"  # Quick reentry/exit monitor
        self.accounts = list(self.realtime_scripts.keys())
        self.realtime_processes: Dict[str, subprocess.Popen] = {}
        self.backup_processes: List[subprocess.Popen] = []
        self.account_backup_processes: Dict[str, List[subprocess.Popen]] = {}
        self.quick_processes: Dict[str, Optional[subprocess.Popen]] = {} 

        self.quick_accounts = ['ang', 'inf', 'men', 'flz', 'fin']

        self.quick_script_mtime: Optional[float] = None
        self.quick_start_times: Dict[str, float] = {} 
        self.quick_grace_period = 120.0  # Give quick scripts 120s to initialize before killing them
        self.check_interval = 15.0  # Check every 15 seconds
        self.stale_threshold = 240.0  # Files must be updated within 60 seconds
        self.redis_stale_threshold = 120.0  # Redis must be updated within 45 seconds
        self.quick_activity_threshold = 180.0 # Increased to prevent premature killing
        self.fetcher_start_times: Dict[str, float] = {}  # Track when each fetcher was started (for grace period)
        self.fetcher_startup_grace_period = 300.0  # Give fetchers 300 seconds to start up
        self.fallback_chain = ["ez_positions.py", "ez_positions_backup.py", "ez_positions_realtime"]  # Fallback order
        self.current_fetcher: Optional[str] = None  # Track which fetcher is currently active
        self.last_force_fetch: Dict[str, float] = {}  # Track last force fetch time per account
        self.force_fetch_cooldown = 10.0  # Minimum 10s between force fetches to prevent flapping
        
        self.fetcher_script = self.base_dir / "ez_positions.py"
        self.fetcher_process: Optional[subprocess.Popen] = None
        self.fetcher_processes: Dict[str, Optional[subprocess.Popen]] = {}  # CRITICAL: One process per account
        
        self.ez_manage_script = self.base_dir / "ez_manage.py"
        self.internal_fetcher_enabled_flag = self.base_dir / ".internal_fetcher_enabled"
        self.internal_fetcher_disabled_flag = self.base_dir / ".internal_fetcher_disabled"
        self.max_backup_instances = 2
        self.process_restart_cooldown: Dict[str, float] = {}
        self.restart_cooldown_seconds = 10.0
        self.fetcher_failure_count: Dict[str, int] = {}
        self.fetcher_last_error: Dict[str, str] = {}
        self.config = Config()
        self.big_position_threshold = 3.0 * self.config.START_POSITION_SIZE
        self.big_position_monitor_running = False
        self.last_big_position_check: Dict[str, float] = {}
        self.cached_services: Dict[str, Any] = {}
        self._primary_fetcher_running = False
        self._primary_fetcher_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None
        self._account_start_locks: Dict[str, asyncio.Lock] = {}
        self._account_starting: Dict[str, bool] = {}

        # Shared Memory Server
        self.share_ind_process = None

        # Tradier Management
        self.tradier_scripts = [
            {"name": "tradier_prices", "cmd": [sys.executable, "-u", "tradier_prices.py"]},
            {"name": "tradier_positions", "cmd": [sys.executable, "-u", "tradier_positions.py", "--accounts", "tra", "trb", "trc"]},
            {"name": "tradier_indicators", "cmd": [sys.executable, "-u", "tradier_indicators.py"]},
            {"name": "tradier_rankings", "cmd": [sys.executable, "-u", "tradier_rankings.py"]},
            {"name": "tradier_manage_trb", "cmd": [sys.executable, "-u", "tradier_manage.py", "--accounts", "trb"]},
            {"name": "tradier_manage_trc", "cmd": [sys.executable, "-u", "tradier_manage.py", "--accounts", "trc"]},
            {"name": "ez_indicators_merger", "cmd": [sys.executable, "-u", "ez_indicators_merger.py"]},
        ]
        self.tradier_processes = {}
        self.last_tradier_restart_date = None

        logger.warning(f"[WATCHDOG] Using base directory: {self.base_dir}")
    
    def _find_conda_sh(self) -> str:
        """Find conda.sh path dynamically"""
        import subprocess
        possible_paths = [
            "/opt/anaconda3/etc/profile.d/conda.sh",
            "/home/niels/miniforge3/etc/profile.d/conda.sh",
            f"{Path.home()}/miniforge3/etc/profile.d/conda.sh",
            f"{Path.home()}/anaconda3/etc/profile.d/conda.sh",
        ]
        try:
            result = subprocess.run(["conda", "info", "--base"], capture_output=True, text=True, timeout=9)
            if result.returncode == 0:
                conda_base = result.stdout.strip()
                conda_sh = Path(conda_base) / "etc/profile.d/conda.sh"
                if conda_sh.exists():
                    return str(conda_sh)
        except Exception:
            pass
        for path in possible_paths:
            if Path(path).exists():
                return path
        return possible_paths[0]
    
    def is_process_running(self, process: Optional[subprocess.Popen]) -> bool:
        """Check if a process is running"""
        if process is None:
            return False
        return process.poll() is None
    
    def find_processes_by_name(self, pattern: str) -> List[int]:
        """Find process IDs by name pattern"""
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                text=True,
                timeout=9
            )
            if result.returncode == 0:
                return [int(pid) for pid in result.stdout.strip().split() if pid]
        except Exception as e:
            logger.debug(f"[WATCHDOG] pgrep failed for {pattern}: {e}")
        return []
    
    def get_cmdline(self, pid: int) -> str:
        """Helper to get process command line in a cross-platform way"""
        try:
            return subprocess.check_output(["ps", "-p", str(pid), "-o", "command="]).decode('utf-8', errors='ignore').strip().lower()
        except Exception:
            return ""

    def find_fetcher_pids_for_account(self, account_key: str) -> List[int]:
        """Find all PIDs for ez_positions.py processes for a specific account"""
        pids = []
        all_pids = self.find_processes_by_name("ez_positions.py")
        for pid in all_pids:
            cmdline = self.get_cmdline(pid)
            if f"--account {account_key.lower()}" in cmdline or f"--account={account_key.lower()}" in cmdline:
                pids.append(pid)
        return pids

    def kill_duplicate_fetchers(self, account_key: str) -> int:
        """Kill duplicate ez_positions.py instances for account, keeping only the oldest one."""
        pids = self.find_fetcher_pids_for_account(account_key)
        if len(pids) <= 1:
            return 0
        logger.info(f"[WATCHDOG][{account_key}] Found {len(pids)} duplicate instances for {account_key} - killing all but oldest")
        pids_with_times = []
        for pid in pids:
            try:
                uptime_str = subprocess.check_output(["ps", "-p", str(pid), "-o", "etimes="]).decode().strip()
                uptime = int(uptime_str) if uptime_str else 0
                pids_with_times.append((pid, -uptime))
            except Exception:
                pids_with_times.append((pid, 0))
        if not pids_with_times:
            return 0
        pids_with_times.sort(key=lambda x: x[1])
        keep_pid = pids_with_times[0][0]
        killed = 0
        for pid, _ in pids_with_times[1:]:
            try:
                os.kill(pid, 9)
                logger.info(f"[WATCHDOG][{account_key}] 💀 Killed duplicate PID {pid} (keeping {keep_pid})")
                killed += 1
            except (ProcessLookupError, FileNotFoundError):
                pass
            except Exception as e:
                logger.error(f"[WATCHDOG][{account_key}] Failed to kill duplicate PID {pid}: {e}")
        return killed

    def is_fetcher_running(self, account_key: Optional[str] = None) -> bool:
        """Check if ez_positions.py is running for account"""
        if account_key:
            if account_key in self.fetcher_processes:
                if self.is_process_running(self.fetcher_processes[account_key]):
                    return True
            pids = self.find_fetcher_pids_for_account(account_key)
            return len(pids) > 0
        else:
            if self.is_process_running(self.fetcher_process):
                return True
            pids = self.find_processes_by_name("ez_positions.py")
            return len(pids) > 0
    
    def is_fetcher_working(self, account_key: Optional[str] = None) -> Tuple[bool, float]:
        """Check if ez_positions.py is working - more robustly check log activity."""
        logs_dir = Path.home() / "logs"
        log_files = [logs_dir / "ez_positions_service.log", logs_dir / "ez_positions.log", logs_dir / "ez_positions_watchdog.log"]
        
        best_age = float('inf')
        found_activity = False
        
        # Keywords indicating process is alive and doing *something*
        activity_keywords = [
            "INFO", "WARN", "ERROR", "DEBUG",  # General logging
            "FETCHING", "FETCHED", "Processing", "Processed", "Websocket connected",
            "ACCOUNT_UPDATE", "WS_", "Broadcast/save", "SAVED",
            "HOLD", "ENTRY", "EXIT", "REDUCE", "STOP" # Strategy actions
        ]

        for fetcher_log in log_files:
            if not fetcher_log.exists():
                continue
            
            # --- Check File Freshness ---
            try:
                mtime = fetcher_log.stat().st_mtime
                file_age = time.time() - mtime
                if file_age > self.stale_threshold * 2: # If file is very old, skip content check
                    continue
                best_age = min(best_age, file_age) # Track oldest file modification time if still potentially valid
            except Exception as e:
                logger.debug(f"[WATCHDOG] Error checking file mtime for {fetcher_log.name}: {e}")
                continue

            # --- Check Content for Activity ---
            # Only check content if file is reasonably fresh
            if file_age < self.stale_threshold: 
                try:
                    with open(fetcher_log, 'r', encoding='utf-8', errors='ignore') as f:
                        # Read last N lines for recent activity
                        lines = f.readlines()
                        for line in reversed(lines[-500:]): # Read more lines for safety
                            if account_key:
                                # Ensure line pertains to the specific account if provided
                                account_patterns = [f"[{account_key}]", f"][{account_key}]", f" {account_key} ", f" {account_key}:", f":{account_key}"]
                                if not any(pattern in line for pattern in account_patterns):
                                    continue
                            
                            # Check for ANY activity keywords
                            if any(keyword in line for keyword in activity_keywords):
                                try:
                                    if ']' in line:
                                        # Extract timestamp from log line
                                        timestamp_str = line.split(']')[0].replace('[', '').strip()
                                        if ',' in timestamp_str: timestamp_str = timestamp_str.replace(',', '.') # Handle comma decimal separator
                                        
                                        dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
                                        log_age = (datetime.now() - dt).total_seconds()
                                        
                                        # If log entry is recent enough, consider it active
                                        if log_age < self.stale_threshold:
                                            found_activity = True
                                            best_age = min(best_age, log_age)
                                            # Found recent activity, no need to check older logs in this file
                                            break 
                                except Exception as parse_err:
                                    logger.debug(f"[WATCHDOG] Error parsing timestamp in {fetcher_log.name}: {parse_err}")
                                    # If parsing fails but file is FRESH, still consider it potential activity
                                    if file_age < self.stale_threshold:
                                        found_activity = True
                                        break # Found activity even if ts parsing failed

                except Exception as e:
                    logger.debug(f"[WATCHDOG] Error reading {fetcher_log.name} for content check: {e}")
        
        # If we found any activity that was within the stale threshold, it's working
        # If best_age is still inf, it means no valid files were found or they were all too old.
        # The original logic returned `found_activity and best_age < 120.0`, which is reasonable.
        # We'll keep that logic but ensure the age is based on the MOST RECENT activity.
        
        # If no activity found at all, return false and a large age value
        if not found_activity:
            return False, float('inf')
            
        # Otherwise, return true if the most recent activity (best_age) is within threshold
        return found_activity and best_age < self.stale_threshold, best_age
    
    def is_realtime_running(self, account_key: str) -> bool:
        if self.is_process_running(self.realtime_processes.get(account_key)):
            return True
        script_name = f"ez_positions_realtime_{account_key}.py"
        pids = self.find_processes_by_name(script_name)
        return len(pids) > 0
    
    def is_backup_running(self) -> bool:
        self.backup_processes = [p for p in self.backup_processes if self.is_process_running(p)]
        if len(self.backup_processes) > 0:
            return True
        pids = self.find_processes_by_name("ez_positions_backup.py")
        return len(pids) > 0
    
    def is_active_running(self) -> bool:
        pids = self.find_processes_by_name("ez_positions_active.py")
        return len(pids) > 0
    
    def is_quick_running(self, account_key: Optional[str] = None) -> bool:
        """Check if ez_positions_quick.py is running for account"""
        if account_key:
            if self.is_process_running(self.quick_processes.get(account_key)):
                return True
            pids = self.find_processes_by_name("ez_positions_quick.py")
            for pid in pids:
                cmdline = self.get_cmdline(pid)
                if f"--account {account_key.lower()}" in cmdline or f"--account={account_key.lower()}" in cmdline or (f" {account_key.lower()}" in cmdline and "--account" in cmdline):
                    return True
            return False
        else:
            for acc in self.quick_accounts:
                if self.is_quick_running(acc):
                    return True
            return False
    
    def is_quick_working(self, account_key: str) -> Tuple[bool, float]:
        """Strict check: Is the script actually writing to its log file? Checks both logs."""
        logs_dir = Path.home() / "logs"
        
        # Check BOTH the internal log and the process stdout log (crucial fix)
        files_to_check = [
            logs_dir / f"ez_positions_quick_{account_key}.log",
            logs_dir / f"quick_{account_key}_process.log"
        ]
        
        best_age = float('inf')
        found_activity = False
        
        # Keywords that indicate healthy activity - expanded list
        keywords = [
            "ENTRY", "EXIT", "STEP", "✅", "🚀", "MONITOR", "Service", "Connected", "Starting", 
            "INFO", "WARN", "ERROR", "DEBUG",
            f"fin:", f"ang:", f"inf:", f"flz:", f"men:", f"[{account_key.upper()}]", f"{account_key}:",
            "price", "hold", "reduce", "stoch"
        ]

        for log_file in files_to_check:
            if not log_file.exists():
                continue
                
            # 1. MTIME CHECK
            try:
                mtime = log_file.stat().st_mtime
                physical_age = time.time() - mtime
                if physical_age < best_age:
                    best_age = physical_age
            except Exception:
                continue

            # 2. CONTENT CHECK
            if physical_age < self.quick_activity_threshold:
                try:
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        # Check more lines to prevent buffer flush issues
                        for line in reversed(lines[-1000:]):
                            if any(k.lower() in line.lower() for k in keywords):
                                if ']' in line:
                                    try:
                                        ts_str = line.split(']')[0].replace('[', '').strip()
                                        if ',' in ts_str: ts_str = ts_str.replace(',', '.')
                                        dt = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S.%f')
                                        log_age = (datetime.now() - dt).total_seconds()
                                        if log_age < self.quick_activity_threshold:
                                            found_activity = True
                                            best_age = min(best_age, log_age)
                                            break 
                                    except Exception:
                                        # Fallback if timestamp parse fails but file is fresh and has keyword
                                        found_activity = True
                                        break
                except Exception:
                    pass
        
        if found_activity:
            return True, best_age
            
        # Fallback: If mtime is VERY fresh (< 45s) assume it's running even if regex failed
        if best_age < 45.0:
            return True, best_age
            
        return False, best_age
    
    def stop_active(self):
        pids = self.find_processes_by_name("ez_positions_active.py")
        for pid in pids:
            try:
                os.kill(pid, 9)
            except Exception:
                pass
        logger.warning(f"[WATCHDOG] ✅ Stopped active positions script")

    def start_quick(self, account_key: str) -> bool:
        """Start ez_positions_quick.py for specific account"""
        if account_key not in self.quick_accounts:
            logger.error(f"[WATCHDOG] ❌ Invalid account for quick script: {account_key}")
            return False
        if self.is_quick_running(account_key):
            logger.debug(f"[WATCHDOG] Quick script already running for {account_key}")
            return False
        if not self.quick_script or not self.quick_script.exists():
            logger.error(f"[WATCHDOG] ❌ Quick script not found: {self.quick_script}")
            return False
        try:
            # Kill any existing process for this account first
            pids = self.find_processes_by_name("ez_positions_quick.py")
            for pid in pids:
                try:
                    cmdline = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="]).decode().lower()
                    if f"--account {account_key.lower()}" in cmdline or f"--account={account_key.lower()}" in cmdline or f" {account_key.lower()}" in cmdline:
                        os.kill(pid, 9)
                except Exception:
                    pass
            logger.info(f"[WATCHDOG] 🚀 Starting ez_positions_quick.py for {account_key}")
            logs_dir = Path.home() / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_file = logs_dir / "ez_positions_quick.log"
            error_log = logs_dir / f"quick_{account_key}_process.log"
            log_f = _open_rotating_log(error_log)
            
            cmd = [sys.executable, "-u", str(self.quick_script), "--account", account_key]

            proc = subprocess.Popen(
                cmd,
                shell=False,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(self.base_dir),
                start_new_session=True,
                env=os.environ.copy()
            )
            self.quick_processes[account_key] = proc
            # CRITICAL: Record start time for grace period
            self.quick_start_times[account_key] = time.time()
            logger.info(f"[WATCHDOG] ✅ ez_positions_quick.py started for {account_key} (PID: {proc.pid})")
            return True
        except Exception as e:
            logger.info(f"[WATCHDOG] ❌ Failed to start quick script for {account_key}: {e}", exc_info=True)
            return False
    
    def stop_quick(self, account_key: Optional[str] = None):
        """Stop ez_positions_quick.py for account"""
        if account_key:
            if account_key in self.quick_start_times:
                del self.quick_start_times[account_key]
            
            if self.quick_processes.get(account_key) and self.is_process_running(self.quick_processes[account_key]):
                try:
                    self.quick_processes[account_key].terminate()
                    try:
                        self.quick_processes[account_key].wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        self.quick_processes[account_key].kill()
                    logger.warning(f"[WATCHDOG] ✅ Stopped quick script for {account_key}")
                except Exception as e:
                    logger.error(f"[WATCHDOG] Error stopping quick script for {account_key}: {e}")
            self.quick_processes.pop(account_key, None)
            
            pids = self.find_processes_by_name("ez_positions_quick.py")
            for pid in pids:
                try:
                    cmdline = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="]).decode().lower()
                    if f"--account {account_key.lower()}" in cmdline or f"--account={account_key.lower()}" in cmdline or f" {account_key.lower()}" in cmdline:
                        os.kill(pid, 9)
                except Exception:
                    pass
        else:
            self.quick_start_times.clear()
            for acc in list(self.quick_processes.keys()):
                self.stop_quick(acc)
            pids = self.find_processes_by_name("ez_positions_quick.py")
            for pid in pids:
                try:
                    os.kill(pid, 9)
                except Exception:
                    pass
    
    def is_account_backup_running(self, account_key: str) -> bool:
        procs = self.account_backup_processes.get(account_key, [])
        alive_procs = [p for p in procs if self.is_process_running(p)]
        self.account_backup_processes[account_key] = alive_procs
        if len(alive_procs) > 0:
            return True
        pids = self.find_processes_by_name(f"ez_positions_backup_account.py.*{account_key}")
        return len(pids) > 0
    
    def check_file_freshness(self, account_key: str) -> Tuple[bool, float]:
        account_dir = self.base_dir / account_key
        long_file = account_dir / "long_positions.json"
        short_file = account_dir / "short_positions.json"
        max_age = 0.0
        files_checked = 0
        for f in [long_file, short_file]:
            if f.exists():
                files_checked += 1
                mtime = f.stat().st_mtime
                age = time.time() - mtime
                max_age = max(max_age, age)
            else:
                return (False, float('inf'))
        if files_checked == 0:
            return (False, float('inf'))
        is_fresh = max_age < self.stale_threshold
        return (is_fresh, max_age)

    def check_save_activity(self, account_key: str) -> Tuple[bool, bool, float, float]:
        logs_dir = Path.home() / "logs"
        log_files = [logs_dir / "ez_positions_service.log", logs_dir / "ez_positions.log", logs_dir / "ez_positions_watchdog.log"]
        redis_save_age = float('inf')
        json_save_age = float('inf')
        redis_save_found = False
        json_save_found = False
        for log_file in log_files:
            if not log_file.exists():
                continue
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    for line in reversed(lines[-2000:]):
                        line_lower = line.lower()
                        if account_key:
                            account_patterns = [f"[{account_key}]", f"][{account_key}]", f" {account_key} ", f" {account_key}:", f":{account_key}"]
                            if not any(pattern in line for pattern in account_patterns):
                                continue
                        redis_keywords = ["_broadcast_positions_to_redis", "broadcast/save", "full broadcast", "broadcasting", "redis", "positions:redis"]
                        if any(keyword.lower() in line_lower for keyword in redis_keywords):
                            try:
                                if ']' in line:
                                    timestamp_str = line.split(']')[0].replace('[', '').strip()
                                    dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                    age = (datetime.now() - dt).total_seconds()
                                    if age < 120.0:
                                        redis_save_found = True
                                        redis_save_age = min(redis_save_age, age)
                            except Exception:
                                pass
                        json_keywords = ["atomic_save", "💾", "saved", "saving", "positions.json", "long_positions.json", "short_positions.json", "completed"]
                        if any(keyword.lower() in line_lower if isinstance(keyword, str) else keyword in line for keyword in json_keywords):
                            try:
                                if ']' in line:
                                    timestamp_str = line.split(']')[0].replace('[', '').strip()
                                    dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                    age = (datetime.now() - dt).total_seconds()
                                    if age < 120.0:
                                        json_save_found = True
                                        json_save_age = min(json_save_age, age)
                            except Exception:
                                pass
            except Exception as e:
                logger.debug(f"[WATCHDOG] Error checking save activity in {log_file.name}: {e}")
        return (redis_save_found and redis_save_age < 120.0, json_save_found and json_save_age < 120.0, redis_save_age, json_save_age)
    
    async def check_redis_freshness(self, account_key: str) -> Tuple[bool, float, int]:
        try:
            import redis
            r = redis.Redis(host='localhost', port=6379, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
            redis_key = f"positions:{account_key}"
            try:
                data = r.get(redis_key)
            except (redis.ConnectionError, redis.TimeoutError, Exception) as conn_err:
                logger.debug(f"[WATCHDOG] Redis connection error for {account_key}: {conn_err}")
                return (False, float('inf'), 0)
            if not data:
                return (False, float('inf'), 0)
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict) and "meta" in parsed:
                    meta = parsed.get("meta", {})
                    timestamp_str = meta.get("timestamp") or meta.get("positions_last_sync")
                    if timestamp_str:
                        try:
                            if isinstance(timestamp_str, str):
                                if 'T' in timestamp_str:
                                    dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                                else:
                                    dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
                            else:
                                dt = datetime.now(timezone.utc)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            now = datetime.now(timezone.utc)
                            age = (now - dt).total_seconds()
                            positions = parsed.get("positions", {})
                            position_count = len(positions) if isinstance(positions, dict) else 0
                            is_fresh = age < self.redis_stale_threshold
                            if not is_fresh and position_count > 0:
                                logger.warning(f"[WATCHDOG][{account_key}] ⚠️ Redis has {position_count} positions but data is STALE ({age:.1f}s > {self.redis_stale_threshold}s)")
                            return (is_fresh, age, position_count)
                        except Exception as parse_err:
                            logger.warning(f"[WATCHDOG][{account_key}] ❌ Failed to parse Redis timestamp (data may be stale): {parse_err}")
                positions = parsed.get("positions", {})
                position_count = len(positions) if isinstance(positions, dict) else 0
                if position_count > 0:
                    logger.warning(f"[WATCHDOG][{account_key}] ⚠️ Redis has {position_count} positions but NO TIMESTAMP - treating as STALE")
                return (False, float('inf'), position_count)
            except Exception as parse_err:
                logger.debug(f"[WATCHDOG] Failed to parse Redis data: {parse_err}")
                return (False, float('inf'), 0)
        except Exception as e:
            logger.debug(f"[WATCHDOG] Redis check failed for {account_key}: {e}")
            return (False, float('inf'), 0)
    
    def can_restart(self, account_key: str) -> bool:
        last_restart = self.process_restart_cooldown.get(account_key, 0.0)
        return (time.time() - last_restart) >= self.restart_cooldown_seconds

    async def start_fetcher(self, account_key: Optional[str] = None, force_kill: bool = False) -> bool:
            if account_key:
                if account_key not in self._account_start_locks:
                    self._account_start_locks[account_key] = asyncio.Lock()
                start_lock = self._account_start_locks[account_key]
                if self._account_starting.get(account_key, False):
                    logger.warning(f"[WATCHDOG][{account_key}] ⏸️ Already starting process for {account_key} - skipping duplicate start")
                    return False
                async with start_lock:
                    if self._account_starting.get(account_key, False):
                        return False
                    if self.is_fetcher_running(account_key):
                        logger.warning(f"[WATCHDOG][{account_key}] ⏸️ Process already running - not starting duplicate")
                        return True
                    self._account_starting[account_key] = True
                    try:
                        logger.info(f"[WATCHDOG][{account_key}] 🚨 Checking for existing ez_positions.py processes for {account_key}...")
                        existing_pids = self.find_fetcher_pids_for_account(account_key)
                        killed_all = 0
                        for pid in existing_pids:
                            try:
                                os.kill(pid, 9)
                                killed_all += 1
                                logger.info(f"[WATCHDOG][{account_key}] 💀 Killed PID {pid} for {account_key}")
                            except (ProcessLookupError, FileNotFoundError):
                                pass
                            except Exception as kill_err:
                                logger.error(f"[WATCHDOG][{account_key}] Failed to kill {pid}: {kill_err}")
                        if killed_all > 0:
                            wait_time = 5.0 if force_kill else 3.0
                            logger.info(f"[WATCHDOG][{account_key}] 💀💀💀 Killed {killed_all} processes for {account_key} - waiting {wait_time}s for cleanup...")
                            await asyncio.sleep(wait_time)
                        remaining_pids = self.find_fetcher_pids_for_account(account_key)
                        for pid in remaining_pids:
                            try:
                                os.kill(pid, 9)
                            except Exception: pass

                        if not self.fetcher_script or not self.fetcher_script.exists():
                            logger.error(f"[WATCHDOG] ❌ Fetcher script not found: {self.fetcher_script}")
                            self._account_starting[account_key] = False
                            return False

                        logger.info(f"[WATCHDOG] 🚀 Starting ez_positions.py fetcher for {account_key}")
                        
                        logs_dir = Path.home() / "logs"
                        logs_dir.mkdir(parents=True, exist_ok=True)
                        log_file = logs_dir / f"ez_positions.log"
                        log_f = _open_rotating_log(log_file)
                        
                        cmd = [ 
                            sys.executable, 
                            "-u", 
                            str(self.fetcher_script),   
                            "--account",   
                            account_key  
                        ]
                    
                        proc = subprocess.Popen(
                            cmd,  
                            shell=False,      
                            stdout=log_f,    
                            stderr=subprocess.STDOUT,    
                            cwd=str(self.base_dir),  
                            start_new_session=True,  
                            env=os.environ.copy()  
                        )
                        
                        if not hasattr(self, '_fetcher_log_files'):
                            self._fetcher_log_files = {}
                        self._fetcher_log_files[account_key] = log_f
                        self.fetcher_processes[account_key] = proc
                        
                        logger.info(f"[WATCHDOG] ✅ Fetcher started for {account_key} (PID: {proc.pid})")
                        
                        await asyncio.sleep(5.0)
                        
                        if proc.poll() is not None:
                            exit_code = proc.poll()
                            error_msg = "Unknown error"
                            try:
                                if log_file.exists():
                                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                        lines = f.readlines()
                                        last_lines = [l.strip() for l in lines[-30:] if l.strip()]
                                        for line in reversed(last_lines):
                                            if account_key.lower() in line.lower() and ('error' in line.lower() or 'exception' in line.lower() or 'traceback' in line.lower() or 'failed' in line.lower()):
                                                error_msg = line[:200]
                                                break
                            except Exception:
                                pass
                            
                            self.fetcher_failure_count[account_key] = self.fetcher_failure_count.get(account_key, 0) + 1
                            self.fetcher_last_error[account_key] = error_msg
                            failure_count = self.fetcher_failure_count[account_key]
                            
                            logger.error(f"[WATCHDOG] ❌❌❌ Fetcher for {account_key} (PID: {proc.pid}) EXITED after 5s with code {exit_code} (failure #{failure_count})")
                            logger.error(f"[WATCHDOG][{account_key}] Error: {error_msg}")
                            self._account_starting[account_key] = False
                            return False
                            
                        if account_key in self.fetcher_failure_count:
                            del self.fetcher_failure_count[account_key]
                        if account_key in self.fetcher_last_error:
                            del self.fetcher_last_error[account_key]

                        try:
                            os.kill(proc.pid, 0)
                            logger.warning(f"[WATCHDOG] Fetcher for {account_key} (PID: {proc.pid}) verified running after 5s (PID check)")
                            self._account_starting[account_key] = False
                            return True
                        except ProcessLookupError:
                            logger.error(f"[WATCHDOG] ❌❌❌ Fetcher for {account_key} (PID: {proc.pid}) not found by PID after 5s - process died")
                            self._account_starting[account_key] = False
                            return False

                    except Exception as e:
                        logger.info(f"[WATCHDOG] ❌ Failed to start fetcher for {account_key}: {e}", exc_info=True)
                        self._account_starting[account_key] = False
                        return False
                    finally:
                        self._account_starting[account_key] = False
            else:
                return False

    def stop_fetcher(self, account_key: Optional[str] = None):
        if account_key:
            if account_key in self.fetcher_processes:
                proc = self.fetcher_processes[account_key]
                if proc and self.is_process_running(proc):
                    try:
                        proc.terminate()
                        try:
                            proc.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        logger.warning(f"[WATCHDOG] ✅ Stopped fetcher for {account_key}")
                    except Exception as e:
                        logger.error(f"[WATCHDOG] Error stopping fetcher for {account_key}: {e}")
                self.fetcher_processes[account_key] = None
            if account_key in self.fetcher_start_times:
                del self.fetcher_start_times[account_key]
            pids = self.find_fetcher_pids_for_account(account_key)
            for pid in pids:
                try:
                    os.kill(pid, 9)
                    logger.warning(f"[WATCHDOG] Killed fetcher process {pid} for {account_key}")
                except Exception:
                    pass
        else:
            if self.fetcher_process and self.is_process_running(self.fetcher_process):
                try:
                    self.fetcher_process.terminate()
                    try:
                        self.fetcher_process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        self.fetcher_process.kill()
                    logger.warning(f"[WATCHDOG] ✅ Stopped fetcher")
                except Exception as e:
                    logger.error(f"[WATCHDOG] Error stopping fetcher: {e}")
            self.fetcher_process = None
            for acc_key, proc in list(self.fetcher_processes.items()):
                if proc and self.is_process_running(proc):
                    try:
                        proc.terminate()
                        try:
                            proc.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                    except Exception:
                        pass
                self.fetcher_processes[acc_key] = None
            self._primary_fetcher_running = False
            pids = self.find_processes_by_name("ez_positions_ultimate.py")
            for pid in pids:
                try:
                    os.kill(pid, 9)
                except Exception:
                    pass
        pids = self.find_processes_by_name("ez_positions_ultimate.py")
        for pid in pids:
            try:
                os.kill(pid, 9)
            except Exception:
                pass
    
    def start_realtime(self, account_key: str) -> bool:
        logger.debug(f"[WATCHDOG] ⏸️ Realtime scripts DISABLED - using ez_positions.py only")
        return False
    
    def stop_realtime(self, account_key: str):
        proc = self.realtime_processes.get(account_key)
        if proc and self.is_process_running(proc):
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                logger.warning(f"[WATCHDOG] ✅ Stopped realtime for {account_key}")
            except Exception as e:
                logger.error(f"[WATCHDOG] Error stopping realtime: {e}")
        self.realtime_processes.pop(account_key, None)
        pids = self.find_processes_by_name(f"ez_positions_realtime_{account_key}.py")
        for pid in pids:
            try:
                os.kill(pid, 9)
            except Exception:
                pass

    def _reap_zombies(self):
        """Reap zombie child processes to prevent defunct accumulation"""
        for proc_dict in [self.realtime_processes, self.fetcher_processes, self.quick_processes, self.tradier_processes]:
            for key, proc in list(proc_dict.items()):
                if proc is not None:
                    try: proc.poll()
                    except Exception: pass
        for proc in self.backup_processes:
            try: proc.poll()
            except Exception: pass
        for acc, procs in self.account_backup_processes.items():
            for proc in procs:
                try: proc.poll()
                except Exception: pass
        reaped = 0
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
                if pid == 0: break
                reaped += 1
            except ChildProcessError: break
            except Exception: break
        if reaped > 0: logger.info(f"[WATCHDOG] 🧹 Reaped {reaped} zombie processes")
    
    def start_account_backup(self, account_key: str, count: int = 1) -> int:
        """Start account-specific backup. Returns number started"""
        procs = self.account_backup_processes.get(account_key, [])
        alive_count = len([p for p in procs if self.is_process_running(p)])
        needed = max(0, count - alive_count)
        if needed == 0:
            return 0
        if not self.can_restart(account_key):
            return 0
        started = 0
        try:
            for i in range(needed):
                logger.info(f"[WATCHDOG] 🚨 Starting account backup #{i+1} for {account_key}")
                proc = subprocess.Popen(
                    [sys.executable, str(self.backup_account_script), account_key, str(i)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(self.base_dir)
                )
                if account_key not in self.account_backup_processes:
                    self.account_backup_processes[account_key] = []
                self.account_backup_processes[account_key].append(proc)
                started += 1
                logger.info(f"[WATCHDOG] ✅ Account backup #{i+1} for {account_key} started (PID: {proc.pid})")
            self.process_restart_cooldown[account_key] = time.time()
        except Exception as e:
            logger.info(f"[WATCHDOG] ❌ Failed to start account backup: {e}", exc_info=True)
        return started
    
    def stop_account_backup(self, account_key: str):
        procs = self.account_backup_processes.get(account_key, [])
        for proc in procs:
            if self.is_process_running(proc):
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except Exception as e:
                    logger.error(f"[WATCHDOG] Error stopping backup: {e}")
        self.account_backup_processes.pop(account_key, None)
        pids = self.find_processes_by_name(f"ez_positions_backup_account.py.*{account_key}")
        for pid in pids:
            try:
                os.kill(pid, 9)
            except Exception:
                pass
        logger.warning(f"[WATCHDOG] ✅ Stopped account backups for {account_key}")
    
    def is_share_ind_running(self) -> bool:
        if self.share_ind_process and self.is_process_running(self.share_ind_process):
            return True
        pids = self.find_processes_by_name("ez_share_ind.py")
        return len(pids) > 0
    def start_share_ind(self) -> bool:
        if self.is_share_ind_running():
            return False
        script = self.base_dir / "ez_share_ind.py"
        if not script.exists():
            logger.warning("[WATCHDOG] ez_share_ind.py not found - skipping")
            return False
        try:
            from pathlib import Path as _Path
            logs_dir = _Path("/home/niels/logs") if sys.platform != "darwin" else _Path("/Users/niels/logs")
            log_file = open(logs_dir / "ez_share_ind.log", "a")
            proc = __import__('subprocess').Popen([sys.executable, "-u", str(script)], stdout=log_file, stderr=log_file, cwd=str(self.base_dir))
            self.share_ind_process = proc
            logger.info(f"[WATCHDOG] ✅ ez_share_ind started (PID: {proc.pid})")
            return True
        except Exception as e:
            logger.error(f"[WATCHDOG] ❌ Failed to start ez_share_ind: {e}")
            return False
    def start_backup(self) -> bool:
        if self.is_backup_running():
            return False
        try:
            logger.info(f"[WATCHDOG] 🚨 Starting main backup script")
            proc = subprocess.Popen(
                [sys.executable, str(self.backup_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.base_dir)
            )
            self.backup_processes.append(proc)
            logger.info(f"[WATCHDOG] ✅ Main backup started (PID: {proc.pid})")
            return True
        except Exception as e:
            logger.info(f"[WATCHDOG] ❌ Failed to start backup: {e}", exc_info=True)
            return False
    
    def stop_backups(self):
        for proc in self.backup_processes[:]:
            if self.is_process_running(proc):
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except Exception as e:
                    logger.error(f"[WATCHDOG] Error stopping backup: {e}")
        self.backup_processes.clear()
        pids = self.find_processes_by_name("ez_positions_backup.py")
        for pid in pids:
            try:
                os.kill(pid, 9)
            except Exception:
                pass

    def is_ez_manage_running(self) -> bool:
        pids = self.find_processes_by_name("ez_manage.py")
        return len(pids) > 0

    def enable_internal_fetcher(self):
        try:
            if self.internal_fetcher_enabled_flag.parent.exists():
                self.internal_fetcher_enabled_flag.touch()
                logger.info(f"[WATCHDOG] ✅ Enabled internal fetcher flag: {self.internal_fetcher_enabled_flag}")
            if self.internal_fetcher_disabled_flag.exists():
                self.internal_fetcher_disabled_flag.unlink()
        except Exception as e:
            logger.error(f"[WATCHDOG] Failed to enable internal fetcher flag: {e}")

    def disable_internal_fetcher(self):
        try:
            if self.internal_fetcher_disabled_flag.parent.exists():
                self.internal_fetcher_disabled_flag.touch()
                logger.warning(f"[WATCHDOG] ⚠️ Disabled internal fetcher flag: {self.internal_fetcher_disabled_flag}")
            if self.internal_fetcher_enabled_flag.exists():
                self.internal_fetcher_enabled_flag.unlink()
        except Exception as e:
            logger.error(f"[WATCHDOG] Failed to disable internal fetcher flag: {e}")

    # --- Tradier Management Methods ---
    def is_tradier_market_hours(self):
        """Checks if current time is Mon-Fri, 9:30 AM - 4:00 PM ET (robust to DST)."""
        et_tz = pytz.timezone("America/New_York")
        now_et = datetime.now(timezone.utc).astimezone(et_tz)
        if now_et.weekday() >= 5: return False
        
        # Buffer for keeping alive: 9:25 AM to 4:30 PM ET
        start = dt_time(9, 25)
        end = dt_time(16, 30)
        current_time = now_et.time()
        return start <= current_time <= end

    async def start_tradier_process(self, script):
        name = script["name"]
        cmd = script["cmd"]
        if name in self.tradier_processes and self.tradier_processes[name].poll() is None:
            return
        cmd_str = " ".join(cmd)
        pgrep = subprocess.run(["pgrep", "-f", cmd_str], capture_output=True, text=True)
        if pgrep.returncode == 0 and pgrep.stdout.strip():
            return
        logger.info(f"[WATCHDOG] 🚀 Starting Tradier process: {name}...")
        try:
            log_file = logs_dir / f"{name}.out"
            f = open(log_file, "a")
            proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(self.base_dir), start_new_session=True)
            self.tradier_processes[name] = proc
            logger.info(f"[WATCHDOG] ✅ {name} started (PID: {proc.pid})")
        except Exception as e:
            logger.error(f"[WATCHDOG] ❌ Failed to start {name}: {e}")

    async def stop_all_tradier(self):
        logger.info("[WATCHDOG] 🛑 Stopping all Tradier processes...")
        for name, proc in list(self.tradier_processes.items()):
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill()
        self.tradier_processes.clear()
        for script in self.tradier_scripts:
            subprocess.run(["pkill", "-f", " ".join(script["cmd"])], stderr=subprocess.DEVNULL)

    async def monitor_tradier(self):
        et_tz = pytz.timezone("America/New_York")
        now_et = datetime.now(timezone.utc).astimezone(et_tz)
        
        # 9:25 AM ET Restart trigger
        if now_et.time() >= dt_time(9, 25) and self.last_tradier_restart_date != now_et.date():
            logger.info(f"[WATCHDOG] ⏰ Scheduled 9:25 AM ET Tradier restart (Current ET: {now_et.strftime('%H:%M:%S')})")
            await self.stop_all_tradier()
            self.last_tradier_restart_date = now_et.date()
            
        # Ensure all Tradier scripts are running 24/7
        for script in self.tradier_scripts:
            await self.start_tradier_process(script)

    async def get_positions_from_redis(self, account_key: str) -> Dict[str, Any]:
        try:
            import redis
            r = redis.Redis(host='localhost', port=6379, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
            redis_key = f"positions:{account_key}"
            data = r.get(redis_key)
            if not data:
                return {}
            parsed = json.loads(data)
            if isinstance(parsed, dict) and "positions" in parsed:
                return parsed.get("positions", {})
            return {}
        except Exception as e:
            logger.debug(f"[BIG_POS_MONITOR] Redis get failed for {account_key}: {e}")
            return {}
    def get_position_value(self, position: Dict[str, Any]) -> float:
        position_amt = abs(safe_fetch_float(position.get("positionAmt") or position.get("pa"), 0.0))
        mark_price = safe_fetch_float(position.get("mark_price") or position.get("markPrice") or position.get("markPriceStr"), 0.0)
        if mark_price <= 0:
            entry_price = safe_fetch_float(position.get("entry_price") or position.get("entryPrice"), 0.0)
            if entry_price > 0:
                mark_price = entry_price
        return position_amt * mark_price if mark_price > 0 else 0.0
    async def reduce_big_position(self, position_key: str, account_key: str, position: Dict[str, Any], reason: str) -> bool:
        if config.HEDGE_MODE:
            try:
                from ez_positions_service import (bootstrap_position_service, load_accounts_from_config)
                if account_key not in self.cached_services:
                    config = Config()
                    accounts = await load_accounts_from_config(config, logger)
                    if account_key not in accounts:
                        return False
                    self.cached_services[account_key] = await bootstrap_position_service(logger=logger, accounts={account_key: accounts[account_key]}, enable_auto_fetch=True, load_priority='disk')
                service = self.cached_services[account_key]
                position_obj = service.positions.get(position_key) or service.positions_by_account.get(account_key, {}).get(position_key)
                if position_obj and hasattr(position_obj, 'gain') and position_obj.gain < 0:
                    logger.warning(f"[HEDGE_MODE_BLOCK] {position_key}: Blocking reduce_big_position - position in loss (gain={position_obj.gain:.2f}%) when HEDGE_MODE=True")
                    return False
            except Exception:
                pass
        try:
            symbol = position.get("symbol") or position.get("s", "")
            if not symbol:
                return False
            position_amt = safe_fetch_float(position.get("positionAmt") or position.get("pa"), 0.0)
            if abs(position_amt) <= 0:
                return False
            position_side = (position.get("positionSide") or position.get("ps") or ("LONG" if position_amt >= 0 else "SHORT")).upper()
            is_long = position_side == "LONG"
            mark_price = safe_fetch_float(position.get("mark_price") or position.get("markPrice"), 0.0)
            if mark_price <= 0:
                return False
            reduce_pct = 0.3
            reduce_qty = abs(position_amt) * reduce_pct
            side = "SELL" if is_long else "BUY"
            unique_id = f"watchdog_reduce_{int(time.time())}"
            logger.warning(f"[BIG_POS_REDUCE] {position_key}: Reducing by {reduce_pct*100:.0f}% ({reduce_qty:.6f}) - {reason}")
            try:
                from ez_positions_service import (bootstrap_position_service, load_accounts_from_config)
                if account_key not in self.cached_services:
                    config = Config()
                    accounts = await load_accounts_from_config(config, logger)
                    if account_key not in accounts:
                        return False
                    self.cached_services[account_key] = await bootstrap_position_service(logger=logger, accounts={account_key: accounts[account_key]}, enable_auto_fetch=False, load_priority='disk')
                service = self.cached_services[account_key]
                result = await service.execute_now(position_key, account_key, symbol, position_amt, side, position_side, reduce_qty, mark_price, unique_id, reason, False, "REDUCE")
                if result and result in ["SUCCESS", "SUCCESS_MAKER", "SUCCESS_MARKET", "SUCCESS_WEBHOOK"]:
                    logger.warning(f"[✅ BIG_POS_REDUCE] {position_key}: Reduced successfully: {result}")
                    return True
                else:
                    logger.error(f"[❌ BIG_POS_REDUCE] {position_key}: Reduce failed: {result}")
                    return False
            except Exception as exec_err:
                logger.error(f"[BIG_POS_REDUCE] {position_key}: Execute error: {exec_err}", exc_info=True)
                return False
        except Exception as e:
            logger.error(f"[BIG_POS_REDUCE] {position_key}: Error: {e}", exc_info=True)
            return False
    async def validate_positionamt_before_save(self, account_key: str, positions_dict: Dict[str, Any]) -> Tuple[bool, List[str]]:
        logs_dir = Path.home() / "logs"
        log_file = logs_dir / "ez_positions_service.log"
        mismatches = []
        if not log_file.exists():
            return (True, [])
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                for line in reversed(lines[-1000:]):
                    if f"[FETCHFETCH]" in line and account_key in line and "Fetched" in line and "positions from API" in line:
                        try:
                            if ']' in line:
                                timestamp_str = line.split(']')[0].replace('[', '').strip()
                                dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                age = (datetime.now() - dt).total_seconds()
                                if age < 300.0:
                                    if 'positions from API:' in line:
                                        fetch_part = line.split('positions from API:')[1].strip()
                                        entries = fetch_part.split(',')
                                        api_positions = {}
                                        for entry in entries:
                                            entry = entry.strip()
                                            if ':' in entry and entry.count(':') >= 2:
                                                parts = entry.split(':')
                                                if len(parts) >= 3:
                                                    try:
                                                        symbol = parts[0].strip()
                                                        side = parts[1].strip().upper()
                                                        api_amt = abs(float(parts[2].strip()))
                                                        api_positions[f"{symbol}_{side}"] = api_amt
                                                    except (ValueError, IndexError):
                                                        continue
                                        for pos_key, pos_data in positions_dict.items():
                                            if not isinstance(pos_data, dict):
                                                continue
                                            symbol = pos_data.get("symbol") or pos_data.get("s", "")
                                            position_side = pos_data.get("positionSide") or pos_data.get("ps", "")
                                            if not symbol or not position_side:
                                                continue
                                            api_key = f"{symbol}_{position_side.upper()}"
                                            api_amt = api_positions.get(api_key)
                                            if api_amt is not None:
                                                save_amt = abs(float(pos_data.get("positionAmt") or pos_data.get("pa") or 0))
                                                if abs(api_amt - save_amt) >= 0.000001:
                                                    mismatches.append(f"{pos_key}: API={api_amt:.6f} SAVE={save_amt:.6f} diff={abs(api_amt-save_amt):.6f}")
                                        break
                        except Exception as parse_err:
                            logger.debug(f"[WATCHDOG][{account_key}] Error parsing FETCHFETCH for validation: {parse_err}")
                        break
        except Exception as e:
            logger.debug(f"[WATCHDOG][{account_key}] Error validating positionAmt: {e}")
        return (len(mismatches) == 0, mismatches)
    
    async def force_update_positions_and_broadcast(self, account_key: Optional[str] = None) -> bool:
        """Force update positions JSONs and broadcasts to Redis for account(s)"""
        try:
            if account_key:
                last_force = self.last_force_fetch.get(account_key, 0.0)
                if time.time() - last_force < self.force_fetch_cooldown:
                    logger.debug(f"[WATCHDOG][{account_key}] ⏸️ Force update cooldown active ({time.time() - last_force:.1f}s < {self.force_fetch_cooldown}s) - skipping")
                    return False
                self.last_force_fetch[account_key] = time.time()
            from ez_positions import atomic_save_positions
            from ez_positions_service import (bootstrap_position_service, load_accounts_from_config)
            config = Config()
            accounts_to_update = [account_key] if account_key else self.accounts
            for acc_key in accounts_to_update:
                try:
                    redis_positions = await self.get_positions_from_redis(acc_key)
                    redis_fresh, redis_age, redis_count = await self.check_redis_freshness(acc_key)
                    if acc_key not in self.cached_services:
                        accounts = await load_accounts_from_config(config, logger)
                        if acc_key not in accounts:
                            logger.error(f"[WATCHDOG][{acc_key}] ❌ Account not found in config")
                            continue
                        self.cached_services[acc_key] = await bootstrap_position_service(logger=logger, accounts={acc_key: accounts[acc_key]}, enable_auto_fetch=True, load_priority='disk')
                    service = self.cached_services[acc_key]
                    account_positions = service.positions_by_account.get(acc_key, {})
                    if redis_positions and redis_fresh and redis_age < 60.0:
                        logger.info(f"[WATCHDOG][{acc_key}] ✅✅✅ Redis has FRESH data (age: {redis_age:.1f}s, count: {redis_count}) - using Redis data instead of memory/files")
                        from ez_positions_service import Position
                        redis_loaded = {}
                        for pos_key, pos_data in redis_positions.items():
                            if isinstance(pos_data, dict) and pos_key.startswith(f"{acc_key}:"):
                                try:
                                    pos_obj = Position.from_dict(pos_data)
                                    redis_loaded[pos_key] = pos_obj
                                    service.positions_by_account.setdefault(acc_key, {})[pos_key] = pos_obj
                                    service.positions[pos_key] = pos_obj
                                except Exception as pos_err:
                                    logger.debug(f"[WATCHDOG][{acc_key}] Failed to create position from Redis {pos_key}: {pos_err}")
                        if redis_loaded:
                            account_positions = redis_loaded
                            logger.info(f"[WATCHDOG][{acc_key}] ✅ Loaded {len(account_positions)} positions from Redis (FRESH data)")
                    elif not account_positions:
                        logger.info(f"[WATCHDOG][{acc_key}] No positions in memory - checking files vs Redis...")
                        file_fresh, file_age = self.check_file_freshness(acc_key)
                        if redis_positions and redis_fresh and redis_age < file_age:
                            logger.info(f"[WATCHDOG][{acc_key}] ⚠️ Redis ({redis_age:.1f}s) is FRESHER than files ({file_age:.1f}s) - using Redis")
                            from ez_positions_service import Position
                            redis_loaded = {}
                            for pos_key, pos_data in redis_positions.items():
                                if isinstance(pos_data, dict) and pos_key.startswith(f"{acc_key}:"):
                                    try:
                                        pos_obj = Position.from_dict(pos_data)
                                        redis_loaded[pos_key] = pos_obj
                                        service.positions_by_account.setdefault(acc_key, {})[pos_key] = pos_obj
                                        service.positions[pos_key] = pos_obj
                                    except Exception as pos_err:
                                        logger.debug(f"[WATCHDOG][{acc_key}] Failed to create position from Redis {pos_key}: {pos_err}")
                            if redis_loaded:
                                account_positions = redis_loaded
                                logger.info(f"[WATCHDOG][{acc_key}] ✅ Loaded {len(account_positions)} positions from Redis")
                        else:
                            logger.info(f"[WATCHDOG][{acc_key}] ⚠️ Loading from JSON files (Redis age: {redis_age:.1f}s, file age: {file_age:.1f}s)")
                            try:
                                original_flag = ez_positions_service._positions_loaded_once_global
                                ez_positions_service._positions_loaded_once_global = False
                                service._positions_loaded_once = False
                                await service.load_account(acc_key, force=True)
                                ez_positions_service._positions_loaded_once_global = original_flag
                                account_positions = service.positions_by_account.get(acc_key, {})
                                if not account_positions:
                                    logger.error(f"[WATCHDOG][{acc_key}] ❌ Still no positions after force reload - trying manual load")
                                    from ez_positions_service import Position
                                    account_dir = Path(self.base_dir) / acc_key
                                    all_loaded = {}
                                    for side in ["LONG", "SHORT"]:
                                        file_path = account_dir / f"{side.lower()}_positions.json"
                                        if file_path.exists():
                                            with open(file_path, 'r') as f:
                                                file_positions = json.load(f)
                                            for pos_key, pos_data in file_positions.items():
                                                if isinstance(pos_data, dict) and pos_key.startswith(f"{acc_key}:"):
                                                    try:
                                                        pos_obj = Position.from_dict(pos_data)
                                                        all_loaded[pos_key] = pos_obj
                                                        service.positions_by_account.setdefault(acc_key, {})[pos_key] = pos_obj
                                                        service.positions[pos_key] = pos_obj
                                                    except Exception as pos_err:
                                                        logger.debug(f"[WATCHDOG][{acc_key}] Failed to create position {pos_key}: {pos_err}")
                                    account_positions = service.positions_by_account.get(acc_key, {})
                                if not account_positions:
                                    logger.error(f"[WATCHDOG][{acc_key}] ❌ Still no positions after loading from files - skipping")
                                    continue
                                logger.info(f"[WATCHDOG][{acc_key}] ✅ Loaded {len(account_positions)} positions from JSON files")
                            except Exception as load_err:
                                logger.error(f"[WATCHDOG][{acc_key}] ❌ Failed to load positions from files: {load_err}", exc_info=True)
                    all_positions_dict = {}
                    for pos_key, pos_obj in account_positions.items():
                        if pos_obj and hasattr(pos_obj, 'to_dict'):
                            all_positions_dict[pos_key] = pos_obj.to_dict()
                    if all_positions_dict:
                        if redis_positions:
                            merged_positions_dict = {}
                            redis_newer_count = 0
                            save_newer_count = 0
                            overwrite_blocked_count = 0
                            for pos_key, save_pos in all_positions_dict.items():
                                if not isinstance(save_pos, dict):
                                    merged_positions_dict[pos_key] = save_pos
                                    continue
                                redis_pos = redis_positions.get(pos_key)
                                if not redis_pos or not isinstance(redis_pos, dict):
                                    merged_positions_dict[pos_key] = save_pos
                                    continue
                                save_ts_str = save_pos.get("last_updated") or save_pos.get("updated_at") or save_pos.get("timestamp")
                                redis_ts_str = redis_pos.get("last_updated") or redis_pos.get("updated_at") or redis_pos.get("timestamp")
                                save_ts = None
                                redis_ts = None
                                try:
                                    if save_ts_str:
                                        if isinstance(save_ts_str, str):
                                            if 'T' in save_ts_str:
                                                save_ts = datetime.fromisoformat(save_ts_str.replace('Z', '+00:00'))
                                            else:
                                                save_ts = datetime.strptime(save_ts_str, '%Y-%m-%d %H:%M:%S.%f')
                                        elif isinstance(save_ts_str, (int, float)):
                                            save_ts = datetime.fromtimestamp(save_ts_str, tz=timezone.utc)
                                    if redis_ts_str:
                                        if isinstance(redis_ts_str, str):
                                            if 'T' in redis_ts_str:
                                                redis_ts = datetime.fromisoformat(redis_ts_str.replace('Z', '+00:00'))
                                            else:
                                                redis_ts = datetime.strptime(redis_ts_str, '%Y-%m-%d %H:%M:%S.%f')
                                        elif isinstance(redis_ts_str, (int, float)):
                                            redis_ts = datetime.fromtimestamp(redis_ts_str, tz=timezone.utc)
                                except Exception as ts_err:
                                    logger.debug(f"[WATCHDOG][{acc_key}] Failed to parse timestamp for {pos_key}: {ts_err}")
                                if redis_ts and save_ts:
                                    if redis_ts > save_ts:
                                        merged_positions_dict[pos_key] = redis_pos
                                        redis_newer_count += 1
                                        if redis_newer_count <= 3:
                                            logger.info(f"[WATCHDOG][{acc_key}] ✅ Redis NEWER: {pos_key} - Redis={redis_ts.isoformat()} vs Save={save_ts.isoformat()}")
                                    elif save_ts > redis_ts:
                                        merged_positions_dict[pos_key] = save_pos
                                        save_newer_count += 1
                                        if save_newer_count <= 3:
                                            logger.info(f"[WATCHDOG][{acc_key}] ✅ Save NEWER: {pos_key} - Save={save_ts.isoformat()} vs Redis={redis_ts.isoformat()}")
                                    else:
                                        merged_positions_dict[pos_key] = redis_pos
                                elif redis_ts:
                                    merged_positions_dict[pos_key] = redis_pos
                                    redis_newer_count += 1
                                elif save_ts:
                                    merged_positions_dict[pos_key] = save_pos
                                else:
                                    redis_amt = abs(safe_fetch_float(redis_pos.get("positionAmt") or redis_pos.get("pa"), 0.0))
                                    save_amt = abs(safe_fetch_float(save_pos.get("positionAmt") or save_pos.get("pa"), 0.0))
                                    if abs(redis_amt - save_amt) >= 0.000001:
                                        logger.warning(f"[WATCHDOG][{acc_key}] ⚠️ No timestamps for {pos_key} - using Redis (amt diff: {abs(redis_amt-save_amt):.6f})")
                                        merged_positions_dict[pos_key] = redis_pos
                                    else:
                                        merged_positions_dict[pos_key] = save_pos
                            if merged_positions_dict:
                                logger.info(f"[WATCHDOG][{acc_key}] 📊 Timestamp comparison: Redis newer={redis_newer_count}, Save newer={save_newer_count}")
                                from ez_positions_service import Position
                                merged_loaded = {}
                                for pos_key, pos_data in merged_positions_dict.items():
                                    if isinstance(pos_data, dict) and pos_key.startswith(f"{acc_key}:"):
                                        try:
                                            pos_obj = Position.from_dict(pos_data)
                                            merged_loaded[pos_key] = pos_obj
                                            service.positions_by_account.setdefault(acc_key, {})[pos_key] = pos_obj
                                            service.positions[pos_key] = pos_obj
                                        except Exception as pos_err:
                                            logger.debug(f"[WATCHDOG][{acc_key}] Failed to create position from merged {pos_key}: {pos_err}")
                                if merged_loaded:
                                    account_positions = merged_loaded
                                    all_positions_dict = {}
                                    for pos_key, pos_obj in account_positions.items():
                                        if pos_obj and hasattr(pos_obj, 'to_dict'):
                                            all_positions_dict[pos_key] = pos_obj.to_dict()
                                    logger.info(f"[WATCHDOG][{acc_key}] ✅ Merged {len(all_positions_dict)} positions using latest timestamps")
                        is_valid, mismatches = await self.validate_positionamt_before_save(acc_key, all_positions_dict)
                        if not is_valid and len(mismatches) > 0:
                            logger.info(f"[WATCHDOG][{acc_key}] positionAmt VALIDATION FAILED BEFORE SAVE - {len(mismatches)} mismatches:")
                            for mismatch in mismatches[:10]:
                                logger.info(f"[WATCHDOG][{acc_key}] 🚨 {mismatch}")
                            logger.info(f"[WATCHDOG][{acc_key}] ⚠️ PROCEEDING WITH SAVE ANYWAY - but data may be corrupted!")
                        else:
                            logger.info(f"[WATCHDOG][{acc_key}] ✅ positionAmt VALIDATION PASSED - all positionAmt values match API")
                        logger.info(f"[WATCHDOG][{acc_key}] 🚀🚀🚀 FORCING update: broadcasting {len(all_positions_dict)} positions to Redis and saving JSONs")
                        await service._broadcast_positions_to_redis(acc_key,all_positions_dict, allow_incomplete=True)
                        await atomic_save_positions(service, acc_key, force=True)
                        logger.info(f"[WATCHDOG][{acc_key}] ORCED update completed: {len(all_positions_dict)} positions broadcasted and saved")
                    else:
                        logger.warning(f"[WATCHDOG][{acc_key}] ⚠️ No position dicts to update")
                except Exception as acc_err:
                    logger.error(f"[WATCHDOG][{acc_key}] ❌ Error forcing update: {acc_err}", exc_info=True)
            return True
        except Exception as e:
            logger.error(f"[WATCHDOG] ❌ Error in force_update_positions_and_broadcast: {e}", exc_info=True)
            return False
            
    async def monitor_big_positions(self):
        """Monitor big positions every 5 seconds - check Redis and reduce when necessary"""
        if self.big_position_monitor_running:
            return
        self.big_position_monitor_running = True
        await asyncio.sleep(15)
        logger.warning(f"[BIG_POS_MONITOR] 🚨 Starting big position monitor (threshold=${self.big_position_threshold:.2f}, interval=5s)")
        while not self._shutdown_requested:
            try:
                now_dt = datetime.now(timezone.utc)
                for account_key in self.accounts:
                    try:
                        last_check = self.last_big_position_check.get(account_key, 0.0)
                        if time.time() - last_check < 5.0:
                            continue
                        self.last_big_position_check[account_key] = time.time()
                        positions = await self.get_positions_from_redis(account_key)
                        if not positions:
                            continue
                        big_positions = []
                        for pos_key, pos_data in positions.items():
                            if not isinstance(pos_data, dict):
                                continue
                            pos_value = self.get_position_value(pos_data)
                            if pos_value >= self.big_position_threshold:
                                position_amt = abs(safe_fetch_float(pos_data.get("positionAmt") or pos_data.get("pa"), 0.0))
                                if position_amt <= 0:
                                    continue
                                big_positions.append((pos_key, pos_data, pos_value))
                        if not big_positions:
                            continue
                        logger.warning(f"[BIG_POS_MONITOR][{account_key}] Found {len(big_positions)} big positions")
                        for position_key, position, position_value in big_positions:
                            try:
                                symbol = position.get("symbol") or position.get("s", "")
                                if not symbol:
                                    continue
                                gain = safe_fetch_float(position.get("gain") or position.get("unrealizedProfit", 0.0), 0.0)
                                position_amt = safe_fetch_float(position.get("positionAmt") or position.get("pa"), 0.0)
                                if abs(position_amt) <= 0:
                                    continue
                                position_side = (position.get("positionSide") or position.get("ps") or ("LONG" if position_amt >= 0 else "SHORT")).upper()
                                is_long = position_side == "LONG"
                                mark_price = safe_fetch_float(position.get("mark_price") or position.get("markPrice"), 0.0)
                                if mark_price <= 0:
                                    continue
                                last_reduction_time = position.get("last_reduction_time")
                                if last_reduction_time:
                                    try:
                                        if isinstance(last_reduction_time, str):
                                            last_red_dt = datetime.fromisoformat(last_reduction_time.replace('Z', '+00:00'))
                                        else:
                                            last_red_dt = last_reduction_time
                                        if last_red_dt.tzinfo is None:
                                            last_red_dt = last_red_dt.replace(tzinfo=timezone.utc)
                                        minutes_since_red = (now_dt - last_red_dt).total_seconds() / 60.0
                                        if minutes_since_red < 3.0:
                                            continue
                                    except Exception:
                                        pass
                                should_reduce = False
                                reduce_reason = ""
                                if gain < 0.2:
                                    prev_gain_raw = position.get("prev_gain")
                                    if prev_gain_raw is not None:
                                        prev_gain = safe_fetch_float(prev_gain_raw, gain)
                                        if gain < prev_gain:
                                            should_reduce = True
                                            reduce_reason = f"EMERGENCY_GAIN_LOW gain={gain:.2f}% < 0.2% deteriorating (prev={prev_gain:.2f}%)"
                                    else:
                                        should_reduce = True
                                        reduce_reason = f"EMERGENCY_GAIN_LOW gain={gain:.2f}% < 0.2% (no prev_gain)"
                                elif gain < 0.5 and position_value > self.big_position_threshold * 2:
                                    should_reduce = True
                                    reduce_reason = f"BIG_POS_LOW_GAIN gain={gain:.2f}% value=${position_value:.2f}"
                                if should_reduce:
                                    await self.reduce_big_position(position_key, account_key, position, reduce_reason)
                            except Exception as pos_err:
                                logger.debug(f"[BIG_POS_MONITOR] Error checking {position_key}: {pos_err}")
                    except Exception as acc_err:
                        logger.error(f"[BIG_POS_MONITOR] Error for {account_key}: {acc_err}", exc_info=True)
                await asyncio.sleep(5.0)
            except Exception as e:
                logger.error(f"[BIG_POS_MONITOR] Error in loop: {e}", exc_info=True)
                await asyncio.sleep(5.0)

    def check_universe_freshness(self) -> Tuple[bool, float, int]:
        """
        Monitors if cleanup_positions is running by checking tradeable_keys.json freshness.
        Returns: (is_fresh, age, key_count)
        """
        universe_file = self.base_dir / "tradeable_keys.json"
        
        # 1. Check File Existence
        if not universe_file.exists():
            return False, 999.0, 0

        try:
            # 2. Check File Age (Modification Time)
            stat = universe_file.stat()
            age = time.time() - stat.st_mtime
            
            # 3. Check Content (Is it empty?)
            if stat.st_size < 10: # Too small to contain valid JSON list
                return False, age, 0
                
            # Optional: Quick peek at count without parsing everything if file is huge
            # But parsing is safer to ensure valid JSON
            with open(universe_file, 'r') as f:
                data = json.load(f)
                count = len(data) if isinstance(data, (list, dict)) else 0
                
            # Threshold: 3 minutes (180s). Cleanup usually runs every 60s.
            is_fresh = age < 180.0
            
            return is_fresh, age, count
            
        except Exception as e:
            logger.error(f"[WATCHDOG] Error checking universe freshness: {e}")
            return False, 999.0, 0

    async def check_and_act_for_account(self, account_key: str):
        """Monitor and act for ONE specific account - runs in parallel with other accounts"""
        killed = self.kill_duplicate_fetchers(account_key)
        if killed > 0:
            await asyncio.sleep(2.0)
        fetcher_running = self.is_fetcher_running(account_key=account_key)
        fetcher_working, fetcher_activity_age = self.is_fetcher_working(account_key=account_key)
        ez_manage_running = self.is_ez_manage_running()
        account_backup_running = self.is_account_backup_running(account_key)
        fetcher_start_time = self.fetcher_start_times.get(account_key, 0.0)
        in_grace_period = (time.time() - fetcher_start_time) < self.fetcher_startup_grace_period if fetcher_start_time > 0 else False
            
        # CRITICAL: Quick script monitoring logic with grace period
        if account_key in self.quick_accounts:
            quick_running = self.is_quick_running(account_key)
            quick_ok, quick_age = self.is_quick_working(account_key)
            
            # Check grace period
            quick_start_time = self.quick_start_times.get(account_key, 0.0)
            in_quick_grace = (time.time() - quick_start_time) < self.quick_grace_period if quick_start_time > 0 else False
            
            if not quick_running:
                # If not running, data providers must be up
                data_providers_running = any(self.is_fetcher_running(account_key=acc) for acc in self.accounts)
                if data_providers_running:
                    logger.info(f"[WATCHDOG][{account_key}] 🚨 ez_positions_quick.py not running - starting NOW")
                    if self.start_quick(account_key):
                        logger.info(f"[WATCHDOG][{account_key}] ✅ ez_positions_quick.py started")
            
            elif quick_running and not quick_ok and not in_quick_grace:
                # Running but hung/silent AND grace period over
                reason = f"SILENT_HANG ({quick_age:.1f}s)"
                logger.info(f"[WATCHDOG][{account_key}] 🚨 ez_positions_quick.py {reason} - RESTARTING NOW")
                self.stop_quick(account_key)
                await asyncio.sleep(1.0)
                self.start_quick(account_key)
            
            elif quick_running and not quick_ok and in_quick_grace:
                logger.info(f"[WATCHDOG][{account_key}] ⏳ ez_positions_quick.py initializing (grace period: {time.time()-quick_start_time:.1f}s/{self.quick_grace_period}s)")

        univ_fresh, univ_age, univ_count = self.check_universe_freshness()
        
        if not univ_fresh and univ_age > 980.0:
            # Only restart if WE are running the fetcher (don't kill manual processes unless necessary)
            if fetcher_running:
                logger.critical(f"[WATCHDOG][{account_key}] 🚨 UNIVERSE STALE (Age: {univ_age:.1f}s). 'cleanup_positions' likely dead.")
                logger.critical(f"[WATCHDOG][{account_key}] 💀 FORCE RESTARTING ez_positions.py to repopulate tradeable keys.")
                
                self.stop_fetcher(account_key=account_key)
                await asyncio.sleep(2.0)
                fetcher_running = False 
        elif univ_count == 0 and fetcher_running and not in_grace_period:
             logger.warning(f"[WATCHDOG][{account_key}] ⚠️ Tradeable Universe is EMPTY (Count: 0). Cleanup may be failing.")
        univ_status = f"univ={'✅' if univ_fresh else f'❌({univ_age:.0f}s)'}[{univ_count}]"
        redis_fresh, redis_age, redis_count = await self.check_redis_freshness(account_key)
        
        if redis_age > 60.0:
            logger.info(f"[WATCHDOG][{account_key}] 🚨 REDIS STALE ({redis_age:.1f}s) - Cycling fetcher instance")
            self.stop_fetcher(account_key)
            await asyncio.sleep(1.0)
            await self.start_fetcher(account_key)
            self.fetcher_start_times[account_key] = time.time()

        if fetcher_running and not fetcher_working and not in_grace_period: # <-- ADDED `not in_grace_period`
            redis_fresh_check, redis_age_check, _ = await self.check_redis_freshness(account_key)
            failure_count = self.fetcher_failure_count.get(account_key, 0)
            if fetcher_activity_age > 150.0 and not redis_fresh_check and redis_age_check > (self.redis_stale_threshold * 1.5):
                if failure_count > 0:
                    logger.warning(f"[WATCHDOG][{account_key}] ⚠️ Previous failures: {failure_count}, but RESTARTING - fetcher not working")
                logger.info(f"[WATCHDOG][{account_key}] Fetcher process running but NOT WORKING (activity {fetcher_activity_age:.1f}s, Redis {redis_age_check:.1f}s) - KILLING and RESTARTING")
                self.stop_fetcher(account_key=account_key)
                self._primary_fetcher_running = False
                await asyncio.sleep(2.0)
                fetcher_running = False
            else:
                logger.warning(f"[WATCHDOG][{account_key}] ⚠️ Fetcher running but no recent activity ({fetcher_activity_age:.1f}s) - monitoring (Redis {'✅' if redis_fresh_check else f'❌({redis_age_check:.1f}s)'})")
        elif fetcher_running and in_grace_period:
            logger.debug(f"[WATCHDOG][{account_key}] ⏸️ Fetcher in grace period ({time.time() - fetcher_start_time:.1f}s / {self.fetcher_startup_grace_period}s) - not checking activity yet")
            
        if self.is_backup_running():
            logger.warning(f"[WATCHDOG][{account_key}] ⏸️ TEMPORARILY DISABLED - Stopping backup scripts")
            self.stop_backups()
        if account_backup_running:
            logger.warning(f"[WATCHDOG][{account_key}] ⏸️ TEMPORARILY DISABLED - Stopping account backup scripts")
            self.stop_account_backup(account_key)
        if self.is_active_running():
            logger.warning(f"[WATCHDOG][{account_key}] ⏸️ TEMPORARILY DISABLED - Stopping active positions script")
            self.stop_active()
        if self.is_realtime_running(account_key):
            logger.warning(f"[WATCHDOG][{account_key}] ⏸️ TEMPORARILY DISABLED - Stopping realtime script")
            self.stop_realtime(account_key)
        
        realtime_running = False
        
        if not fetcher_running:
            failure_count = self.fetcher_failure_count.get(account_key, 0)
            if failure_count > 0:
                last_error = self.fetcher_last_error.get(account_key, "Unknown")
                logger.warning(f"[WATCHDOG][{account_key}] ⚠️ Previous failures: {failure_count}, but RESTARTING ANYWAY - system must keep fetching. Last error: {last_error}")
            logger.info(f"[WATCHDOG][{account_key}] 🚨 ez_positions.py not running for {account_key} - starting SEPARATE instance NOW")
            if await self.start_fetcher(account_key=account_key, force_kill=False):
                self._primary_fetcher_running = True
                self.fetcher_start_times[account_key] = time.time()
                logger.info(f"[WATCHDOG][{account_key}] ✅ ez_positions.py started for {account_key} - independent instance with ONE fetch loop & ONE websocket (grace period: {self.fetcher_startup_grace_period}s)")
                await asyncio.sleep(9.0)
                fetcher_still_running = self.is_fetcher_running(account_key=account_key)
                if not fetcher_still_running:
                    logger.error(f"[WATCHDOG][{account_key}] CRITICAL: Fetcher process died immediately after start! Check ~/logs/ez_positions.log for startup errors")
                    try:
                        log_file = Path.home() / "logs" / "ez_positions.log"
                        if log_file.exists():
                            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                lines = f.readlines()
                                last_lines = lines[-20:] if len(lines) > 20 else lines
                                logger.error(f"[WATCHDOG][{account_key}] Last 20 lines of ez_positions.log:")
                                for line in last_lines:
                                    if account_key.lower() in line.lower() or 'error' in line.lower() or 'exception' in line.lower() or 'traceback' in line.lower():
                                        logger.error(f"[WATCHDOG][{account_key}] {line.strip()}")
                    except Exception as log_err:
                        logger.debug(f"[WATCHDOG][{account_key}] Could not read log file: {log_err}")
                else:
                    logger.warning(f"[WATCHDOG][{account_key}] etcher verified running after 3s")
                fetcher_running = fetcher_still_running
        elif fetcher_running:
            self._primary_fetcher_running = True
            logs_dir = Path.home() / "logs"
            log_file = logs_dir / "ez_positions_service.log"
            last_fetch_age = 999.0
            if log_file.exists():
                try:
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        for line in reversed(lines[-1000:]):
                            if (f"[FETCHFETCH]" in line and account_key in line) or (f"[fetch_positions][{account_key}]" in line and ("Fetched" in line or "API RETURNED" in line or "CALLING API" in line or "Fetched {len" in line)):
                                try:
                                    if ']' in line:
                                        timestamp_str = line.split(']')[0].replace('[', '').strip()
                                        dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                        last_fetch_age = (datetime.now() - dt).total_seconds()
                                        break
                                except Exception:
                                    pass
                except Exception:
                    pass
            if last_fetch_age > 8.0:
                logger.info(f"[WATCHDOG][{account_key}] FORCING FETCH - last fetch was {last_fetch_age:.1f}s ago (>10s threshold)")
                try:
                    from ez_positions_service import (bootstrap_position_service, load_accounts_from_config)
                    if account_key not in self.cached_services:
                        config = Config()
                        accounts = await load_accounts_from_config(config, logger)
                        if account_key in accounts:
                            self.cached_services[account_key] = await bootstrap_position_service(logger=logger, accounts={account_key: accounts[account_key]}, enable_auto_fetch=True, load_priority='disk')
                    if account_key in self.cached_services:
                        service = self.cached_services[account_key]
                        await service.fetch_positions(account_key)
                        logger.info(f"[WATCHDOG][{account_key}] ORCED FETCH COMPLETED")
                except Exception as force_err:
                    logger.error(f"[WATCHDOG][{account_key}] ❌ Failed to force fetch: {force_err}", exc_info=True)
            last_ws_age = 999.0
            if log_file.exists():
                try:
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        for line in reversed(lines[-500:]):
                            if f"[{account_key}]" in line and ("ACCOUNT_UPDATE" in line or "handle_account_update" in line or "WS_ARRIVAL" in line or "WebSocket" in line or "websocket" in line or "WS_" in line or "_broadcast_and_save" in line or "Broadcasted" in line):
                                try:
                                    if ']' in line:
                                        timestamp_str = line.split(']')[0].replace('[', '').strip()
                                        dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                        last_ws_age = (datetime.now() - dt).total_seconds()
                                        if "ACCOUNT_UPDATE" in line or "handle_account_update" in line or "WS_ARRIVAL" in line:
                                            break
                                except Exception:
                                    pass
                except Exception:
                    pass
            if last_ws_age > 60.0:
                logger.info(f"[WATCHDOG][{account_key}] WEBSOCKET STALE - last activity was {last_ws_age:.1f}s ago (>60s threshold) - RESTARTING FETCHER")
                if self.is_fetcher_running(account_key=account_key):
                    logger.info(f"[WATCHDOG][{account_key}] 🛑 Stopping fetcher to restart websocket...")
                    self.stop_fetcher(account_key=account_key)
                    await asyncio.sleep(2.0)
                if await self.start_fetcher(account_key=account_key, force_kill=False):
                    logger.info(f"[WATCHDOG][{account_key}] ETCHER RESTARTED (websocket should reconnect)")
                    self.fetcher_start_times[account_key] = time.time()
        
        redis_save_ok, json_save_ok, redis_save_age, json_save_age = self.check_save_activity(account_key)
        if not redis_save_ok:
            logger.info(f"[WATCHDOG][{account_key}] NO REDIS SAVES DETECTED in last {redis_save_age:.1f}s - positions may not be saved to Redis!")
        if not json_save_ok:
            logger.info(f"[WATCHDOG][{account_key}] NO JSON SAVES DETECTED in last {json_save_age:.1f}s - positions may not be saved to JSON files!")
        if not redis_save_ok or not json_save_ok:
            logger.info(f"[WATCHDOG][{account_key}] SAVE MONITORING: Redis={'✅' if redis_save_ok else f'❌({redis_save_age:.1f}s)'} JSON={'✅' if json_save_ok else f'❌({json_save_age:.1f}s)'}")
        logs_dir = Path.home() / "logs"
        log_file = logs_dir / "ez_positions_service.log"
        last_fetch_age = 999.0
        if log_file.exists():
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    for line in reversed(lines[-1000:]):
                        if (f"[FETCHFETCH]" in line and account_key in line) or (f"[fetch_positions][{account_key}]" in line and ("Fetched" in line or "API RETURNED" in line or "CALLING API" in line)):
                            try:
                                if ']' in line:
                                    timestamp_str = line.split(']')[0].replace('[', '').strip()
                                    dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                    last_fetch_age = (datetime.now() - dt).total_seconds()
                                    break
                            except Exception:
                                pass
            except Exception:
                pass
        if last_fetch_age > 8.0:
            logger.info(f"[WATCHDOG][{account_key}] FORCING FETCH - last fetch was {last_fetch_age:.1f}s ago (>10s threshold)")
            try:
                from ez_positions_service import (bootstrap_position_service, load_accounts_from_config)
                if account_key not in self.cached_services:
                    config = Config()
                    accounts = await load_accounts_from_config(config, logger)
                    if account_key in accounts:
                        self.cached_services[account_key] = await bootstrap_position_service(logger=logger, accounts={account_key: accounts[account_key]}, enable_auto_fetch=True, load_priority='disk')
                if account_key in self.cached_services:
                    service = self.cached_services[account_key]
                    await service.fetch_positions(account_key)
                    logger.info(f"[WATCHDOG][{account_key}] ORCED FETCH COMPLETED")
            except Exception as force_err:
                logger.error(f"[WATCHDOG][{account_key}] ❌ Failed to force fetch: {force_err}", exc_info=True)
        last_ws_age = 999.0
        if log_file.exists():
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    for line in reversed(lines[-500:]):
                        if f"[{account_key}]" in line and ("WebSocket" in line or "websocket" in line or "ACCOUNT_UPDATE" in line or "handle_account_update" in line or "WS_" in line or "_broadcast_and_save" in line or "Broadcasted" in line):
                            try:
                                if ']' in line:
                                    timestamp_str = line.split(']')[0].replace('[', '').strip()
                                    dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                    last_ws_age = (datetime.now() - dt).total_seconds()
                                    if "ACCOUNT_UPDATE" in line or "handle_account_update" in line:
                                        break
                            except Exception:
                                pass
            except Exception:
                pass
        if last_ws_age > 60.0:
            logger.info(f"[WATCHDOG][{account_key}] WEBSOCKET STALE - last activity was {last_ws_age:.1f}s ago (>60s threshold) - RESTARTING FETCHER")
            if self.is_fetcher_running(account_key=account_key):
                logger.info(f"[WATCHDOG][{account_key}] 🛑 Stopping fetcher to restart websocket...")
                self.stop_fetcher(account_key=account_key)
                await asyncio.sleep(2.0)
            if await self.start_fetcher(account_key=account_key, force_kill=False):
                logger.info(f"[WATCHDOG][{account_key}] ETCHER RESTARTED (websocket should reconnect)")
                self.fetcher_start_times[account_key] = time.time()
        file_fresh, file_age = self.check_file_freshness(account_key)
        redis_fresh, redis_age, redis_count = await self.check_redis_freshness(account_key)
        redis_positions = await self.get_positions_from_redis(account_key)
        stale_write_detected = False
        if redis_positions and file_fresh:
            try:
                account_dir = self.base_dir / account_key
                long_file = account_dir / "long_positions.json"
                if long_file.exists():
                    with open(long_file, 'r', encoding='utf-8') as f:
                        file_data = json.load(f)
                        if isinstance(file_data, dict):
                            stale_count = 0
                            for pos_key, file_pos in file_data.items():
                                redis_pos = redis_positions.get(pos_key)
                                if redis_pos and isinstance(redis_pos, dict) and isinstance(file_pos, dict):
                                    redis_ts_str = redis_pos.get('last_updated') or redis_pos.get('meta', {}).get('timestamp')
                                    file_ts_str = file_pos.get('last_updated')
                                    if redis_ts_str and file_ts_str:
                                        try:
                                            if isinstance(redis_ts_str, str):
                                                redis_ts = datetime.fromisoformat(redis_ts_str.replace('Z', '+00:00'))
                                            else:
                                                redis_ts = redis_ts_str
                                            if redis_ts.tzinfo is None:
                                                redis_ts = redis_ts.replace(tzinfo=timezone.utc)
                                            if isinstance(file_ts_str, str):
                                                file_ts = datetime.fromisoformat(file_ts_str.replace('Z', '+00:00'))
                                            else:
                                                file_ts = file_ts_str
                                            if file_ts.tzinfo is None:
                                                file_ts = file_ts.replace(tzinfo=timezone.utc)
                                            age_diff = (redis_ts - file_ts).total_seconds()
                                            if age_diff > 20.0:
                                                stale_count += 1
                                                if stale_count <= 5:
                                                    logger.info(f"[WATCHDOG][{account_key}] STALE WRITE DETECTED: {pos_key} - file={file_ts.isoformat()}, redis={redis_ts.isoformat()} - FILE IS OLDER THAN REDIS (stale data written)!")
                                        except Exception as comp_err:
                                            logger.debug(f"[WATCHDOG][{account_key}] Comparison error: {comp_err}")
                            if stale_count > 0:
                                stale_write_detected = True
                                logger.info(f"[WATCHDOG][{account_key}] CRITICAL: STALE WRITE DETECTED - {stale_count} positions in files are OLDER than Redis! This means stale data was written over fresh data!")
            except Exception as comp_err:
                logger.debug(f"[WATCHDOG][{account_key}] File comparison error: {comp_err}")
        validation_passed = False
        logs_dir = Path.home() / "logs"
        log_file = logs_dir / "ez_positions_service.log"
        redis_positions = await self.get_positions_from_redis(account_key)
        account_dir = self.base_dir / account_key
        long_file = account_dir / "long_positions.json"
        short_file = account_dir / "short_positions.json"
        json_positions = {}
        if long_file.exists():
            try:
                with open(long_file, 'r', encoding='utf-8') as f:
                    long_data = json.load(f)
                    if isinstance(long_data, dict):
                        json_positions.update(long_data)
            except Exception:
                pass
        if short_file.exists():
            try:
                with open(short_file, 'r', encoding='utf-8') as f:
                    short_data = json.load(f)
                    if isinstance(short_data, dict):
                        json_positions.update(short_data)
            except Exception:
                pass
        if log_file.exists() and (redis_positions or json_positions):
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    for line in reversed(lines[-1000:]):
                        if f"[FETCHFETCH]" in line and account_key in line and "Fetched" in line and "positions from API" in line:
                            try:
                                if ']' in line:
                                    timestamp_str = line.split(']')[0].replace('[', '').strip()
                                    dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                    age = (datetime.now() - dt).total_seconds()
                                    if age < 300.0:
                                        if 'positions from API:' in line:
                                            fetch_part = line.split('positions from API:')[1].strip()
                                            entries = fetch_part.split(',')
                                            matched_redis = 0
                                            matched_json = 0
                                            checked_count = 0
                                            for entry in entries[:50]:
                                                entry = entry.strip()
                                                if ':' in entry and entry.count(':') >= 2:
                                                    parts = entry.split(':')
                                                    if len(parts) >= 3:
                                                        try:
                                                            symbol = parts[0].strip()
                                                            side = parts[1].strip().upper()
                                                            fetch_amt = abs(float(parts[2].strip()))
                                                            checked_count += 1
                                                            pos_key = f"{account_key}:{symbol}_{side}"
                                                            redis_pos = redis_positions.get(pos_key) if redis_positions else None
                                                            if redis_pos:
                                                                redis_amt = abs(float(redis_pos.get('positionAmt') or redis_pos.get('pa') or 0))
                                                                if abs(fetch_amt - redis_amt) < 0.000001:
                                                                    matched_redis += 1
                                                                else:
                                                                    logger.info(f"[WATCHDOG][{account_key}] positionAmt MISMATCH REDIS: {pos_key} - API(ABS)={fetch_amt:.6f}, Redis={redis_amt:.6f}, diff={abs(fetch_amt-redis_amt):.6f}")
                                                                    logger.info(f"[WATCHDOG][{account_key}] This means data was CORRUPTED between API fetch and Redis write!")
                                                            json_pos = json_positions.get(pos_key) if json_positions else None
                                                            if json_pos:
                                                                json_amt = abs(float(json_pos.get('positionAmt') or json_pos.get('pa') or 0))
                                                                if abs(fetch_amt - json_amt) < 0.000001:
                                                                    matched_json += 1
                                                                else:
                                                                    logger.info(f"[WATCHDOG][{account_key}] positionAmt MISMATCH JSON: {pos_key} - API(ABS)={fetch_amt:.6f}, JSON={json_amt:.6f}, diff={abs(fetch_amt-json_amt):.6f}")
                                                                    logger.info(f"[WATCHDOG][{account_key}] This means data was CORRUPTED between API fetch and JSON write!")
                                                        except (ValueError, IndexError):
                                                            continue
                                            if checked_count > 0:
                                                redis_match_pct = (matched_redis / checked_count * 100) if checked_count > 0 else 0
                                                json_match_pct = (matched_json / checked_count * 100) if checked_count > 0 else 0
                                                if matched_redis == checked_count and matched_json == checked_count:
                                                    validation_passed = True
                                                    logger.debug(f"[WATCHDOG][{account_key}] ✅ positionAmt VALIDATION PASSED: Redis={matched_redis}/{checked_count} JSON={matched_json}/{checked_count} (100% match)")
                                                else:
                                                    logger.warning(f"[WATCHDOG][{account_key}] ⚠️ positionAmt VALIDATION FAILED: Redis={matched_redis}/{checked_count} ({redis_match_pct:.1f}%) JSON={matched_json}/{checked_count} ({json_match_pct:.1f}%)")
                                            break
                            except Exception as parse_err:
                                logger.debug(f"[WATCHDOG][{account_key}] Error parsing FETCHFETCH: {parse_err}")
            except Exception as e:
                logger.debug(f"[WATCHDOG][{account_key}] Error checking validation: {e}")
        is_healthy = redis_fresh and redis_count > 0 and redis_age < self.redis_stale_threshold and not stale_write_detected and validation_passed and redis_save_ok and json_save_ok
        status_parts = [
            f"fetcher={'✅' if fetcher_running else '❌'}",
            f"validation={'✅' if validation_passed else '❌'}",
            f"working={'✅' if fetcher_working else f'❌({fetcher_activity_age:.1f}s)'}",
            f"redis_save={'✅' if redis_save_ok else f'❌({redis_save_age:.1f}s)'}",
            f"json_save={'✅' if json_save_ok else f'❌({json_save_age:.1f}s)'}",
            f"file={'✅' if file_fresh else f'❌({file_age:.1f}s)'}",
            f"redis={'✅' if redis_fresh else f'❌({redis_age:.1f}s)'}",
            f"count={redis_count}"
            f"keys={univ_count if 'univ_count' in locals() else '?'}" # Add this

        ]
        logger.info(f"[WATCHDOG][{account_key}] {'✅ HEALTHY' if is_healthy else '🚨 STALE'} - {', '.join(status_parts)}")
        if not is_healthy:
            fetcher_start_time = self.fetcher_start_times.get(account_key, 0.0)
            in_grace_period = (time.time() - fetcher_start_time) < self.fetcher_startup_grace_period if fetcher_start_time > 0 else False
            if in_grace_period:
                logger.debug(f"[WATCHDOG][{account_key}] ⏸️ Fetcher in grace period ({time.time() - fetcher_start_time:.1f}s / {self.fetcher_startup_grace_period}s) - Redis stale {redis_age:.1f}s but not restarting yet")
            elif not redis_fresh and redis_age > (self.redis_stale_threshold * 2):
                failure_count = self.fetcher_failure_count.get(account_key, 0)
                if failure_count > 0:
                    logger.warning(f"[WATCHDOG][{account_key}] ⚠️ Previous failures: {failure_count}, but RESTARTING - Redis stale {redis_age:.1f}s")
                if fetcher_running:
                    logger.info(f"[WATCHDOG][{account_key}] Redis REALLY stale ({redis_age:.1f}s > {self.redis_stale_threshold * 2}s) - RESTARTING FETCHER NOW")
                    self.stop_fetcher(account_key=account_key)
                    await asyncio.sleep(2.0)
                    if await self.start_fetcher(account_key=account_key, force_kill=True):
                        self.fetcher_start_times[account_key] = time.time()
                    fetcher_running = True
                    await asyncio.sleep(3.0)
                elif not fetcher_running:
                    logger.info(f"[WATCHDOG][{account_key}] 🚨 No fetcher running - starting NOW")
                    if await self.start_fetcher(account_key=account_key, force_kill=False):
                        self.fetcher_start_times[account_key] = time.time()
                        fetcher_running = True
                        await asyncio.sleep(3.0)
            elif not redis_fresh and redis_age <= (self.redis_stale_threshold * 2):
                logger.warning(f"[WATCHDOG][{account_key}] ⚠️ Redis slightly stale ({redis_age:.1f}s) - monitoring (not restarting to avoid flapping)")
            if not redis_save_ok or not json_save_ok:
                last_force = self.last_force_fetch.get(account_key, 0.0)
                if time.time() - last_force >= self.force_fetch_cooldown:
                    logger.info(f"[WATCHDOG][{account_key}] SAVES NOT HAPPENING - forcing update: Redis={'❌' if not redis_save_ok else '✅'} JSON={'❌' if not json_save_ok else '✅'}")
                    try:
                        await self.force_update_positions_and_broadcast(account_key)
                        logger.info(f"[WATCHDOG][{account_key}] ORCED save completed - Redis and JSON should now be updated")
                    except Exception as force_err:
                        logger.error(f"[WATCHDOG][{account_key}] ❌ Failed to force save: {force_err}", exc_info=True)
                else:
                    logger.debug(f"[WATCHDOG][{account_key}] ⏸️ Saves not happening but cooldown active ({time.time() - last_force:.1f}s < {self.force_fetch_cooldown}s)")
            elif not redis_fresh and redis_age > 60.0:
                last_force = self.last_force_fetch.get(account_key, 0.0)
                if time.time() - last_force >= self.force_fetch_cooldown:
                    logger.info(f"[WATCHDOG][{account_key}] Redis STALE ({redis_age:.1f}s) - forcing reload and update")
                    try:
                        await self.force_update_positions_and_broadcast(account_key)
                        logger.info(f"[WATCHDOG][{account_key}] ORCED reload completed - Redis and JSON should now be updated")
                    except Exception as force_err:
                        logger.error(f"[WATCHDOG][{account_key}] ❌ Failed to force reload: {force_err}", exc_info=True)
                else:
                    logger.debug(f"[WATCHDOG][{account_key}] ⏸️ Redis stale but cooldown active ({time.time() - last_force:.1f}s < {self.force_fetch_cooldown}s)")
        else:
            if file_fresh and fetcher_running and self._primary_fetcher_running:
                logger.debug(f"[WATCHDOG][{account_key}] ✅ ez_positions.py healthy - keeping it running")
                if ez_manage_running:
                    self.disable_internal_fetcher()
                if account_backup_running:
                    logger.warning(f"[WATCHDOG][{account_key}] ✅ Realtime healthy - stopping account backups")
                    self.stop_account_backup(account_key)
                if self.is_backup_running():
                    logger.warning(f"[WATCHDOG] ✅ Realtime healthy - stopping main backup script")
                    self.stop_backups()
                if self.is_active_running():
                    logger.warning(f"[WATCHDOG] ✅ Realtime healthy - stopping active positions script")
                    self.stop_active()
    
    async def watchdog_loop_for_account(self, account_key: str):
        """Dedicated watchdog loop for ONE account - runs in parallel"""
        logger.warning(f"[WATCHDOG][{account_key}] 🐕 Starting dedicated watchdog for account")
        while not self._shutdown_requested:
            try:
                self._reap_zombies()
                await self.check_and_act_for_account(account_key)
                await asyncio.sleep(self.check_interval)
            except KeyboardInterrupt:
                logger.warning(f"[WATCHDOG][{account_key}] Shutdown requested")
                break
            except Exception as e:
                logger.error(f"[WATCHDOG][{account_key}] Error in loop: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)
    
    async def check_and_act(self):
        """Main monitoring and action loop - DEPRECATED: Use check_and_act_for_account instead"""
        logger.warning("[WATCHDOG] ⚠️ check_and_act() called - should use watchdog_loop_for_account() instead")
        await self.check_and_act_for_account(self.accounts[0] if self.accounts else "unknown")
    
    async def run(self):
        """Main watchdog loop - starts parallel watchdog instances"""
        logger.warning(f"[WATCHDOG] 🐕 Starting comprehensive position watchdog for {len(self.accounts)} accounts")
        logger.warning(f"[WATCHDOG] 🚀🚀🚀 Creating {len(self.accounts)} parallel watchdog instances: {self.accounts}")
        
        watchdog_tasks = []
        for account_key in self.accounts:
            logger.warning(f"[WATCHDOG] 🚀 Creating watchdog task for {account_key}...")
            task = asyncio.create_task(self.watchdog_loop_for_account(account_key))
            watchdog_tasks.append((account_key, task))
            logger.warning(f"[WATCHDOG] ✅ Watchdog task created for {account_key}: {task}")
        
        await asyncio.sleep(0.3)
        running = sum(1 for _, task in watchdog_tasks if not task.done())
        logger.info(f"[WATCHDOG] ✅✅✅ VERIFIED: {running}/{len(watchdog_tasks)} watchdog instances running in PARALLEL")
        
        logger.info(f"[WATCHDOG] STARTING ALL 5 ACCOUNTS IN PARALLEL: {self.accounts} (each with fetch loop every 4s + websocket 24/7)")
        startup_tasks = []
        for account_key in self.accounts:
            if not self.is_fetcher_running(account_key=account_key):
                logger.info(f"[WATCHDOG] 🚀 Starting ez_positions.py for {account_key} (parallel mode)...")
                startup_tasks.append(asyncio.create_task(self.start_fetcher(account_key=account_key, force_kill=False)))
            else:
                logger.warning(f"[WATCHDOG] ⏸️ {account_key} already running - skipping immediate start")
        if startup_tasks:
            results = await asyncio.gather(*startup_tasks, return_exceptions=True)
            for account_key, result in zip(self.accounts, results):
                if isinstance(result, Exception):
                    logger.error(f"[WATCHDOG] ❌ Failed to start {account_key}: {result}")
                elif result:
                    logger.info(f"[WATCHDOG] ✅✅✅ {account_key} started successfully")
                    self.fetcher_start_times[account_key] = time.time()
                else:
                    logger.warning(f"[WATCHDOG] ⚠️ {account_key} start returned False")
        await asyncio.sleep(3.0)
        for account_key in self.accounts:
            if self.is_fetcher_running(account_key=account_key):
                logger.info(f"[WATCHDOG] ✅✅✅ VERIFIED: {account_key} is RUNNING")
            else:
                logger.error(f"[WATCHDOG] ❌❌❌ CRITICAL: {account_key} is NOT RUNNING")
        
        data_providers_running = any(self.is_fetcher_running(account_key=acc) for acc in self.accounts)
        logger.warning(f"[WATCHDOG] Data providers running: {data_providers_running} for accounts: {self.accounts}")
        if not data_providers_running:
            logger.warning(f"[WATCHDOG] ⚠️ Data providers not running yet - will start ez_positions_quick.py once they're available")
        
        for quick_account in self.quick_accounts:
            quick_running = self.is_quick_running(quick_account)
            quick_working, quick_activity_age = self.is_quick_working(quick_account)
            if not quick_running and data_providers_running:
                logger.info(f"[WATCHDOG] 🚨 ez_positions_quick.py not running for {quick_account} - starting NOW")
                if self.start_quick(quick_account):
                    logger.info(f"[WATCHDOG] ✅✅✅ ez_positions_quick.py started for {quick_account}")
                else:
                    logger.error(f"[WATCHDOG] ❌ Failed to start ez_positions_quick.py for {quick_account}")
            elif quick_running and not quick_working and quick_activity_age > 180.0:
                logger.info(f"[WATCHDOG] ez_positions_quick.py running but HANGING for {quick_account} - RESTARTING")
                self.stop_quick(quick_account)
                await asyncio.sleep(2.0)
                if data_providers_running:
                    if self.start_quick(quick_account):
                        logger.info(f"[WATCHDOG] ✅✅✅ ez_positions_quick.py restarted for {quick_account}")
            elif quick_running and quick_working:
                logger.info(f"[WATCHDOG] ✅ ez_positions_quick.py running and working for {quick_account}")
            else:
                logger.warning(f"[WATCHDOG] ⏸️ ez_positions_quick.py not started yet for {quick_account}")
        
        asyncio.create_task(self.monitor_big_positions())
        
        # Shared Memory Monitor Loop
        async def share_ind_monitor_loop():
            await asyncio.sleep(10.0)
            while not self._shutdown_requested:
                try:
                    if not self.is_share_ind_running():
                        logger.warning("[WATCHDOG] ⚠️ ez_share_ind not running - restarting")
                        self.start_share_ind()
                    await asyncio.sleep(30)
                except Exception as e:
                    logger.error(f"[WATCHDOG] Error in share_ind monitor: {e}")
                    await asyncio.sleep(30)
        asyncio.create_task(share_ind_monitor_loop())

        # Tradier Monitoring Loop
        async def tradier_monitor_loop():
            while not self._shutdown_requested:
                try:
                    await self.monitor_tradier()
                    await asyncio.sleep(30)
                except Exception as e:
                    logger.error(f"[WATCHDOG] Error in Tradier monitor loop: {e}")
                    await asyncio.sleep(30)
        asyncio.create_task(tradier_monitor_loop())

        watchdog_tasks = []
        for account_key in self.accounts:
            logger.warning(f"[WATCHDOG] 🚀 Creating watchdog task for {account_key}...")
            task = asyncio.create_task(self.watchdog_loop_for_account(account_key))
            watchdog_tasks.append((account_key, task))
            logger.warning(f"[WATCHDOG] ✅ Watchdog task created for {account_key}: {task}")
        
        await asyncio.sleep(0.5)
        running = sum(1 for _, task in watchdog_tasks if not task.done())
        logger.info(f"[WATCHDOG] ✅✅✅ VERIFIED: {running}/{len(watchdog_tasks)} watchdog instances running in PARALLEL")
        logger.info(f"[WATCHDOG] 🚨 Starting aggressive monitoring (check every {self.check_interval}s)")
        
        async def quick_health_check_loop():
            await asyncio.sleep(60.0)
            if self.quick_script.exists():
                self.quick_script_mtime = self.quick_script.stat().st_mtime
            while not self._shutdown_requested:
                try:
                    await asyncio.sleep(60.0)
                    data_providers_running = any(self.is_fetcher_running(account_key=acc) for acc in self.accounts)
                    
                    if self.quick_script.exists():
                        current_mtime = self.quick_script.stat().st_mtime
                        if self.quick_script_mtime is not None and current_mtime > self.quick_script_mtime:
                            logger.info(f"[WATCHDOG] 🔄 ez_positions_quick.py file changed - RESTARTING all instances")
                            self.stop_quick()
                            await asyncio.sleep(2.0)
                            self.quick_script_mtime = current_mtime
                            if data_providers_running:
                                for quick_account in self.quick_accounts:
                                    if self.start_quick(quick_account):
                                        logger.info(f"[WATCHDOG] ✅✅✅ ez_positions_quick.py restarted for {quick_account}")
                            continue
                        elif self.quick_script_mtime is None:
                            self.quick_script_mtime = current_mtime
                    
                    for quick_account in self.quick_accounts:
                        quick_running = self.is_quick_running(quick_account)
                        quick_working, quick_activity_age = self.is_quick_working(quick_account)
                        
                        # Use start time to calculate grace period status
                        start_time = self.quick_start_times.get(quick_account, 0.0)
                        in_grace = (time.time() - start_time) < self.quick_grace_period if start_time > 0 else False
                        
                        if quick_running and not quick_working and not in_grace and quick_activity_age > 180.0:
                            logger.info(f"[WATCHDOG] Quick script HANGING for {quick_account} - RESTARTING")
                            self.stop_quick(quick_account)
                            await asyncio.sleep(2.0)
                            if data_providers_running:
                                self.start_quick(quick_account)
                        elif not quick_running and data_providers_running:
                            logger.info(f"[WATCHDOG] 🚨 Quick script not running for {quick_account} - RESTARTING")
                            self.start_quick(quick_account)
                except Exception as e:
                    logger.error(f"[WATCHDOG] Error in quick health check: {e}")
        asyncio.create_task(quick_health_check_loop())
        
        try:
            await asyncio.gather(*[task for _, task in watchdog_tasks], return_exceptions=True)
        except KeyboardInterrupt:
            logger.warning("[WATCHDOG] Shutdown requested")
        except Exception as e:
            logger.error(f"[WATCHDOG] Error in watchdog tasks: {e}", exc_info=True)
        
        self.stop_fetcher()
        self.stop_quick()
        await self.stop_all_tradier()
        for account_key in list(self.realtime_processes.keys()):
            self.stop_realtime(account_key)
        for account_key in list(self.account_backup_processes.keys()):
            self.stop_account_backup(account_key)
        self.stop_backups()

async def main():
    watchdog = PositionWatchdog()
    _force_exit_requested = False
    def signal_handler(sig, frame):
        nonlocal _force_exit_requested
        if _force_exit_requested: os._exit(1)
        _force_exit_requested = True
        logger.warning(f"[WATCHDOG] 🛑 Received signal {sig}, shutting down...")
        watchdog._shutdown_requested = True
        import threading
        def force_exit():
            time.sleep(2.0)
            logger.warning(f"[WATCHDOG] 🛑 Force exiting...")
            os._exit(0)
        threading.Thread(target=force_exit, daemon=True).start()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    await watchdog.run()

if __name__ == "__main__":
    asyncio.run(main())