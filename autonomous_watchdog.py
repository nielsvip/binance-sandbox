"""Autonomous search watchdog — runs every 2 min via cron.

Detects dead/stuck workers on Mac, S1, S2 and restarts them immediately
with new unique seeds. Never repeats a seed. Never lets more than a few
minutes pass without all workers running.

Cron line (add via: crontab -e):
*/2 * * * * /opt/anaconda3/envs/binance_env/bin/python /Users/niels/Documents/binance/autonomous_watchdog.py >> /Users/niels/logs/autonomous_watchdog.log 2>&1
"""
import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

NOW = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
LOG_STALE_SECS = 1200     # 20 min without update = worker stuck (300s timeout × 4 consecutive allowed)
STARTUP_GRACE_SECS = 600  # 10 min grace for 0-byte logs while NPZ is loading

STATE_FILE = Path("/Users/niels/Documents/binance/data/autonomous/watchdog_state.json")

MACHINES = {
    "mac": {
        "host": "local",
        "python": "/opt/anaconda3/envs/binance_env/bin/python",
        "script": "/Users/niels/Documents/binance/autonomous_search.py",
        "cwd": "/Users/niels/Documents/binance",
        "mode": "tradier",
        "symbols": 114,
        "start": "2022-01-01",
        "npz_dir": "/Users/niels/Documents/binance/backtest_v8/indicators",
        "bh_pct": 50,
        "target_gain": 150,
        "target_sharpe": 3.0,
        "out_dir_template": "/Users/niels/Documents/binance/data/autonomous/tradier_3p4361_macbook/w{seed}",
        "log_template": "/Users/niels/logs/autonomous_tradier_macbook_w{seed}.log",
        "baseline_json": "/Users/niels/Documents/binance/data/baselines/tradier_3p4361_genuine.json",
        "n_workers": 2,
        "max_iter_seconds": 300,
        "extra_flags": "--min-trades-per-sym 1 --sharpe-useless-floor 3.0 --bool-flip-prob 0.02 --numeric-perturb-prob 0.04",
    },
    "s1": {
        "host": "niels@157.180.125.52",
        "python": "/home/niels/.conda/envs/binance_env/bin/python",
        "script": "/home/niels/binance-sandbox/autonomous_search.py",
        "cwd": "/home/niels/binance-sandbox",
        "mode": "crypto",
        "symbols": 50,
        "start": "2022-01-01",
        "npz_dir": "/home/niels/binance-sandbox/backtest_v8/indicators",
        "bh_pct": 3500,
        "target_gain": 120,
        "target_sharpe": 2.0,
        "out_dir_template": "/home/niels/binance-sandbox/data/autonomous/crypto_2p6365_50sym/w{seed}",
        "log_template": "/home/niels/logs/autonomous_crypto_w{seed}.log",
        "baseline_json": "/home/niels/binance-sandbox/data/baselines/crypto_2p6365_genuine.json",
        "n_workers": 1,
        "max_iter_seconds": 120,
        "extra_flags": "--min-trades-per-sym 1 --sharpe-useless-floor 2.0 --bool-flip-prob 0.02 --numeric-perturb-prob 0.02",
    },
    "s2": {
        "host": "niels@204.168.181.211",
        "python": "/home/niels/miniconda3/envs/binance_env/bin/python",
        "script": "/home/niels/binance-sandbox/autonomous_search.py",
        "cwd": "/home/niels/binance-sandbox",
        "mode": "tradier",
        "symbols": 114,
        "start": "2022-01-01",
        "npz_dir": "/home/niels/binance-sandbox/backtest_v8/indicators",
        "bh_pct": 50,
        "target_gain": 150,
        "target_sharpe": 3.0,
        "out_dir_template": "/home/niels/binance-sandbox/data/autonomous/tradier_3p4361_114sym/w{seed}",
        "log_template": "/home/niels/logs/autonomous_tradier_w{seed}.log",
        "baseline_json": "/home/niels/binance-sandbox/data/baselines/tradier_3p4361_genuine.json",
        "n_workers": 2,
        "max_iter_seconds": 120,
        "extra_flags": "--min-trades-per-sym 1 --sharpe-useless-floor 3.0 --bool-flip-prob 0.02 --numeric-perturb-prob 0.04",
    },
}


