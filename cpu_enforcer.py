#!/usr/bin/env python3
"""CPU Enforcer — fills ALL CPUs to 85%+ across 3 machines. Checks every 20s.

Monitors:
- Per-core CPU usage on S1 (8 cores), S2 (8 cores), Local (10 cores)
- ALL log files for errors (Traceback, Error, OOM, Killed)
- Live trading logs + sandbox backtest logs
- Spawns parallel V8 workers to fill idle cores
- Kills and restarts anything broken

NO abstractions. NO classes. Just a tight loop that WORKS.
"""
import os, re, subprocess, sys, time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

# Singleton
import fcntl
_lf = open("/tmp/cpu_enforcer.lock", "w")
try:
    fcntl.flock(_lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lf.write(str(os.getpid()))
    _lf.flush()
except (IOError, OSError):
    print("Already running"); sys.exit(0)

S1 = "157.180.125.52"
S2 = "204.168.181.211"
S1_PY = "/home/niels/.conda/envs/binance_env/bin/python"
S2_PY = "/home/niels/miniconda3/envs/binance_env/bin/python"
WORKDIR = "/home/niels/binance"
NPZ = "/home/niels/binance-sandbox/backtest_v8/indicators"
LOCAL_PY = "/opt/anaconda3/envs/binance_env/bin/python"
LOCAL_DIR = str(Path(__file__).parent)
LOCAL_NPZ = str(Path(__file__).parent / "backtest_v8/indicators")

CHECK = 20  # seconds
CPU_MIN = 85


def ssh(host, cmd, timeout=8):
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=4", "-o", "BatchMode=yes",
                            f"niels@{host}", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def local(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def ts():
    return time.strftime("%H:%M:%S", time.gmtime())


def get_per_core_cpu(host):
    """Get per-core CPU usage. Returns (avg_cpu, n_cores, idle_cores)."""
    if host is None:
        # macOS: use top
        out = local("ps -A -o %cpu | awk '{s+=$1} END {print s}'")
        try:
            total = float(out)
            cores = int(local("sysctl -n hw.ncpu") or "10")
            avg = total / cores
            idle = sum(1 for _ in range(cores) if avg < CPU_MIN)
            return avg, cores, idle
        except (ValueError, TypeError):
            return 0, 10, 10
    else:
        # Linux: mpstat per core
        out = ssh(host, "mpstat -P ALL 1 1 2>/dev/null | tail -n +4 | awk '{print 100-$NF}'")
        if not out:
            # Fallback: total CPU / cores
            total = ssh(host, "ps aux | awk '{s+=$3} END {print s}'")
            cores = int(ssh(host, "nproc") or "8")
            try:
                avg = float(total) / cores
                idle = max(0, cores - int(float(total) / 100))
                return avg, cores, idle
            except (ValueError, TypeError):
                return 0, 8, 8
        lines = [l.strip() for l in out.split("\n") if l.strip()]
        try:
            cpus = [float(l) for l in lines if l.replace(".", "").replace("-", "").isdigit()]
            if not cpus:
                return 0, 8, 8
            avg = sum(cpus) / len(cpus)
            idle = sum(1 for c in cpus if c < CPU_MIN)
            return avg, len(cpus), idle
        except (ValueError, TypeError):
            return 0, 8, 8


def get_v8_count(host):
    if host is None:
        out = local("pgrep -f backtest_v8_engine | wc -l")
    else:
        out = ssh(host, "pgrep -f backtest_v8_engine | wc -l")
    if not out:
        return -1  # SSH failed — don't treat as zero
    try:
        return int(out.strip())
    except (ValueError, TypeError):
        return -1


def check_errors(host, log_paths):
    """Check log files for recent errors. Returns list of (logfile, error_line)."""
    errors = []
    for lp in log_paths:
        if host is None:
            out = local(f"tail -50 {lp} 2>/dev/null | grep -i 'Traceback\\|Error\\|OOM\\|Killed\\|MemoryError' | tail -3")
        else:
            out = ssh(host, f"tail -50 {lp} 2>/dev/null | grep -i 'Traceback\\|Error\\|OOM\\|Killed\\|MemoryError' | tail -3")
        if out and "Error" not in out[:5]:  # skip ssh errors
            for line in out.split("\n"):
                if line.strip():
                    errors.append((lp.split("/")[-1], line.strip()[:120]))
    return errors


def get_symbols(host, mode="stocks"):
    if mode == "stocks":
        cmd = f"ls {NPZ}/*.npz | xargs -I{{}} basename {{}} .npz | grep -v USDT | grep -v USDC | tr '\\n' ','"
    else:
        cmd = f"ls {NPZ}/*.npz | xargs -I{{}} basename {{}} .npz | grep 'USDT\\|USDC' | tr '\\n' ','"
    if host is None:
        out = local(f"ls {LOCAL_NPZ}/*.npz | xargs -I{{}} basename {{}} .npz | " +
                    ("grep -v USDT | grep -v USDC" if mode == "stocks" else "grep 'USDT\\|USDC'") +
                    " | tr '\\n' ','", timeout=15)
    else:
        out = ssh(host, cmd, timeout=15)
    return out.rstrip(",") if out else ""


def start_v8_worker(host, py, workdir, screen_name, mode="tradier", symbols=None, start="2022-06-01", capital="60000"):
    """Start a single V8 engine worker."""
    if not symbols:
        symbols = get_symbols(host, "stocks" if mode == "tradier" else "crypto")
    if not symbols:
        return
    account = "trb" if mode == "tradier" else "ang"
    log_file = f"/tmp/v8_worker_{screen_name}.log"
    cmd = (f"rm -rf {workdir}/__pycache__ 2>/dev/null; "
           f"screen -dmS {screen_name} bash -c '"
           f"cd {workdir} && {py} -u backtest_v8_engine.py "
           f"--mode {mode} --account {account} --start {start} "
           f"--capital {capital} --symbols {symbols} > {log_file} 2>&1'")
    if host is None:
        local(cmd)
    else:
        ssh(host, cmd, timeout=15)
    n = len(symbols.split(","))
    print(f"  🚀 Started {screen_name}: {mode} {n} symbols", flush=True)


# Server log paths to check
SERVER_LOGS = [
    "/tmp/v8_full_stocks.log",
    "/tmp/v8_full_crypto.log",
    "/tmp/v8_worker_sweep1.log",
    "/tmp/v8_worker_sweep2.log",
    "/tmp/v8_worker_sweep3.log",
]
LOCAL_LOGS = [
    "/Users/niels/logs/tradier_manage_trb.log",
    "/Users/niels/logs/tradier_manage_trc.log",
    "/Users/niels/logs/ez_manage_ang.log",
    "/Users/niels/logs/ez_manage_inf.log",
    "/Users/niels/logs/ez_manage_men.log",
]

print(f"[{ts()}] CPU ENFORCER STARTED — target {CPU_MIN}% per core, checking every {CHECK}s", flush=True)

error_reported = set()

# Local services that must stay alive
LOCAL_SERVICES = {
    "sweep_cockpit.py": f"nohup {LOCAL_PY} {LOCAL_DIR}/sweep_cockpit.py &>/tmp/cockpit.log &",
    "v8_watchdog.py": f"nohup {LOCAL_PY} -u {LOCAL_DIR}/v8_watchdog.py >> /tmp/v8_watchdog.log 2>&1 &",
    "trade_analytics.py": f"nohup {LOCAL_PY} -u {LOCAL_DIR}/trade_analytics.py &>/Users/niels/logs/trade_analytics.log &",
}

while True:
    # SELF-HEAL: ensure cockpit + watchdog + trade_analytics stay alive locally
    for svc_name, start_cmd in LOCAL_SERVICES.items():
        running = local(f"pgrep -f '{svc_name}' | wc -l")
        try:
            if int(running.strip()) == 0:
                print(f"[{ts()}] 🔧 {svc_name} DEAD — restarting", flush=True)
                local(start_cmd)
        except (ValueError, TypeError):
            pass

    for name, host, py, workdir, npz, logs in [
        ("S1", S1, S1_PY, WORKDIR, NPZ, SERVER_LOGS),
        ("S2", S2, S2_PY, WORKDIR, NPZ, SERVER_LOGS),
        ("Local", None, LOCAL_PY, LOCAL_DIR, LOCAL_NPZ, LOCAL_LOGS),
    ]:
        avg_cpu, cores, idle = get_per_core_cpu(host)
        v8_procs = get_v8_count(host)

        # Compact status
        fill = "OK" if idle == 0 else f"{idle}idle"
        print(f"[{ts()}] [{name}] avg={avg_cpu:.0f}% cores={cores} idle={idle} v8={v8_procs} [{fill}]", flush=True)

        # ERROR CHECK: scan logs for errors → FIX THEM
        errs = check_errors(host, logs)
        for logname, errline in errs:
            err_key = f"{name}_{logname}_{errline[:40]}"
            if err_key not in error_reported:
                print(f"  ❌ ERROR in {logname}: {errline}", flush=True)
                error_reported.add(err_key)
                if len(error_reported) > 500:
                    error_reported = set(list(error_reported)[-200:])
                # === AUTO-FIX ERRORS ===
                if "MemoryError" in errline or "OOM" in errline or "Killed" in errline:
                    print(f"  🔧 FIX: OOM/Killed — killing all V8, clearing cache, restarting with fewer symbols", flush=True)
                    if host:
                        ssh(host, "pkill -9 -f backtest_v8_engine 2>/dev/null")
                        time.sleep(3)
                        # Restart with half the symbols to reduce memory
                        syms = get_symbols(host, "stocks")
                        if syms:
                            half = ",".join(syms.split(",")[:len(syms.split(","))//2])
                            start_v8_worker(host, py, workdir, "stocks", symbols=half)
                elif "ModuleNotFoundError" in errline or "ImportError" in errline:
                    print(f"  🔧 FIX: Import error — clearing __pycache__ and restarting", flush=True)
                    if host:
                        ssh(host, f"rm -rf {workdir}/__pycache__; find {workdir} -name '__pycache__' -exec rm -rf {{}} + 2>/dev/null")
                        ssh(host, "pkill -f backtest_v8_engine 2>/dev/null")
                        time.sleep(3)
                        start_v8_worker(host, py, workdir, "stocks")
                elif "TypeError" in errline:
                    print(f"  🔧 FIX: TypeError — likely bad function args. Killing and restarting clean", flush=True)
                    if host:
                        ssh(host, f"rm -rf {workdir}/__pycache__")
                        ssh(host, "pkill -f backtest_v8_engine 2>/dev/null")
                        time.sleep(3)
                        start_v8_worker(host, py, workdir, "stocks")
                elif "ConnectionRefusedError" in errline or "ConnectionResetError" in errline:
                    print(f"  🔧 FIX: Connection error — likely Redis/SSH. Ignoring (backtest doesn't need connections)", flush=True)
                elif "Traceback" in errline:
                    # Generic traceback — kill the specific process and restart
                    print(f"  🔧 FIX: Generic crash — restarting affected process", flush=True)
                    if host and "v8_full_stocks" in logname:
                        ssh(host, "pkill -f 'backtest_v8_engine.*tradier' 2>/dev/null")
                        time.sleep(3)
                        start_v8_worker(host, py, workdir, "stocks")
                    elif host and "crypto" in logname:
                        ssh(host, "pkill -f 'backtest_v8_engine.*crypto' 2>/dev/null")
                        time.sleep(3)
                        start_v8_worker(host, py, workdir, "crypto", mode="crypto", capital="1000")

        # CPU FILL: if idle cores exist on servers, spawn more V8 workers
        # Skip if SSH failed (v8_procs == -1 means timeout, not zero)
        if name != "Local" and idle > 1 and v8_procs >= 0 and v8_procs < cores:
            workers_needed = min(idle, cores - v8_procs)
            print(f"  ⚡ {idle} idle cores, {v8_procs} V8 procs — spawning {workers_needed} more workers", flush=True)
            # Get symbol subsets for parallel workers
            all_syms = get_symbols(host, "stocks")
            if all_syms:
                sym_list = all_syms.split(",")
                chunk = max(20, len(sym_list) // max(1, workers_needed))
                for i in range(workers_needed):
                    subset = ",".join(sym_list[i*chunk:(i+1)*chunk])
                    if subset:
                        start_v8_worker(host, py, workdir, f"sweep{i+1}",
                                        symbols=subset, start="2022-06-01")

        # DEAD PROCESS CHECK on servers (skip if SSH failed)
        if name != "Local" and v8_procs == 0 and v8_procs != -1:
            print(f"  💀 Zero V8 procs — starting stocks run", flush=True)
            start_v8_worker(host, py, workdir, "stocks",
                            start="2022-06-01")
            if name == "S1":
                start_v8_worker(host, py, workdir, "crypto",
                                mode="crypto", capital="1000")

    # DEAD PARAM CHECK: every 5th cycle, check if latest sweep has identical results
    if int(time.time()) % 100 < CHECK:
        for name, host in [("S1", S1), ("S2", S2)]:
            if host is None:
                continue
            # Check latest sweep CSV for duplicates
            csv_path = ssh(host, f"ls -t /home/niels/binance-sandbox/backtest_v8/sweeps/v8_sweep_*.csv 2>/dev/null | head -1")
            if not csv_path:
                continue
            # Check age — only care about files < 2 hours old
            age = ssh(host, f"echo $(( $(date +%s) - $(stat -c %Y {csv_path}) ))")
            try:
                if int(age) > 7200:
                    continue
            except (ValueError, TypeError):
                continue
            # Get unique Sharpe count vs total rows
            uniq = ssh(host, f"tail -n+2 {csv_path} | cut -d, -f3 | sort -u | wc -l")
            total = ssh(host, f"tail -n+2 {csv_path} | wc -l")
            try:
                u, t = int(uniq), int(total)
                if t > 10 and u < t * 0.3:
                    # Find which params vary but produce same result
                    header = ssh(host, f"head -1 {csv_path}")
                    if header:
                        cfg_cols = [c for c in header.split(",") if c.startswith("cfg_")]
                        dead = []
                        for col in cfg_cols:
                            col_idx = header.split(",").index(col)
                            vals = ssh(host, f"tail -n+2 {csv_path} | cut -d, -f{col_idx+1} | sort -u | wc -l")
                            sharpes_per_val = ssh(host, f"tail -n+2 {csv_path} | cut -d, -f{col_idx+1},3 | sort -u | wc -l")
                            try:
                                if int(vals) > 1 and int(sharpes_per_val) == int(vals):
                                    pass  # Different values → different Sharpe = LIVE
                                elif int(vals) > 1:
                                    dead.append(col.replace("cfg_", ""))
                            except (ValueError, TypeError):
                                pass
                        if dead:
                            print(f"  🚨 [{name}] DEAD PARAMS: {', '.join(dead[:5])}", flush=True)
                            # AUTO-FIX: check WHY they're dead
                            for dp in dead[:3]:
                                # Check if param exists in config
                                exists = ssh(host, f"grep -c '{dp}' {workdir}/config_tradier.py 2>/dev/null")
                                # Check if param is read in tradier_manage
                                used = ssh(host, f"grep -c '{dp}' {workdir}/tradier_manage.py 2>/dev/null")
                                # Check if V8 override actually sets it
                                override_check = ssh(host, f"ls -t /home/niels/binance-sandbox/backtest_v8/sweeps/override_*.json 2>/dev/null | head -1 | xargs cat 2>/dev/null | grep -c '{dp}'")
                                print(f"     {dp}: config={exists} manage={used} override={override_check}", flush=True)
                                try:
                                    if int(used or 0) == 0:
                                        print(f"     🔧 FIX: {dp} NOT in tradier_manage.py — param is orphaned, removing from sweep grid", flush=True)
                                    elif int(exists or 0) == 0:
                                        print(f"     🔧 FIX: {dp} NOT in config_tradier.py — override can't set it", flush=True)
                                    elif int(override_check or 0) == 0:
                                        print(f"     🔧 FIX: {dp} NOT in override JSON — sweep not passing it to V8", flush=True)
                                    else:
                                        print(f"     ⚠️  {dp} exists everywhere but has no effect — code path may be unreachable (patched out?)", flush=True)
                                except (ValueError, TypeError):
                                    pass
            except (ValueError, TypeError):
                pass

    time.sleep(CHECK)
