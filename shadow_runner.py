#!/usr/bin/env python3
"""shadow_runner.py — supervisor for shadow_executor processes.

Reads shadows/REGISTRY.json, spawns one subprocess per enabled shadow,
restarts crashes, gates tradier shadows by market hours (13:25–20:05 UTC),
and reloads the registry on SIGHUP.

Each shadow gets:
    SHADOW_CONFIG_ID=<id> SHADOW_PLATFORM=<crypto|tradier> SHADOW_ACCOUNT=<acct>
    python3 shadow_executor.py

PIDs in pids/shadow_<id>_<acct>.pid; logs in logs/shadow_<id>_<acct>.log.
Status JSON written every 30s to data/shadow_decisions/_runner_status.json.

USAGE:
    python3 shadow_runner.py              # foreground
    nohup python3 shadow_runner.py &      # background

To rotate configs: edit shadows/REGISTRY.json (set enabled=false / true), then
    kill -SIGHUP $(cat pids/shadow_runner.pid)
The runner will gracefully stop disabled shadows and start newly-enabled ones
on the next supervision tick (≤30s).
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "shadows" / "REGISTRY.json"
PID_DIR = BASE / "pids"
LOG_DIR = BASE / "logs"
STATUS_FILE = BASE / "data" / "shadow_decisions" / "_runner_status.json"
PID_DIR.mkdir(exist_ok=True); LOG_DIR.mkdir(exist_ok=True)
STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)

RUNNER_PID_FILE = PID_DIR / "shadow_runner.pid"
TICK_SECONDS = 30
TRADIER_OPEN_HHMM = (13, 25)   # UTC: 5min before market open
TRADIER_CLOSE_HHMM = (20, 5)   # UTC: 5min after market close
PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
EXECUTOR = str(BASE / "shadow_executor.py")

_reload_requested = False
_shutdown_requested = False
_processes: dict[str, subprocess.Popen] = {}     # id_acct → Popen
_registry_cache: dict = {}

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"[{ts}] runner: {msg}"
    print(line, flush=True)

def _utc_now_hhmm() -> tuple[int, int]:
    n = datetime.now(timezone.utc)
    return (n.hour, n.minute)

def _is_tradier_hours() -> bool:
    now = _utc_now_hhmm()
    return TRADIER_OPEN_HHMM <= now <= TRADIER_CLOSE_HHMM

def _load_registry() -> dict:
    global _registry_cache
    try:
        with open(REGISTRY) as f:
            _registry_cache = json.load(f)
    except Exception as e:
        _log(f"FAILED to load registry: {e}")
        if not _registry_cache:
            _log("no cached registry — exiting")
            sys.exit(2)
    return _registry_cache

def _shadow_key(cfg_id: str, account: str) -> str:
    return f"{cfg_id}__{account}"

def _spawn(cfg_id: str, platform: str, account: str) -> subprocess.Popen | None:
    key = _shadow_key(cfg_id, account)
    log_path = LOG_DIR / f"shadow_{cfg_id}_{account}.log"
    env = os.environ.copy()
    env["SHADOW_CONFIG_ID"] = cfg_id
    env["SHADOW_PLATFORM"] = platform
    env["SHADOW_ACCOUNT"] = account
    # Make sure the shadow process never tries to participate in launchd auto-restart
    env["SHADOW_MODE"] = "1"
    try:
        log_fp = open(log_path, "ab", buffering=0)
        p = subprocess.Popen(
            [PYTHON, "-u", EXECUTOR],
            env=env, stdout=log_fp, stderr=subprocess.STDOUT,
            cwd=str(BASE), close_fds=True, start_new_session=True,
        )
        with open(PID_DIR / f"shadow_{cfg_id}_{account}.pid", "w") as f:
            f.write(str(p.pid))
        _log(f"spawn {key} pid={p.pid} log={log_path.name}")
        return p
    except Exception as e:
        _log(f"spawn FAILED {key}: {e}")
        return None

def _stop(key: str, reason: str):
    p = _processes.pop(key, None)
    if not p: return
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        _log(f"stop  {key} pid={p.pid} reason={reason}")
    except ProcessLookupError:
        pass
    except Exception as e:
        _log(f"stop ERROR {key}: {e}")
    # Wait briefly for clean exit, then SIGKILL
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try: os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception: pass

def _supervise():
    """One supervision tick: reconcile desired vs actual shadow processes."""
    reg = _load_registry()
    desired: dict[str, dict] = {}
    for cfg_id, meta in reg.get("shadows", {}).items():
        if not meta.get("enabled"): continue
        platform = meta.get("platform"); account = meta.get("account")
        if platform not in ("crypto", "tradier") or not account:
            _log(f"skip {cfg_id}: bad platform/account")
            continue
        if platform == "tradier" and not _is_tradier_hours():
            continue  # market closed → don't spawn
        desired[_shadow_key(cfg_id, account)] = {"cfg_id": cfg_id, "platform": platform, "account": account}

    # Stop processes that should not be running
    for key in list(_processes.keys()):
        if key not in desired:
            _stop(key, "removed/disabled/market-closed")

    # Spawn missing processes; restart crashed ones
    for key, meta in desired.items():
        p = _processes.get(key)
        if p is None:
            np = _spawn(meta["cfg_id"], meta["platform"], meta["account"])
            if np: _processes[key] = np
        elif p.poll() is not None:
            _log(f"crashed {key} exit_code={p.returncode} — restarting")
            np = _spawn(meta["cfg_id"], meta["platform"], meta["account"])
            if np: _processes[key] = np

    # Write status snapshot
    try:
        status = {
            "ts": time.time(),
            "ts_iso": datetime.now(timezone.utc).isoformat(),
            "tradier_hours": _is_tradier_hours(),
            "active": {k: {"pid": p.pid, "alive": p.poll() is None} for k, p in _processes.items()},
            "desired_count": len(desired),
        }
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        _log(f"status write failed: {e}")

def _on_sighup(signum, frame):
    global _reload_requested
    _reload_requested = True
    _log("SIGHUP received → registry reload queued")

def _on_term(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    _log(f"signal {signum} received → shutting down all shadows")

def main():
    # Singleton check
    if RUNNER_PID_FILE.exists():
        try:
            old = int(RUNNER_PID_FILE.read_text().strip())
            os.kill(old, 0)
            _log(f"another runner is alive (pid={old}) — refusing to start")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            RUNNER_PID_FILE.unlink(missing_ok=True)
    RUNNER_PID_FILE.write_text(str(os.getpid()))

    signal.signal(signal.SIGHUP, _on_sighup)
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    _log(f"starting; registry={REGISTRY}; tick={TICK_SECONDS}s")
    try:
        while not _shutdown_requested:
            global _reload_requested
            if _reload_requested:
                _reload_requested = False
                _log("reloading registry")
            _supervise()
            # Sleep in 1s slices so signals respond quickly
            for _ in range(TICK_SECONDS):
                if _shutdown_requested or _reload_requested: break
                time.sleep(1)
    finally:
        _log("stopping all shadows")
        for k in list(_processes.keys()):
            _stop(k, "runner shutdown")
        try: RUNNER_PID_FILE.unlink()
        except FileNotFoundError: pass
        _log("exit")

if __name__ == "__main__":
    main()
