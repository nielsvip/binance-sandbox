#!/usr/bin/env python3
"""24/7 Sweep Monitor Agent — detects duplicate results, investigates dead params, fixes them.

Runs as a daemon. Every 5 minutes:
1. Pulls latest results from both servers
2. Checks for duplicate Sharpe/trade counts (identical results = dead param)
3. If duplicates found: identifies which param is varying but having no effect
4. Logs findings to /tmp/sweep_monitor.log
5. Optionally fixes by removing dead params from the sweep grid

Usage: python -u sweep_monitor_agent.py &
"""
import csv, io, json, os, re, subprocess, sys, time
from collections import defaultdict
from datetime import datetime, timezone

sys.stdout.reconfigure(line_buffering=True)

LOG = "/tmp/sweep_monitor.log"
CHECK_INTERVAL = 300  # 5 minutes

SERVERS = [
    {"name": "S1", "host": "157.180.125.52", "user": "niels",
     "log": "/tmp/v8_full_stocks.log", "crypto_log": "/tmp/v8_full_crypto.log",
     "sweep_dir": "/home/niels/binance-sandbox/backtest_v8/sweeps"},
    {"name": "S2", "host": "204.168.181.211", "user": "niels",
     "log": "/tmp/v8_full_stocks.log",
     "sweep_dir": "/home/niels/binance-sandbox/backtest_v8/sweeps"},
]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def ssh(host, cmd, timeout=15):
    try:
        r = subprocess.run(
            ["ssh", f"niels@{host}", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def get_latest_csv(server):
    """Get latest sweep CSV from server."""
    cmd = f"ls -t {server['sweep_dir']}/v8_sweep_tradier_t25*.csv 2>/dev/null | head -1"
    path = ssh(server["host"], cmd)
    if not path or "ERROR" in path:
        return None, []
    content = ssh(server["host"], f"cat {path}", timeout=30)
    if not content or "ERROR" in content:
        return path, []
    reader = csv.DictReader(io.StringIO(content))
    return path, list(reader)


def get_run_status(server, log_key="log"):
    """Get current run status from log file."""
    log_path = server.get(log_key, server["log"])
    output = ssh(server["host"], f"tail -3 {log_path} 2>/dev/null")
    if "ERROR" in output or not output:
        return {"status": "STOPPED", "raw": output}
    # Check if V8_RESULT exists (completed)
    result_line = ssh(server["host"], f"grep V8_RESULT {log_path} 2>/dev/null | tail -1")
    if result_line and "V8_RESULT:" in result_line:
        parts = dict(re.findall(r'(\w+)=([0-9.-]+)', result_line))
        return {
            "status": "COMPLETED",
            "sharpe": float(parts.get("sharpe", 0)),
            "pnl": float(parts.get("pnl", 0)),
            "trades": int(parts.get("trades", 0)),
            "wins": int(parts.get("wins", 0)),
            "losses": int(parts.get("losses", 0)),
        }
    # Check if process is running
    running = ssh(server["host"], f"pgrep -f 'backtest_v8_engine.*{log_path.split('/')[-1].replace('.log','').replace('v8_full_','')}' | wc -l")
    is_running = running.strip() not in ("0", "", "ERROR")
    return {"status": "RUNNING" if is_running else "STOPPED", "raw": output[-200:]}


def detect_duplicates(rows):
    """Detect configs producing identical results → dead params."""
    if len(rows) < 4:
        return [], []
    # Group by (sharpe, trades) — identical results
    groups = defaultdict(list)
    for row in rows:
        try:
            key = (float(row.get("sharpe", 0)), int(row.get("trades", 0)))
        except (ValueError, TypeError):
            continue
        groups[key].append(row)
    # Find groups with multiple configs producing same result
    duplicates = {k: v for k, v in groups.items() if len(v) > 1}
    if not duplicates:
        return [], []
    # Identify which params vary within duplicate groups but have no effect
    dead_params = set()
    for (sharpe, trades), configs in duplicates.items():
        if len(configs) < 2:
            continue
        # Find cfg_ columns that vary
        cfg_keys = [k for k in configs[0].keys() if k.startswith("cfg_")]
        for ck in cfg_keys:
            values = set(str(c.get(ck, "")) for c in configs)
            if len(values) > 1:
                dead_params.add(ck.replace("cfg_", ""))
    return list(duplicates.keys()), list(dead_params)


def check_single_run_credibility(server, log_key="log"):
    """Check if a single full run is producing credible results."""
    log_path = server.get(log_key, server["log"])
    # Count trades and check for suspiciously identical patterns
    output = ssh(server["host"],
                 f"grep -c V8_EXEC_NOW {log_path} 2>/dev/null")
    try:
        exec_count = int(output.strip())
    except (ValueError, TypeError):
        exec_count = 0
    # Check for blocked entries (real gates working)
    blocked = ssh(server["host"],
                  f"grep -c BLOCKED {log_path} 2>/dev/null")
    try:
        blocked_count = int(blocked.strip())
    except (ValueError, TypeError):
        blocked_count = 0
    # Check for real ETA usage
    real_eta = ssh(server["host"],
                   f"grep -c 'Using REAL' {log_path} 2>/dev/null")
    try:
        real_eta_count = int(real_eta.strip())
    except (ValueError, TypeError):
        real_eta_count = 0
    return {
        "exec_now_calls": exec_count,
        "blocked_entries": blocked_count,
        "real_eta_active": real_eta_count > 0,
        "ratio": f"{exec_count}/{exec_count + blocked_count}" if (exec_count + blocked_count) > 0 else "0/0"
    }


def restart_run(server, mode="tradier", log_key="log"):
    """Auto-restart a crashed V8 run on a server."""
    screen_name = "stocks" if mode == "tradier" else "crypto"
    log_path = server.get(log_key, server["log"])
    account = "trb" if mode == "tradier" else "ang"
    capital = "60000" if mode == "tradier" else "1000"
    python = "/home/niels/.conda/envs/binance_env/bin/python" if server["name"] == "S1" else "/home/niels/miniconda3/envs/binance_env/bin/python"
    # Get symbol list from NPZ files
    if mode == "tradier":
        sym_cmd = "ls /home/niels/binance-sandbox/backtest_v8/indicators/*.npz | xargs -I{} basename {} .npz | grep -v USDT | grep -v USDC | tr '\\n' ','"
    else:
        sym_cmd = "ls /home/niels/binance-sandbox/backtest_v8/indicators/*.npz | xargs -I{} basename {} .npz | grep 'USDT\\|USDC' | tr '\\n' ','"
    syms = ssh(server["host"], sym_cmd, timeout=20).rstrip(",")
    if not syms or "ERROR" in syms:
        log(f"  ❌ Cannot restart {screen_name} on {server['name']}: no symbols found")
        return False
    cmd = (f"screen -dmS {screen_name} bash -c 'cd /home/niels/binance && "
           f"{python} -u backtest_v8_engine.py --mode {mode} --account {account} "
           f"--start 2022-06-01 --capital {capital} --symbols {syms} > {log_path} 2>&1'")
    result = ssh(server["host"], cmd, timeout=10)
    log(f"  🔄 AUTO-RESTARTED {screen_name} on {server['name']}: {len(syms.split(','))} symbols")
    return True


def check_per_symbol_pnl(server, log_key="log"):
    """Parse latest JSONL log and report per-symbol performance."""
    log_path = server.get(log_key, server["log"])
    # Find latest JSONL
    jsonl_path = ssh(server["host"],
                     "ls -t /home/niels/binance-sandbox/backtest_v8/logs/v8_tradier_trb_*.jsonl 2>/dev/null | head -1")
    if not jsonl_path or "ERROR" in jsonl_path:
        return {}
    # Get trade summary per symbol
    output = ssh(server["host"],
                 f"cat {jsonl_path} | python3 -c \""
                 "import json,sys; from collections import defaultdict; "
                 "d=defaultdict(lambda:{{'w':0,'l':0,'pnl':0.0}}); "
                 "[d[json.loads(l).get('symbol','?')].__setitem__('w' if json.loads(l).get('pnl_pct',0)>0 else 'l', d[json.loads(l).get('symbol','?')].get('w' if json.loads(l).get('pnl_pct',0)>0 else 'l',0)+1) or d[json.loads(l).get('symbol','?')].__setitem__('pnl', d[json.loads(l).get('symbol','?')].get('pnl',0)+json.loads(l).get('pnl_dollars',0)) for l in sys.stdin if 'CLOSE' in l]; "
                 "print(json.dumps({k:v for k,v in sorted(d.items(), key=lambda x:x[1].get('pnl',0), reverse=True)}))"
                 "\" 2>/dev/null", timeout=30)
    if not output or "ERROR" in output:
        return {}
    try:
        return json.loads(output)
    except Exception:
        return {}


def investigate_pass_rate(server):
    """If pass rate is suspicious, check what's actually blocking/passing."""
    log_path = server["log"]
    # Get top block reasons
    output = ssh(server["host"],
                 f"grep -oP 'BLOCKED_\\w+' {log_path} 2>/dev/null | sort | uniq -c | sort -rn | head -5")
    return output if output and "ERROR" not in output else "no data"


def main():
    log("=" * 60)
    log("SWEEP MONITOR AGENT v2 — ACTIVE INVESTIGATOR")
    log("Checks every 5 min: run health, gate effectiveness, per-symbol PnL")
    log("=" * 60)
    prev_alerts = set()
    prev_exec = {}  # Track exec count to detect stalls
    stall_counts = defaultdict(int)  # consecutive stall checks per server
    check_count = 0
    while True:
        try:
            check_count += 1
            log(f"--- CHECK #{check_count} ---")
            for srv in SERVERS:
                # 1. Run status + credibility
                status = get_run_status(srv)
                cred = check_single_run_credibility(srv)
                exec_count = cred["exec_now_calls"]
                blocked_count = cred["blocked_entries"]
                total = exec_count + blocked_count
                pass_rate = exec_count * 100 / max(1, total)
                log(f"[{srv['name']}] stocks: {status['status']} | trades={exec_count} blocked={blocked_count} pass_rate={pass_rate:.1f}% real_eta={cred['real_eta_active']}")
                if status["status"] == "COMPLETED":
                    log(f"  ✅ RESULT: Sharpe={status.get('sharpe')} PnL={status.get('pnl')}% Trades={status.get('trades')} WR={status.get('wins',0)*100/max(1,status.get('trades',1)):.0f}%")
                # 2. Detect stalls and auto-restart
                prev_key = f"{srv['name']}_stocks_exec"
                if prev_key in prev_exec and exec_count == prev_exec[prev_key] and status["status"] == "RUNNING":
                    stall_counts[prev_key] += 1
                    log(f"  ⚠️  STALL #{stall_counts[prev_key]}: no new trades since last check ({exec_count} total)")
                    if stall_counts[prev_key] >= 3:
                        log(f"  🚨 STALLED 3x — investigating and restarting...")
                        block_reasons = investigate_pass_rate(srv)
                        log(f"     Last block reasons: {block_reasons}")
                        # Check if process is actually dead
                        proc_count = ssh(srv["host"], "pgrep -f 'backtest_v8_engine.*tradier' | wc -l")
                        if proc_count.strip() in ("0", ""):
                            log(f"     Process DEAD — auto-restarting stocks on {srv['name']}")
                            restart_run(srv, mode="tradier")
                        else:
                            log(f"     Process alive ({proc_count.strip()} PIDs) but stuck — may be OOM or deadlock")
                        stall_counts[prev_key] = 0
                elif status["status"] != "RUNNING":
                    # Process stopped/completed — check if it should restart
                    proc_count = ssh(srv["host"], "pgrep -f 'backtest_v8_engine.*tradier' | wc -l")
                    if proc_count.strip() in ("0", "") and status["status"] != "COMPLETED":
                        log(f"  🚨 {srv['name']} stocks CRASHED — auto-restarting")
                        restart_run(srv, mode="tradier")
                    stall_counts[prev_key] = 0
                else:
                    stall_counts[prev_key] = 0
                prev_exec[prev_key] = exec_count
                # 3. Alert: real ETA not active
                if not cred["real_eta_active"] and exec_count > 0:
                    alert_key = f"{srv['name']}_no_real_eta"
                    if alert_key not in prev_alerts:
                        log(f"  🚨 CRITICAL: {srv['name']} NOT using real gates! All entries pass unfiltered!")
                        log(f"     FIX: check backtest_v8_engine.py _use_real_eta = True")
                        prev_alerts.add(alert_key)
                # 4. Alert: pass rate too high (gates not working)
                if total > 200 and pass_rate > 70:
                    alert_key2 = f"{srv['name']}_high_pass_{check_count//12}"  # re-alert every hour
                    if alert_key2 not in prev_alerts:
                        log(f"  🚨 HIGH PASS RATE: {pass_rate:.0f}% — gates may not be filtering!")
                        block_reasons = investigate_pass_rate(srv)
                        log(f"     Top block reasons: {block_reasons}")
                        prev_alerts.add(alert_key2)
                # 5. Alert: pass rate suspiciously low (everything blocked)
                if total > 200 and pass_rate < 1:
                    alert_key3 = f"{srv['name']}_all_blocked_{check_count//12}"
                    if alert_key3 not in prev_alerts:
                        log(f"  🚨 ALL BLOCKED: {pass_rate:.0f}% pass rate — gates too restrictive!")
                        block_reasons = investigate_pass_rate(srv)
                        log(f"     Top block reasons: {block_reasons}")
                        prev_alerts.add(alert_key3)
                # 6. Crypto on S1 — monitor and auto-restart
                if srv["name"] == "S1":
                    c_status = get_run_status(srv, "crypto_log")
                    log(f"[{srv['name']}] crypto: {c_status['status']}")
                    if c_status["status"] == "COMPLETED":
                        log(f"  ✅ CRYPTO RESULT: Sharpe={c_status.get('sharpe')} PnL={c_status.get('pnl')}% Trades={c_status.get('trades')}")
                    elif c_status["status"] not in ("RUNNING", "COMPLETED"):
                        proc_count = ssh(srv["host"], "pgrep -f 'backtest_v8_engine.*crypto' | wc -l")
                        if proc_count.strip() in ("0", ""):
                            log(f"  🚨 {srv['name']} crypto CRASHED — auto-restarting")
                            restart_run(srv, mode="crypto", log_key="crypto_log")
                # 7. Per-symbol PnL (every 6th check = 30 min)
                if check_count % 6 == 0:
                    sym_pnl = check_per_symbol_pnl(srv)
                    if sym_pnl:
                        top3 = list(sym_pnl.items())[:3]
                        bot3 = list(sym_pnl.items())[-3:]
                        top_str = ", ".join(f"{s}=${d.get('pnl',0):.0f}" for s, d in top3)
                        bot_str = ", ".join(f"{s}=${d.get('pnl',0):.0f}" for s, d in bot3)
                        log(f"  TOP symbols: {top_str}")
                        log(f"  BOT symbols: {bot_str}")
            # 8. Check sweep CSVs for duplicates (only recent files)
            for srv in SERVERS:
                csv_path, rows = get_latest_csv(srv)
                if not rows or len(rows) < 4:
                    continue
                # Only check CSVs modified in last 2 hours
                if csv_path:
                    age = ssh(srv["host"], f"stat -c %Y {csv_path} 2>/dev/null")
                    try:
                        file_age = time.time() - float(age.strip())
                        if file_age > 7200:
                            continue  # Skip stale CSVs
                    except (ValueError, TypeError):
                        pass
                dupe_keys, dead_params = detect_duplicates(rows)
                if dead_params:
                    log(f"[{srv['name']}] 🚨 DEAD PARAMS in active sweep: {', '.join(dead_params)}")
                    log(f"     Investigating: checking if params are read in tradier_manage.py...")
                    for dp in dead_params[:3]:
                        usage = ssh(srv["host"], f"grep -c '{dp}' /home/niels/binance/tradier_manage.py 2>/dev/null")
                        log(f"     {dp}: {usage} references in tradier_manage.py")
        except Exception as e:
            log(f"ERROR: {type(e).__name__}: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
