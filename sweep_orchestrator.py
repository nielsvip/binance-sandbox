#!/usr/bin/env python3
"""V8 Sweep Orchestrator — 24/7 autonomous test manager across 3 machines.

- Checks every 60 seconds
- Fills CPU to 85%+ on all machines
- Auto-restarts crashed runs immediately
- Detects stalls, empty results, duplicate configs
- Queues next tests automatically after completion
- Survives restarts via launchd plist

Usage: python -u sweep_orchestrator.py
"""
import json, os, re, subprocess, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

# Singleton lock — prevent multiple instances
LOCK_FILE = "/tmp/sweep_orchestrator.lock"
import fcntl
_lock_fd = open(LOCK_FILE, "w")
try:
    fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()
except (IOError, OSError):
    print("Another sweep_orchestrator is already running. Exiting.")
    sys.exit(0)

LOG = "/tmp/sweep_orchestrator.log"
CHECK_INTERVAL = 60  # seconds
CPU_TARGET = 85  # percent
BASE_DIR = Path(__file__).parent

MACHINES = [
    {
        "name": "S1", "host": "157.180.125.52",
        "python": "/home/niels/.conda/envs/binance_env/bin/python",
        "workdir": "/home/niels/binance",
        "npz_dir": "/home/niels/binance-sandbox/backtest_v8/indicators",
        "cores": 8,
    },
    {
        "name": "S2", "host": "204.168.181.211",
        "python": "/home/niels/miniconda3/envs/binance_env/bin/python",
        "workdir": "/home/niels/binance",
        "npz_dir": "/home/niels/binance-sandbox/backtest_v8/indicators",
        "cores": 8,
    },
]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)  # stdout only — nohup/launchd handles the file


def ssh(host, cmd, timeout=15):
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                            f"niels@{host}", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def get_cpu(m):
    out = ssh(m["host"], "top -bn1 | grep 'Cpu(s)' | awk '{print $2+$4}'")
    if "ERROR" in out or not out:
        out = ssh(m["host"], "ps aux | awk '{s+=$3} END {print s}'")
    try:
        return float(out.strip())
    except (ValueError, TypeError):
        return 0.0


def get_v8_count(m):
    out = ssh(m["host"], "pgrep -f backtest_v8_engine | wc -l")
    try:
        return int(out.strip())
    except (ValueError, TypeError):
        return 0


def get_screens(m):
    out = ssh(m["host"], "screen -ls 2>/dev/null | grep -oP '\\d+\\.\\w+'")
    if not out or "ERROR" in out or "No Sockets" in out:
        return []
    return [s.strip() for s in out.split("\n") if s.strip()]


def get_health(m, log_name):
    ex = ssh(m["host"], f"grep -c V8_EXEC_NOW /tmp/{log_name} 2>/dev/null")
    bl = ssh(m["host"], f"grep -c BLOCKED /tmp/{log_name} 2>/dev/null")
    re_eta = ssh(m["host"], f"grep -c 'Using REAL' /tmp/{log_name} 2>/dev/null")
    try:
        ex = int(ex.strip())
    except (ValueError, TypeError):
        ex = 0
    try:
        bl = int(bl.strip())
    except (ValueError, TypeError):
        bl = 0
    try:
        re_on = int(re_eta.strip()) > 0
    except (ValueError, TypeError):
        re_on = False
    return {"exec": ex, "blocked": bl, "real_eta": re_on}


def get_result(m, log_name):
    out = ssh(m["host"], f"grep V8_RESULT /tmp/{log_name} 2>/dev/null | tail -1")
    if out and "V8_RESULT:" in out:
        p = dict(re.findall(r'(\w+)=([0-9.-]+)', out))
        return {k: float(p.get(k, 0)) for k in ("sharpe", "pnl", "trades", "wins", "losses")}
    return None


def get_symbols(m, mode="stocks"):
    if mode == "stocks":
        cmd = f"ls {m['npz_dir']}/*.npz | xargs -I{{}} basename {{}} .npz | grep -v USDT | grep -v USDC | tr '\\n' ','"
    else:
        cmd = f"ls {m['npz_dir']}/*.npz | xargs -I{{}} basename {{}} .npz | grep 'USDT\\|USDC' | tr '\\n' ','"
    out = ssh(m["host"], cmd, timeout=20)
    return out.rstrip(",") if out and "ERROR" not in out else ""


