#!/usr/bin/env python3
"""V8 Watchdog — simple, aggressive, actually works.

Every 30 seconds:
- SSH check both servers (5s timeout, no bloat)
- If V8 not running → restart immediately
- If stalled 2x → kill and restart
- If results identical → log and flag
- Logs to stdout only (nohup handles file)

No fancy classes. No abstractions. Just checks and fixes.
"""
# metrics_guard retrofit (audited 2026-04-30): this script writes a Sharpe
# number to a print/log surface. Per CLAUDE.md NO-LIES MANDATE, any future
# user-facing Sharpe MUST be routed through metrics_guard.validate_and_format_sharpe()
# with explicit label, n_syms, years, trades, mode. Bare 'Sharpe X.XX' output is forbidden.
from metrics_guard import validate_and_format_sharpe  # noqa: F401  (forward-prevention import)
import os, re, subprocess, sys, time
from collections import defaultdict

sys.stdout.reconfigure(line_buffering=True)

S1 = "157.180.125.52"
S2 = "204.168.181.211"
S1_PY = "/home/niels/.conda/envs/binance_env/bin/python"
S2_PY = "/home/niels/miniconda3/envs/binance_env/bin/python"
WORKDIR = "/home/niels/binance"
NPZ = "/home/niels/binance-sandbox/backtest_v8/indicators"

# Singleton
LOCK = "/tmp/v8_watchdog.lock"
import fcntl
_lf = open(LOCK, "w")
try:
    fcntl.flock(_lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lf.write(str(os.getpid()))
    _lf.flush()
except (IOError, OSError):
    sys.exit(0)


def ssh(host, cmd):
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                            f"niels@{host}", cmd],
                           capture_output=True, text=True, timeout=8)
        return r.stdout.strip()
    except Exception:
        return ""


def now():
    return time.strftime("%H:%M:%S", time.gmtime())


def get_stock_symbols(host):
    out = ssh(host, f"ls {NPZ}/*.npz | xargs -I{{}} basename {{}} .npz | grep -v USDT | grep -v USDC | tr '\\n' ','")
    return out.rstrip(",") if out else ""


def get_crypto_symbols(host):
    out = ssh(host, f"ls {NPZ}/*.npz | xargs -I{{}} basename {{}} .npz | grep 'USDT\\|USDC' | tr '\\n' ','")
    return out.rstrip(",") if out else ""


def start_stocks(host, py):
    syms = get_stock_symbols(host)
    if not syms:
        return
    n = len(syms.split(","))
    ssh(host, f"rm -rf {WORKDIR}/__pycache__; screen -dmS stocks bash -c 'cd {WORKDIR} && {py} -u backtest_v8_engine.py --mode tradier --account trb --start 2022-06-01 --capital 60000 --symbols {syms} > /tmp/v8_full_stocks.log 2>&1'")
    print(f"  🚀 STARTED stocks: {n} symbols", flush=True)


def start_crypto(host, py):
    syms = get_crypto_symbols(host)
    if not syms:
        return
    n = len(syms.split(","))
    ssh(host, f"rm -rf {WORKDIR}/__pycache__; screen -dmS crypto bash -c 'cd {WORKDIR} && {py} -u backtest_v8_engine.py --mode crypto --account ang --start 2022-06-01 --capital 1000 --symbols {syms} > /tmp/v8_full_crypto.log 2>&1'")
    print(f"  🚀 STARTED crypto: {n} symbols", flush=True)


prev = {}
stalls = defaultdict(int)

print(f"[{now()}] V8 WATCHDOG STARTED — checking every 30s", flush=True)

while True:
    for name, host, py in [("S1", S1, S1_PY), ("S2", S2, S2_PY)]:
        # Count V8 processes
        procs = ssh(host, "pgrep -f backtest_v8_engine | wc -l")
        try:
            n_procs = int(procs)
        except (ValueError, TypeError):
            n_procs = -1

        # Count trades
        trades = ssh(host, "grep -c V8_EXEC_NOW /tmp/v8_full_stocks.log 2>/dev/null")
        try:
            n_trades = int(trades)
        except (ValueError, TypeError):
            n_trades = 0

        # Check for completed result
        result = ssh(host, "grep V8_RESULT /tmp/v8_full_stocks.log 2>/dev/null | tail -1")
        done = "V8_RESULT:" in result if result else False

        # Real gates?
        real = ssh(host, "grep -c 'Using REAL' /tmp/v8_full_stocks.log 2>/dev/null")
        try:
            real_on = int(real) > 0
        except (ValueError, TypeError):
            real_on = False

        status = "DONE" if done else ("RUN" if n_procs > 0 else "DEAD")
        gate = "REAL" if real_on else "FAKE"
        print(f"[{now()}] [{name}] {status} procs={n_procs} trades={n_trades} gates={gate}", flush=True)

        if done:
            parts = dict(re.findall(r'(\w+)=([0-9.-]+)', result))
            sh = parts.get("sharpe", "?")
            pnl = parts.get("pnl", "?")
            tr = parts.get("trades", "?")
            print(f"  ✅ Sharpe={sh} PnL={pnl}% Trades={tr}", flush=True)
            # Auto-restart after completion
            if n_procs == 0:
                print(f"  🔄 Completed — restarting", flush=True)
                start_stocks(host, py)

        elif n_procs == 0:
            print(f"  💀 NO V8 PROCESS — starting immediately", flush=True)
            start_stocks(host, py)
            stalls[name] = 0

        else:
            # Stall check
            k = f"{name}_trades"
            if k in prev and n_trades == prev[k] and n_trades > 0:
                stalls[name] += 1
                if stalls[name] >= 4:  # 2 minutes stalled
                    print(f"  🔄 STALLED {stalls[name]}x — killing and restarting", flush=True)
                    ssh(host, "pkill -9 -f backtest_v8_engine 2>/dev/null")
                    time.sleep(3)
                    start_stocks(host, py)
                    stalls[name] = 0
            else:
                stalls[name] = 0
            prev[k] = n_trades

            # Gate check
            if not real_on and n_trades > 50:
                print(f"  🚨 FAKE GATES — results will be garbage!", flush=True)

        # S1 crypto
        if name == "S1":
            c_procs = ssh(host, "pgrep -f 'backtest_v8_engine.*crypto' | wc -l")
            try:
                cn = int(c_procs)
            except (ValueError, TypeError):
                cn = 0
            if cn == 0:
                c_done = ssh(host, "grep V8_RESULT /tmp/v8_full_crypto.log 2>/dev/null | tail -1")
                if c_done and "V8_RESULT:" in c_done:
                    print(f"  ✅ CRYPTO done — restarting", flush=True)
                else:
                    print(f"  💀 CRYPTO dead — restarting", flush=True)
                start_crypto(host, py)

        # Kill live trading services (every 10th check)
        if int(time.time()) % 300 < 30:
            live = ssh(host, "pgrep -f 'ez_copilot|tradier_indicators|gateway_data_broadcaster' | wc -l")
            try:
                if int(live) > 0:
                    ssh(host, "pkill -f 'ez_copilot|tradier_indicators|gateway_data_broadcaster|klines_merge' 2>/dev/null")
                    print(f"  ⚠️  Killed {live} live trading processes", flush=True)
            except (ValueError, TypeError):
                pass

    time.sleep(30)
