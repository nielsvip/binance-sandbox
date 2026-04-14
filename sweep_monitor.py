#!/usr/bin/env python3
"""Sweep monitor — runs every 15 min, checks all machines, logs results, restarts dead sweeps."""
import subprocess, time, os, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path("/Users/niels/Documents/binance/data/sweep_results/overnight_audit.log")
LOG.parent.mkdir(parents=True, exist_ok=True)

S1 = "s1-int"
S2 = "s2-int"
S1_PY = "/home/niels/.conda/envs/binance_env/bin/python"
S2_PY = "/home/niels/miniconda3/envs/binance_env/bin/python"

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def ssh(host, cmd, timeout=15):
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, cmd],
                          capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"SSH_ERROR: {e}"

def count_ok(logfile, local=False):
    try:
        if local:
            lines = open(logfile).readlines()
        else:
            return -1  # handled by ssh
        return sum(1 for l in lines if "status=ok" in l)
    except:
        return -1

def get_sharpe_stats(text):
    sharpes = []
    trades = []
    for m in re.finditer(r'sharpe=([-\d.]+) trades=(\d+) status=ok', text):
        sharpes.append(float(m.group(1)))
        trades.append(int(m.group(2)))
    if not sharpes:
        return None
    pos = sum(1 for s in sharpes if s > 0)
    return {
        "n": len(sharpes), "pos": pos, "pct": round(100*pos/len(sharpes)),
        "best": max(sharpes), "worst": min(sharpes),
        "avg_sharpe": round(sum(sharpes)/len(sharpes), 4),
        "avg_trades": round(sum(trades)/len(trades)),
    }

def check_server(name, host, logpath, sweep_cmd):
    """Check server, restart if dead."""
    # Count results
    raw = ssh(host, f"cat {logpath} 2>/dev/null")
    stats = get_sharpe_stats(raw) if raw and "SSH_ERROR" not in raw else None
    # Count processes
    procs = ssh(host, "ps aux | grep backtest_v8_engine | grep -v grep | wc -l")
    try:
        nprocs = int(procs.strip())
    except:
        nprocs = 0
    # Memory
    mem = ssh(host, "free -m | head -2 | tail -1 | awk '{print $3\"/\"$2}'")
    if stats:
        log(f"{name}: {stats['n']} configs done, {stats['pos']}/{stats['n']} profitable ({stats['pct']}%), "
            f"best={stats['best']:+.4f}, avg={stats['avg_sharpe']:+.4f}, avg_trades={stats['avg_trades']}, "
            f"procs={nprocs}, mem={mem}")
    else:
        log(f"{name}: NO RESULTS, procs={nprocs}, mem={mem}")
    # Restart if dead (0 procs but should be running)
    if nprocs == 0 and sweep_cmd:
        log(f"{name}: DEAD — RESTARTING")
        ssh(host, f'screen -dmS sweep bash -c "{sweep_cmd}"', timeout=30)
        log(f"{name}: Restart command sent")
    elif nprocs > 0 and stats and stats['n'] > 0:
        # Check if making progress — compare to last check
        progress_file = Path(f"/tmp/sweep_progress_{name}.json")
        last_n = 0
        if progress_file.exists():
            try:
                last_n = json.load(open(progress_file)).get("n", 0)
            except:
                pass
        if stats['n'] == last_n:
            log(f"{name}: WARNING — no progress since last check ({stats['n']} configs)")
        json.dump({"n": stats['n'], "ts": time.time()}, open(progress_file, "w"))

def check_local():
    logpath = "/tmp/v8_grid_local.log"
    try:
        raw = open(logpath).read()
        stats = get_sharpe_stats(raw)
    except:
        stats = None
    procs = subprocess.run(["pgrep", "-f", "backtest_v8_sweep"], capture_output=True, text=True)
    nprocs = len(procs.stdout.strip().split("\n")) if procs.stdout.strip() else 0
    if stats:
        log(f"LOCAL: {stats['n']}/288 done, {stats['pos']}/{stats['n']} profitable ({stats['pct']}%), "
            f"best={stats['best']:+.4f}, procs={nprocs}")
    else:
        log(f"LOCAL: no results, procs={nprocs}")

def main():
    log("=" * 60)
    log("SWEEP MONITOR CHECK")
    check_server("S1", S1, "/tmp/v8_t5.log",
                 f"cd /home/niels/binance && {S1_PY} -u backtest_v8_sweep.py --mode tradier --account trb --start 2025-06-01 --workers 4 --tier 4 --symbols AAPL,MSFT,NVDA,AMZN,AMD,XOM,QQQ,SPY --resume > /tmp/v8_t5.log 2>&1")
    check_server("S2", S2, "/tmp/v8_t5.log",
                 f"cd /home/niels/binance && {S2_PY} -u backtest_v8_sweep.py --mode tradier --account trb --start 2024-06-01 --workers 7 --tier 4 --symbols AAPL,MSFT,NVDA,AMZN,AMD,XOM,DIS,META,QQQ,SPY --resume > /tmp/v8_t5.log 2>&1")
    check_local()
    log("=" * 60)

if __name__ == "__main__":
    main()
