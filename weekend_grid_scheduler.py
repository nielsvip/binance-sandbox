#!/usr/bin/env python3
# pylint: disable=W,C,R
"""
WEEKEND GRID SCHEDULER — Runs Fri 20:00 UTC to Mon 00:00 UTC.
Keeps 80%+ of ALL cores busy on the server with backtest sweeps.
Supervises running jobs, restarts crashed ones, adjusts worker counts.

Architecture:
  - Server: 16 cores → 13 workers for backtests (3 reserved for OS/ssh)
  - Jobs queue: list of backtest commands sorted by priority
  - Supervisor loop: every 60s checks running jobs, fills empty slots
  - Results: aggregated into data/weekend_grid/results_YYYYMMDD.json

Usage:
    python weekend_grid_scheduler.py                # Start scheduler
    python weekend_grid_scheduler.py --status       # Show status
    python weekend_grid_scheduler.py --kill          # Kill all running jobs
    python weekend_grid_scheduler.py --local         # Run on local MacBook (4 workers)
"""
import argparse, json, logging, os, platform, signal, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

IS_SERVER = platform.system() == "Linux"
BASE = Path("/home/niels/binance-sandbox") if IS_SERVER else Path("/Users/niels/Documents/binance")
PYTHON = "/home/niels/.conda/envs/binance_env/bin/python" if IS_SERVER else "/opt/anaconda3/envs/binance_env/bin/python"
GRID_DIR = BASE / "data" / "weekend_grid"
GRID_DIR.mkdir(parents=True, exist_ok=True)
STATUS_FILE = GRID_DIR / "scheduler_status.json"
LOG_FILE = Path.home() / "logs" / "weekend_grid_scheduler.log"
MAX_WORKERS_SERVER = 13  # 16 cores - 3 reserved
MAX_WORKERS_LOCAL = 4    # MacBook has 12 but trading uses most
CHECK_INTERVAL = 60      # Seconds between supervisor checks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GRID] %(message)s", handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(str(LOG_FILE), mode="a")])
log = logging.getLogger(__name__)

shutdown = False
def _handle_sig(sig, frame):
    global shutdown
    shutdown = True
signal.signal(signal.SIGINT, _handle_sig)
signal.signal(signal.SIGTERM, _handle_sig)


@dataclass
class GridJob:
    name: str
    command: str
    workers: int = 1          # How many cores this job uses
    priority: int = 0         # Higher = run first
    status: str = "pending"   # pending, running, completed, failed
    pid: int = 0
    start_time: float = 0
    end_time: float = 0
    log_file: str = ""
    result_file: str = ""
    retries: int = 0
    max_retries: int = 2