def _load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {m: {"next_seed": 5000 + i * 10000, "used_seeds": []} for i, m in enumerate(MACHINES)}


def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _run_local(cmd, cwd=None):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=45)


def _run_remote(host, cmd, timeout=45, capture=True):
    full = f'ssh -o ConnectTimeout=10 -o BatchMode=yes {host} "{cmd}"'
    if capture:
        return subprocess.run(full, shell=True, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)
    return subprocess.run(full, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL, timeout=timeout)


def _log_age_local(log_path):
    """Return staleness in seconds. 0-byte logs get a 10-min startup grace."""
    try:
        age = time.time() - os.path.getmtime(log_path)
        size = os.path.getsize(log_path)
        if size == 0:
            return age if age > STARTUP_GRACE_SECS else 0
        return age
    except OSError:
        return 9999


def _log_age_remote(host, log_path):
    """Return staleness in seconds. 0-byte logs get a 10-min startup grace."""
    r = _run_remote(host, f"stat -c '%Y %s' {log_path} 2>/dev/null || echo '0 0'")
    try:
        parts = r.stdout.strip().split()
        mtime, size = int(parts[0]), int(parts[1])
        if not mtime:
            return 9999
        age = time.time() - mtime
        if size == 0:
            return age if age > STARTUP_GRACE_SECS else 0
        return age
    except Exception:
        return 9999


def _count_running_local():
    """Count only PARENT python workers (exclude forked children and bash wrappers)."""
    r = _run_local(
        "ps ax -o pid= -o ppid= -o command= | "
        "awk 'BEGIN{n=0} /autonomous_search\\.py/ && /python/ && !/grep/ && !/bash/{pid[NR]=$1;ppid[NR]=$2;row[NR]=1} "
        "END{for(i in row){p=pid[i];ok=1;for(j in row){if(ppid[i]==pid[j]){ok=0;break}};if(ok)n++};print n}'"
    )
    try:
        return int(r.stdout.strip())
    except Exception:
        return 0


def _count_running_remote(host):
    """Count only PARENT python workers on remote host (exclude forked children and bash wrappers)."""
    r = _run_remote(host, (
        'ps ax -o pid= -o ppid= -o command= | '
        'awk \'BEGIN{n=0} /autonomous_search\\.py/ && /python/ && !/grep/ && !/bash/{pid[NR]=$1;ppid[NR]=$2;row[NR]=1} '
        'END{for(i in row){p=pid[i];ok=1;for(j in row){if(ppid[i]==pid[j]){ok=0;break}};if(ok)n++};print n}\''
    ))
    try:
        return int(r.stdout.strip())
    except Exception:
        return 0


def _kill_stale_local(log_path):
    r = _run_local(f"ps aux | grep autonomous_search.py | grep -v grep | awk '{{print $2}}'")
    for pid in r.stdout.strip().split():
        # Check if this PID's log matches the stale log
        pr = _run_local(f"lsof -p {pid} 2>/dev/null | grep {Path(log_path).name}")
        if pr.stdout.strip():
            _run_local(f"kill -9 {pid}")
            print(f"  Killed stale local PID {pid} (log: {Path(log_path).name})", flush=True)


def _kill_all_stale_remote(host):
    _run_remote(host, "pkill -9 -f autonomous_search.py 2>/dev/null; sleep 1")
    print(f"  Killed all autonomous workers on {host}", flush=True)