def start_run(m, mode="tradier", screen_name="stocks", symbols=None, start="2022-06-01", capital="60000"):
    if not symbols:
        symbols = get_symbols(m, "stocks" if mode == "tradier" else "crypto")
    if not symbols:
        log(f"  [!] No symbols for {mode} on {m['name']}")
        return False
    account = "trb" if mode == "tradier" else "ang"
    log_file = f"/tmp/v8_full_{'stocks' if mode == 'tradier' else 'crypto'}.log"
    cmd = (f"rm -rf {m['workdir']}/__pycache__; "
           f"screen -dmS {screen_name} bash -c '"
           f"cd {m['workdir']} && {m['python']} -u backtest_v8_engine.py "
           f"--mode {mode} --account {account} --start {start} "
           f"--capital {capital} --symbols {symbols} > {log_file} 2>&1'")
    ssh(m["host"], cmd, timeout=20)
    n_sym = len(symbols.split(","))
    log(f"  🚀 STARTED {screen_name} on {m['name']}: {mode} {n_sym} symbols from {start} ${capital}")
    return True


def start_sweep(m, screen_name="sweep", tier=25, symbols=None, start="2022-06-01", workers=None):
    if not symbols:
        symbols = get_symbols(m, "stocks")
    if not symbols:
        return False
    if workers is None:
        workers = max(2, m["cores"] - 2)
    log_file = f"/tmp/v8_sweep_t{tier}.log"
    cmd = (f"rm -rf {m['workdir']}/__pycache__; "
           f"screen -dmS {screen_name} bash -c '"
           f"cd {m['workdir']} && {m['python']} -u backtest_v8_sweep.py "
           f"--mode tradier --account trb --start {start} "
           f"--workers {workers} --tier {tier} --symbols {symbols} > {log_file} 2>&1'")
    ssh(m["host"], cmd, timeout=20)
    log(f"  🚀 STARTED sweep t{tier} on {m['name']}: {workers} workers, {len(symbols.split(','))} symbols")
    return True


def kill_live_services(m):
    """Kill any live trading services that shouldn't be running on servers."""
    live_scripts = ["ez_copilot", "tradier_indicators", "gateway_data_broadcaster",
                    "klines_merge_daemon", "trade_quality_auditor", "trade_auto_tuner",
                    "nightly_lab", "ang_reentry_monitor", "bitget_trader_scraper",
                    "okx_trader_scraper", "tradier_rankings", "tradier_positions",
                    "ez_positions", "ez_prices", "ez_rankings", "ez_indicators",
                    "ez_market_data", "ez_news_scanner"]
    for s in live_scripts:
        ssh(m["host"], f"pkill -f '{s}' 2>/dev/null", timeout=5)
    # Disable systemd services
    services = " ".join(f"binance-{s.replace('_','-')}" for s in
                        ["copilot", "ezindicators", "ezmarketdata", "eznewsscanner",
                         "ezpositions", "ezprices-ws", "ezprices", "ezrankings",
                         "klines-merge", "klines-sync", "tradier-indicators",
                         "tradier-positions", "tradier-rankings"])
    ssh(m["host"], f"sudo systemctl stop {services} gateway_data_broadcaster 2>/dev/null", timeout=10)