def generate_job_queue(is_local: bool = False) -> List[GridJob]:
    """Generate all backtest jobs for the weekend grid."""
    jobs = []
    w = MAX_WORKERS_LOCAL if is_local else MAX_WORKERS_SERVER
    bt_script = "backtest_hedge_strategies.py"
    # ── PRIORITY 1: Hedge massive sweep (parallel, uses all workers) ──
    jobs.append(GridJob(
        name="hedge_massive_sweep",
        command=f"{PYTHON} {bt_script} --massive --workers {w} --resume --start 2023-01-01 --end 2025-12-31",
        workers=w, priority=100,
        log_file=str(GRID_DIR / "hedge_massive.log"),
        result_file=str(BASE / "data" / "backtest_hedge_strategies" / "hedge_strategies_results.json"),
    ))
    # ── PRIORITY 2: Focused hedge sweep around quick-run winners ──
    jobs.append(GridJob(
        name="hedge_focused_sweep",
        command=f"{PYTHON} {bt_script} --focused --workers {w} --resume --start 2023-01-01 --end 2025-12-31",
        workers=w, priority=90,
        log_file=str(GRID_DIR / "hedge_focused.log"),
        result_file=str(BASE / "data" / "backtest_hedge_strategies" / "hedge_focused_results.json"),
    ))
    # ── PRIORITY 3: Size sweep (fine-grained around winners) ──
    jobs.append(GridJob(
        name="hedge_size_sweep",
        command=f"{PYTHON} {bt_script} --size-sweep --workers {w} --resume --start 2023-01-01 --end 2025-12-31",
        workers=w, priority=80,
        log_file=str(GRID_DIR / "hedge_size_sweep.log"),
        result_file=str(BASE / "data" / "backtest_hedge_strategies" / "hedge_size_results.json"),
    ))
    # ── PRIORITY 4: V4 simulate sweeps (if script exists) ──
    if (BASE / "backtest_v4_sweep.py").exists():
        jobs.append(GridJob(
            name="v4_crypto_sweep",
            command=f"{PYTHON} backtest_v4_sweep.py --workers {w}",
            workers=w, priority=70,
            log_file=str(GRID_DIR / "v4_crypto_sweep.log"),
        ))
    # ── PRIORITY 5: Tradier hedge sweep ──
    if (BASE / "backtest_v4_simulate_tradier.py").exists():
        jobs.append(GridJob(
            name="tradier_sweep",
            command=f"{PYTHON} {bt_script} --massive --workers {w} --resume --start 2023-06-01 --end 2025-12-31",
            workers=w, priority=60,
            log_file=str(GRID_DIR / "tradier_sweep.log"),
        ))
    # ── PRIORITY 6: Reentry sweep, stock consolidation (already running as single-core) ──
    # These are existing scripts — we just need to ensure they're running
    for script in ["backtest_reentry_sweep.py", "backtest_stock_consolidation.py"]:
        if (BASE / script).exists():
            jobs.append(GridJob(
                name=script.replace(".py", ""),
                command=f"{PYTHON} {script}",
                workers=1, priority=40,
                log_file=str(GRID_DIR / f"{script.replace('.py', '')}.log"),
            ))
    return jobs


def is_weekend_window() -> bool:
    """Check if we're in the Fri 20:00 - Mon 00:00 UTC window."""
    now = datetime.now(timezone.utc)
    dow = now.weekday()  # 0=Mon ... 4=Fri, 5=Sat, 6=Sun
    hour = now.hour
    # Friday 20:00+ → True
    if dow == 4 and hour >= 20: return True
    # Saturday all day → True
    if dow == 5: return True
    # Sunday all day → True
    if dow == 6: return True
    # Monday 00:00 exactly → end
    return False


def is_process_alive(pid: int) -> bool:
    if pid <= 0: return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def count_running_cores(jobs: List[GridJob]) -> int:
    return sum(j.workers for j in jobs if j.status == "running" and is_process_alive(j.pid))


def start_job(job: GridJob) -> bool:
    """Start a backtest job in background."""
    try:
        log_fh = open(job.log_file, "a") if job.log_file else subprocess.DEVNULL
        proc = subprocess.Popen(
            job.command.split(),
            stdout=log_fh, stderr=subprocess.STDOUT,
            cwd=str(BASE), env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        job.pid = proc.pid
        job.status = "running"
        job.start_time = time.time()
        log.info(f"▶ STARTED: {job.name} (PID {job.pid}, {job.workers} workers)")
        return True
    except Exception as e:
        log.error(f"❌ FAILED to start {job.name}: {e}")
        job.status = "failed"
        return False


def check_job(job: GridJob) -> str:
    """Check job status. Returns: running, completed, failed."""
    if job.status != "running":
        return job.status
    if not is_process_alive(job.pid):
        # Process ended — check if it completed or crashed
        elapsed = time.time() - job.start_time
        if elapsed < 30:
            log.warning(f"⚠ {job.name} died after {elapsed:.0f}s — likely crashed")
            return "failed"
        else:
            log.info(f"✅ {job.name} completed in {elapsed/3600:.1f}h")
            return "completed"
    return "running"


def save_status(jobs: List[GridJob]):
    status = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "jobs": [asdict(j) for j in jobs],
        "total_cores_used": count_running_cores(jobs),
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2))