def _start_worker_local(cfg, seed, state, machine_key):
    log = cfg["log_template"].format(seed=seed)
    out_dir = cfg["out_dir_template"].format(seed=seed)
    cmd = (
        f"nohup {cfg['python']} -u {cfg['script']} "
        f"--mode {cfg['mode']} --symbols {cfg['symbols']} "
        f"--start {cfg['start']} --npz-dir {cfg['npz_dir']} "
        f"--bh-accumulated-gain-pct {cfg['bh_pct']} "
        f"--target-gain-abs {cfg['target_gain']} "
        f"--target-sharpe-min {cfg['target_sharpe']} "
        f"--max-iter-seconds {cfg['max_iter_seconds']} "
        f"--out-dir {out_dir} --n-max 1000000 "
        f"--base-overrides-json {cfg['baseline_json']} "
        f"--seed {seed} "
        f"{cfg['extra_flags']} "
        f"</dev/null >{log} 2>&1 & disown"
    )
    r = _run_local(cmd, cwd=cfg["cwd"])
    state[machine_key]["used_seeds"].append(seed)
    state[machine_key]["next_seed"] = seed + 1
    print(f"  Started local worker seed={seed} log={Path(log).name}", flush=True)
    # Lower priority to not compete with live trading
    time.sleep(1)
    _run_local("renice -n 15 $(pgrep -f 'autonomous_search.py' | tail -1) 2>/dev/null")


def _launch_one_remote(host, cfg, seed):
    """Start a single worker via SSH. SSH may time out waiting for Python to load (normal — process IS running)."""
    log = cfg["log_template"].format(seed=seed)
    out_dir = cfg["out_dir_template"].format(seed=seed)
    cmd = (
        f"cd {cfg['cwd']} && "
        f"nohup {cfg['python']} -u {cfg['script']} "
        f"--mode {cfg['mode']} --symbols {cfg['symbols']} "
        f"--start {cfg['start']} --npz-dir {cfg['npz_dir']} "
        f"--bh-accumulated-gain-pct {cfg['bh_pct']} "
        f"--target-gain-abs {cfg['target_gain']} "
        f"--target-sharpe-min {cfg['target_sharpe']} "
        f"--max-iter-seconds {cfg['max_iter_seconds']} "
        f"--out-dir {out_dir} --n-max 1000000 "
        f"--base-overrides-json {cfg['baseline_json']} "
        f"--seed {seed} "
        f"{cfg['extra_flags']} "
        f"</dev/null >{log} 2>&1 & disown"
    )
    try:
        _run_remote(host, cmd, timeout=20, capture=False)
    except subprocess.TimeoutExpired:
        # Python takes >15s to load NPZ data — SSH times out but the process IS running.
        # Log a note and treat as probable success; next watchdog run verifies.
        print(f"  (SSH timed out launching seed={seed} — process likely running, will verify next cycle)", flush=True)
    return seed


def _start_workers_remote_batch(host, cfg, seeds, state, machine_key):
    """Start multiple workers in parallel SSH sessions (one per worker)."""
    with ThreadPoolExecutor(max_workers=len(seeds)) as ex:
        futures = {ex.submit(_launch_one_remote, host, cfg, seed): seed for seed in seeds}
        for fut in as_completed(futures, timeout=60):
            seed = futures[fut]
            try:
                fut.result()
                state[machine_key]["used_seeds"].append(seed)
                state[machine_key]["next_seed"] = max(state[machine_key]["next_seed"], seed + 1)
                print(f"  Started remote {host} worker seed={seed}", flush=True)
            except subprocess.TimeoutExpired:
                # Already handled inside _launch_one_remote — still treat as launched
                state[machine_key]["used_seeds"].append(seed)
                state[machine_key]["next_seed"] = max(state[machine_key]["next_seed"], seed + 1)
            except Exception as e:
                print(f"  Launch error seed={seed} on {host}: {e}", flush=True)


def _start_worker_remote(host, cfg, seed, state, machine_key):
    _start_workers_remote_batch(host, cfg, [seed], state, machine_key)