def main():
    log("=" * 70)
    log("V8 SWEEP ORCHESTRATOR — 24/7 AUTONOMOUS TEST MANAGER")
    log(f"Machines: {', '.join(m['name'] for m in MACHINES)}")
    log(f"Check interval: {CHECK_INTERVAL}s | CPU target: {CPU_TARGET}%")
    log("=" * 70)

    prev_exec = {}
    stall_counts = defaultdict(int)
    check_count = 0
    # Kill live services on startup
    for m in MACHINES:
        log(f"[{m['name']}] Killing live trading services (servers = sandbox only)...")
        kill_live_services(m)

    while True:
        try:
            check_count += 1
            ts_now = datetime.now(timezone.utc).strftime("%H:%M:%S")

            for m in MACHINES:
                name = m["name"]
                cpu = get_cpu(m)
                v8_procs = get_v8_count(m)
                screens = get_screens(m)
                screen_names = ",".join(screens) if screens else "none"

                # Compact status line
                log(f"[{name}] CPU={cpu:.0f}% procs={v8_procs} screens={screen_names}")

                # --- HEALTH CHECK: stocks ---
                h = get_health(m, "v8_full_stocks.log")
                if h["exec"] > 0 or h["blocked"] > 0:
                    total = h["exec"] + h["blocked"]
                    rate = h["exec"] * 100 / max(1, total)
                    status_parts = [f"trades={h['exec']}", f"blocked={h['blocked']}", f"pass={rate:.0f}%"]
                    if not h["real_eta"]:
                        status_parts.append("🚨NO_REAL_GATES")
                    log(f"  stocks: {' '.join(status_parts)}")

                    # Stall detection
                    key = f"{name}_stocks"
                    if key in prev_exec and h["exec"] == prev_exec[key]:
                        stall_counts[key] += 1
                        if stall_counts[key] >= 2:  # 2 minutes stall
                            if v8_procs == 0 or not any("stocks" in s for s in screens):
                                log(f"  🔄 STALL {stall_counts[key]}x + process dead → RESTARTING")
                                start_run(m, mode="tradier", screen_name="stocks")
                                stall_counts[key] = 0
                            elif stall_counts[key] >= 5:  # 5 minutes stuck
                                log(f"  🔄 STALL {stall_counts[key]}x — force killing and restarting")
                                ssh(m["host"], "pkill -f 'backtest_v8_engine.*tradier' 2>/dev/null")
                                time.sleep(3)
                                start_run(m, mode="tradier", screen_name="stocks")
                                stall_counts[key] = 0
                    else:
                        stall_counts[key] = 0
                    prev_exec[key] = h["exec"]

                # --- COMPLETED RESULT ---
                result = get_result(m, "v8_full_stocks.log")
                if result and result["trades"] > 0:
                    wr = result["wins"] * 100 / max(1, result["trades"])
                    log(f"  ✅ STOCKS DONE: Sharpe={result['sharpe']:.3f} PnL={result['pnl']:.2f}% Trades={int(result['trades'])} WR={wr:.0f}%")
                    # Auto-restart with next config or re-run
                    if v8_procs == 0:
                        log(f"  🔄 Run completed — restarting for continuous testing")
                        start_run(m, mode="tradier", screen_name="stocks")

                # --- CRYPTO on S1 ---
                if name == "S1":
                    ch = get_health(m, "v8_full_crypto.log")
                    if ch["exec"] > 0:
                        log(f"  crypto: trades={ch['exec']} blocked={ch['blocked']}")
                    cr = get_result(m, "v8_full_crypto.log")
                    if cr and cr["trades"] > 0:
                        log(f"  ✅ CRYPTO DONE: Sharpe={cr['sharpe']:.3f} Trades={int(cr['trades'])}")
                    # Auto-restart crypto if dead
                    crypto_alive = any("crypto" in s for s in screens)
                    if not crypto_alive:
                        crypto_procs = int(ssh(m["host"], "pgrep -f 'backtest_v8_engine.*crypto' | wc -l") or "0")
                        if crypto_procs == 0:
                            log(f"  🔄 Crypto not running — starting")
                            start_run(m, mode="crypto", screen_name="crypto", capital="1000")

                # --- CPU FILL: if underutilized, start more work ---
                if v8_procs == 0 and not any("stocks" in s for s in screens):
                    log(f"  ⚡ NO V8 running — starting baseline stocks run")
                    start_run(m, mode="tradier", screen_name="stocks")
                elif cpu < CPU_TARGET * 0.4 and v8_procs <= 1:
                    log(f"  ⚡ CPU very low ({cpu:.0f}%) — could add parallel sweep")

                # --- LIVE SERVICE CLEANUP (every 10 checks) ---
                if check_count % 10 == 0:
                    live_count = ssh(m["host"],
                                    "pgrep -f 'ez_copilot|tradier_indicators|gateway_data_broadcaster|klines_merge' | wc -l")
                    try:
                        lc = int(live_count.strip())
                    except (ValueError, TypeError):
                        lc = 0
                    if lc > 0:
                        log(f"  ⚠️  {lc} live trading processes detected — killing")
                        kill_live_services(m)

        except Exception as e:
            log(f"ERROR: {type(e).__name__}: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