def run_scheduler(is_local: bool = False):
    max_cores = MAX_WORKERS_LOCAL if is_local else MAX_WORKERS_SERVER
    jobs = generate_job_queue(is_local)
    jobs.sort(key=lambda j: -j.priority)
    log.info(f"=" * 60)
    log.info(f"WEEKEND GRID SCHEDULER — {len(jobs)} jobs, {max_cores} max cores")
    log.info(f"Window: {'ACTIVE' if is_weekend_window() else 'OUTSIDE (running anyway)'}")
    log.info(f"=" * 60)
    for j in jobs:
        log.info(f"  [{j.priority:>3}] {j.name:<30} workers={j.workers}")
    # Main supervisor loop
    while not shutdown:
        # Update job statuses
        for job in jobs:
            if job.status == "running":
                new_status = check_job(job)
                if new_status != "running":
                    job.status = new_status
                    job.end_time = time.time()
                    if new_status == "failed" and job.retries < job.max_retries:
                        job.retries += 1
                        log.warning(f"🔄 Retrying {job.name} (attempt {job.retries}/{job.max_retries})")
                        job.status = "pending"
        # Count available cores
        used_cores = count_running_cores(jobs)
        available = max_cores - used_cores
        # Start pending jobs that fit
        for job in jobs:
            if job.status != "pending": continue
            if job.workers <= available:
                if start_job(job):
                    available -= job.workers
            if available <= 0:
                break
        # Status report
        running = [j for j in jobs if j.status == "running"]
        pending = [j for j in jobs if j.status == "pending"]
        completed = [j for j in jobs if j.status == "completed"]
        failed = [j for j in jobs if j.status == "failed"]
        utilization = used_cores / max_cores * 100
        log.info(f"[SUPERVISOR] Cores: {used_cores}/{max_cores} ({utilization:.0f}%) | Running: {len(running)} | Pending: {len(pending)} | Done: {len(completed)} | Failed: {len(failed)}")
        if not running and not pending:
            log.info("🏁 All jobs completed!")
            break
        save_status(jobs)
        # Sleep until next check
        time.sleep(CHECK_INTERVAL)
    # Final save
    save_status(jobs)
    log.info("Scheduler exiting.")


def show_status():
    if not STATUS_FILE.exists():
        print("No scheduler status found. Start with: python weekend_grid_scheduler.py")
        return
    status = json.loads(STATUS_FILE.read_text())
    print(f"Last update: {status['timestamp']}")
    print(f"Total cores used: {status['total_cores_used']}")
    print(f"{'Name':<30} {'Status':<12} {'Workers':>8} {'PID':>8} {'Runtime':>10}")
    print("-" * 75)
    for j in status["jobs"]:
        runtime = ""
        if j["start_time"] > 0:
            elapsed = (j["end_time"] if j["end_time"] > 0 else time.time()) - j["start_time"]
            runtime = f"{elapsed/3600:.1f}h"
        print(f"{j['name']:<30} {j['status']:<12} {j['workers']:>8} {j['pid']:>8} {runtime:>10}")


def kill_all():
    if STATUS_FILE.exists():
        status = json.loads(STATUS_FILE.read_text())
        for j in status["jobs"]:
            if j["pid"] > 0 and j["status"] == "running":
                try:
                    os.kill(j["pid"], signal.SIGTERM)
                    print(f"Killed {j['name']} (PID {j['pid']})")
                except OSError:
                    pass
    # Also kill any running backtest processes
    os.system("pkill -f 'python.*backtest_hedge' 2>/dev/null")
    print("All backtest processes killed.")


def main():
    parser = argparse.ArgumentParser(description="Weekend Grid Scheduler")
    parser.add_argument("--status", action="store_true", help="Show scheduler status")
    parser.add_argument("--kill", action="store_true", help="Kill all running jobs")
    parser.add_argument("--local", action="store_true", help="Run on local MacBook")
    parser.add_argument("--force", action="store_true", help="Run even outside weekend window")
    args = parser.parse_args()
    if args.status:
        show_status()
    elif args.kill:
        kill_all()
    else:
        run_scheduler(is_local=args.local)


if __name__ == "__main__":
    main()