def check_and_fix_machine(machine_key, cfg, state):
    host = cfg["host"]
    n_wanted = cfg["n_workers"]
    is_local = (host == "local")

    print(f"\n[{NOW}] Checking {machine_key} (want {n_wanted} workers)...", flush=True)

    # --- Log-based freshness check (primary — immune to process-counting races) ---
    used = state[machine_key].get("used_seeds", [])
    active_seeds = used[-n_wanted:] if len(used) >= n_wanted else used

    fresh_count = 0
    stale_seeds = []
    for seed in active_seeds:
        log_path = cfg["log_template"].format(seed=seed)
        if is_local:
            age = _log_age_local(log_path)
        else:
            try:
                age = _log_age_remote(host, log_path)
            except Exception:
                age = 9999
        if age <= LOG_STALE_SECS:
            fresh_count += 1
        else:
            if age < 9999:
                print(f"  STALE: seed={seed} log age={age:.0f}s (>{LOG_STALE_SECS}s threshold)", flush=True)
            stale_seeds.append(seed)

    # --- Process-count check (secondary — used only for over-capacity detection) ---
    if is_local:
        n_running = _count_running_local()
    else:
        try:
            n_running = _count_running_remote(host)
        except Exception as e:
            print(f"  SSH ERROR checking {host}: {e}", flush=True)
            n_running = fresh_count  # fall back to log-based count

    print(f"  procs={n_running} fresh_logs={fresh_count}/{n_wanted}", flush=True)

    # Over-capacity: kill all only if also stale (running processes but no fresh logs = zombie duplicates)
    if n_running > n_wanted + 1 and fresh_count < n_wanted:
        print(f"  Over-capacity ({n_running} procs > {n_wanted+1}) with stale logs — killing all, restarting clean", flush=True)
        if is_local:
            _run_local("pkill -9 -f autonomous_search.py 2>/dev/null")
        else:
            try:
                _kill_all_stale_remote(host)
            except Exception as e:
                print(f"  Kill error: {e}", flush=True)
        time.sleep(2)
        n_running = 0
        fresh_count = 0
        stale_seeds = list(active_seeds)

    # Stale logs: kill all and restart
    if stale_seeds and fresh_count == 0:
        print(f"  {len(stale_seeds)} stale worker(s) detected — killing all and restarting", flush=True)
        if is_local:
            _run_local("pkill -9 -f autonomous_search.py 2>/dev/null")
        else:
            try:
                _kill_all_stale_remote(host)
            except Exception as e:
                print(f"  Kill error: {e}", flush=True)
        time.sleep(2)
        n_running = 0
        fresh_count = 0
    elif stale_seeds:
        print(f"  {len(stale_seeds)} stale + {fresh_count} fresh — restarting stale only", flush=True)
        if is_local:
            _run_local("pkill -9 -f autonomous_search.py 2>/dev/null")
        else:
            try:
                _kill_all_stale_remote(host)
            except Exception as e:
                print(f"  Kill error: {e}", flush=True)
        time.sleep(2)
        n_running = 0
        fresh_count = 0

    # Use log-based count as authoritative (immune to bash-wrapper race)
    effective = max(fresh_count, n_running)

    if effective >= n_wanted:
        print(f"  All {n_wanted} workers healthy", flush=True)
        return

    deficit = n_wanted - effective
    print(f"  Starting {deficit} new worker(s)", flush=True)
    if is_local:
        for _ in range(deficit):
            seed = state[machine_key]["next_seed"]
            _start_worker_local(cfg, seed, state, machine_key)
    else:
        seeds = [state[machine_key]["next_seed"] + i for i in range(deficit)]
        try:
            _start_workers_remote_batch(host, cfg, seeds, state, machine_key)
        except Exception as e:
            print(f"  Batch start error on {host}: {e}", flush=True)


def main():
    print(f"\n{'='*60}", flush=True)
    print(f"[WATCHDOG] {NOW}", flush=True)
    state = _load_state()
    for machine_key, cfg in MACHINES.items():
        try:
            check_and_fix_machine(machine_key, cfg, state)
        except Exception as e:
            print(f"  [WATCHDOG] ERROR on {machine_key}: {e}", flush=True)
        _save_state(state)
    print(f"[WATCHDOG] Done. State: mac_next={state['mac']['next_seed']} s1_next={state['s1']['next_seed']} s2_next={state['s2']['next_seed']}", flush=True)


if __name__ == "__main__":
    main()
