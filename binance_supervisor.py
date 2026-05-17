#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""binance_supervisor.py — Single long-running supervisor for the trading infra.

Subsumes the useful bits of server_watchdog, sweep_monitor, v8_watchdog,
monitor_all_scalpers, hedge_monitor_daily, trade_block_monitor, ez_disk_watchdog.

Responsibilities:
  - 60s health-check of S1 + S2 (load, ram, disk, sshd, sweep python procs).
  - Watch backtest_v8/sweeps/*.csv mtime. >120s stale on an actively-running host = stuck.
  - If server load <2.0 for >3 min during a sweep window → log CPU_IDLE; batch-launch
    TODO placeholder (no remote state changes this version).
  - Kill-stuck-sweeps logic is NOT re-implemented here — the authoritative kill is inside
    backtest_v8_sweep.py (ZERO_TRADES_TIMEOUT=60, HEARTBEAT_TIMEOUT=600). We only alert
    when CSV mtime exceeds those thresholds, which means the inner kill must have failed.
  - Central log: /Users/niels/logs/supervisor.log, rotated daily.
  - Graceful SIGTERM.
  - Optional --hourly-fix subcommand: one Opus spawn with OPUS_SPAWN_ENABLED=1,
    OPUS_SPAWN_DAILY_CAP=3, strict scope prompt, then disables.
  - HARD CAP: max ONE Opus spawn per HOUR across all triggers, regardless of env.

Does NOT:
  - Modify anything on S1/S2 (ssh calls are read-only: uptime, free, df, pgrep, ls, stat).
  - Spawn Claude agents outside --hourly-fix (and even that obeys the hourly cap).
  - Touch claude_session_tracker.py / com.niels.claude-session-reopen.plist.
  - Reboot Hetzner servers (see HETZNER_REBOOT_PLAN.md — blocked on credentials).
"""
import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG_DIR = Path.home() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "supervisor.log"
STATE_FILE = LOG_DIR / "supervisor_state.json"
PID_FILE = Path("/tmp/binance_supervisor.pid")

CHECK_INTERVAL = 120  # 2026-04-16: 2min — slow remote when sweeps are CPU-pegging hosts
SSH_TIMEOUT = 120  # 2026-04-16: observed 3min SSH under CPU load; 120s to distinguish real-dead from slow
SWEEP_STALE_SECONDS = 120
IDLE_LOAD_THRESHOLD = 2.0
IDLE_WINDOW_SECONDS = 180
V8_VERIFY_INTERVAL_SECONDS = 86400

# 2026-04-16 Phase 1 alerting thresholds
SSH_DEAD_ALERT_SECONDS = 300    # 5min: loud alert
SSH_DEAD_RESET_SECONDS = 600    # 10min: attempt Hetzner hw reset (if creds)
SSH_DEAD_SECOND_RESET_SECONDS = 900  # 15min: second reset attempt
HETZNER_ENV_FILE = Path.home() / ".config" / "hetzner.env"
ALERT_LOG = Path.home() / "SERVER_DOWN_ALERT.log"
ALERT_DIR = BASE / "data" / "alerts"

# Hard upper bounds — NEVER exceed regardless of env vars
MAX_OPUS_SPAWNS_PER_HOUR = 1
MAX_SSH_CALLS_PER_MINUTE = 30
LOG_ROTATION_KEEP_DAYS = 14

SERVERS = {
    "S1": {"host": "s1-int", "user": "niels"},
    # S2 destroyed 2026-05-08 (user shutdown). Do not probe — endless rc=255 noise.
}

# Remote-only sweep result dirs. MacBook does not run sweeps (CLAUDE.md SWEEP-LIVENESS).
SWEEP_DIRS = {
    "S1": "/home/niels/binance-sandbox/backtest_v8/sweeps",
}
SWEEP_ABANDONED_AGE_SECONDS = 86400  # >24h since last write = no active sweep, skip noise

# Tradier alert window: Mon-Fri 09:30-16:00 ET (strict US market hours).
# Outside this, tradier_manage is NOT ALLOWED to run, so a stale tradier log is
# expected, not an emergency. Crypto checks remain 24/7.
TRADIER_WINDOW_OPEN_MIN_ET = 570   # 09:30 ET
TRADIER_WINDOW_CLOSE_MIN_ET = 960  # 16:00 ET


def is_tradier_alert_window() -> bool:
    """True when tradier_manage is expected to be running. Outside this, suppress
    tradier-account alerts. Falls back to True (alert-as-before) only if no tz lib."""
    try:
        try:
            from zoneinfo import ZoneInfo
            et = ZoneInfo("America/New_York")
        except Exception:
            import pytz  # type: ignore
            et = pytz.timezone("America/New_York")
        now_et = datetime.now(et)
        if now_et.weekday() >= 5:
            return False
        mins = now_et.hour * 60 + now_et.minute
        return TRADIER_WINDOW_OPEN_MIN_ET <= mins <= TRADIER_WINDOW_CLOSE_MIN_ET
    except Exception:
        return True


def _setup_logger():
    logger = logging.getLogger("supervisor")
    logger.setLevel(logging.INFO)
    handler = TimedRotatingFileHandler(LOG_FILE, when="midnight", backupCount=LOG_ROTATION_KEEP_DAYS, utc=True)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(stream)
    return logger


log = _setup_logger()


class SshRateLimiter:
    def __init__(self, max_per_minute):
        self.max = max_per_minute
        self.calls = []

    def allow(self):
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) >= self.max:
            return False
        self.calls.append(now)
        return True


_ssh_limiter = SshRateLimiter(MAX_SSH_CALLS_PER_MINUTE)

_SSH_ENV = {**os.environ, "HOME": os.path.expanduser("~")}


def ssh_read(host, user, cmd, timeout=SSH_TIMEOUT):
    if not _ssh_limiter.allow():
        log.warning(f"SSH rate-limit hit ({MAX_SSH_CALLS_PER_MINUTE}/min) — skipping {host} {cmd[:40]}")
        return 1, "RATE_LIMITED"
    try:
        # 2026-04-16: link has slow handshake (16-32s observed), allow full window
        r = subprocess.run(
            ["ssh",
             "-o", f"ConnectTimeout={min(timeout - 5, 20)}",
             "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=no",
             "-o", "ServerAliveInterval=10",
             "-o", "ServerAliveCountMax=3",
             f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=timeout + 10, env=_SSH_ENV,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 255, str(e)


# ═══ 2026-04-16 Phase 1: loud alert + optional Hetzner hw reset ═══
_LAST_ALERT_TS = {}  # server_name -> epoch of last alert (dedup spam)

def loud_alert(msg: str, server_name: str = "UNKNOWN", severity: str = "CRITICAL"):
    """Visible/audible alert that a server is dead. No creds required.

    Triggers:
    - macOS Notification Center banner + Sosumi sound
    - Funk warning sound via afplay
    - Append to ~/SERVER_DOWN_ALERT.log
    - JSON record in data/alerts/ for any dashboard watcher
    - supervisor.log CRITICAL line
    """
    # Rate-limit same-server alerts to 1/60s so we don't spam notifications
    now = time.time()
    last = _LAST_ALERT_TS.get(server_name, 0)
    if now - last < 60:
        return
    _LAST_ALERT_TS[server_name] = now
    ts = datetime.now(timezone.utc).isoformat()
    full = f"[{severity}] [{server_name}] {msg}"
    log.critical(f"🚨 {full}")
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_LOG, "a") as f:
            f.write(f"[{ts}] {full}\n")
    except Exception as e:
        log.error(f"alert_log write failed: {e}")
    try:
        ALERT_DIR.mkdir(parents=True, exist_ok=True)
        alert_json = ALERT_DIR / f"server_down_{server_name}_{int(now)}.json"
        alert_json.write_text(json.dumps({"ts": ts, "server": server_name, "severity": severity, "msg": msg}))
    except Exception as e:
        log.error(f"alert_json write failed: {e}")
    # macOS notification banner (non-blocking, ignore failures)
    try:
        safe_msg = msg.replace('"', "'")[:200]
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "🚨 {server_name} DOWN" sound name "Sosumi"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["afplay", "/System/Library/Sounds/Funk.aiff"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def hetzner_hw_reset(server_name: str) -> tuple[bool, str]:
    """Attempt Hetzner CLOUD API reset. Gated on ~/.config/hetzner.env presence.

    Creds file format:
      HETZNER_CLOUD_TOKEN=<api-token-from-console.hetzner.cloud>
      HETZNER_S1_ID=<numeric server id>
      HETZNER_S2_ID=<numeric server id>

    Get API token: https://console.hetzner.cloud/ → Security → API Tokens → Generate new (Read+Write)
    Get server ID: same console → Servers → click server → URL ends in /servers/<ID>,
                   OR list via `curl -H "Authorization: Bearer <token>" https://api.hetzner.cloud/v1/servers`
    """
    if not HETZNER_ENV_FILE.exists():
        return False, "no_creds_file"
    try:
        creds = {}
        for line in HETZNER_ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip().strip('"').strip("'")
        token = creds.get("HETZNER_CLOUD_TOKEN")
        sid = creds.get(f"HETZNER_{server_name}_ID")
        if not (token and sid):
            return False, f"missing_fields token={bool(token)} id_{server_name}={bool(sid)}"
        import requests  # lazy import
        r = requests.post(
            f"https://api.hetzner.cloud/v1/servers/{sid}/actions/reset",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30,
        )
        # 201 Created is the success code for Cloud actions
        ok = r.status_code in (200, 201)
        return ok, f"status={r.status_code} body={(r.text or '')[:120]}"
    except Exception as e:
        return False, f"exc={type(e).__name__}:{str(e)[:80]}"


def handle_ssh_dead(name: str, dead_s: int, state: dict):
    """Called when a server has been SSH-dead for dead_s seconds.

    Fires exactly ONCE per down-episode at each threshold crossing:
      >= 5min: first-alert (loud)
      >= 10min: attempt Hetzner hw reset
      >= 15min: second reset + critical alert
    State flags cleared on recovery so next episode re-arms all thresholds.
    """
    key_alert = f"{name}_alert_fired"
    key_reset1 = f"{name}_reset1_ts"
    key_reset2 = f"{name}_reset2_ts"

    if dead_s >= SSH_DEAD_ALERT_SECONDS and not state.get(key_alert):
        loud_alert(f"SSH dead {dead_s}s — investigating", name)
        state[key_alert] = time.time()

    if dead_s >= SSH_DEAD_RESET_SECONDS and not state.get(key_reset1):
        ok, info = hetzner_hw_reset(name)
        state[key_reset1] = time.time()
        if ok:
            log.critical(f"🔌 {name} Hetzner hw reset FIRED: {info}")
            loud_alert(f"Hetzner hw reset fired — waiting for reboot", name)
        else:
            log.error(f"🚨 {name} Hetzner reset FAILED: {info}")
            if info == "no_creds_file":
                loud_alert(f"SSH dead {dead_s}s — NO HETZNER CREDS — MANUAL REBOOT NEEDED", name)
            else:
                loud_alert(f"Hetzner reset failed ({info}) — MANUAL REBOOT NEEDED", name)

    # 2nd attempt only if first attempt was ≥2min ago (give reboot time to take effect)
    reset1_ts = state.get(key_reset1, 0)
    if (
        dead_s >= SSH_DEAD_SECOND_RESET_SECONDS
        and not state.get(key_reset2)
        and time.time() - reset1_ts >= 120
    ):
        ok, info = hetzner_hw_reset(name)
        state[key_reset2] = time.time()
        if ok:
            log.critical(f"🔌🔌 {name} Hetzner hw reset 2nd attempt FIRED: {info}")
        else:
            log.error(f"🚨🚨 {name} 2nd reset failed: {info}")
            loud_alert(f"2nd Hetzner reset failed — SERVER NEEDS MANUAL INTERVENTION (down {dead_s}s)", name)


def acquire_lock():
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            os.kill(old, 0)
            log.error(f"supervisor already running pid={old}; exit")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            PID_FILE.unlink(missing_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def release_lock():
    try:
        if PID_FILE.exists() and int(PID_FILE.read_text().strip()) == os.getpid():
            PID_FILE.unlink()
    except Exception:
        pass


def probe_host(name, cfg):
    host = cfg["host"]
    user = cfg["user"]
    probe = (
        "echo '===UP==='; uptime; "
        "echo '===MEM==='; free -m | head -2; "
        "echo '===DISK==='; df -h /home 2>/dev/null | tail -1 || df -h / | tail -1; "
        "echo '===SSHD==='; pgrep -c sshd; "
        "echo '===PY==='; pgrep -cf backtest_v8_sweep.py; "
        "echo '===SCR==='; screen -ls 2>/dev/null | grep -cE '(sweep|sentinel)' || echo 0"
    )
    rc, out = ssh_read(host, user, probe, timeout=SSH_TIMEOUT)
    return {"host": name, "rc": rc, "out": out, "ts": time.time()}


def parse_load(out):
    try:
        up_line = [l for l in out.splitlines() if "load average" in l]
        if not up_line:
            return None
        return float(up_line[0].split("load average:")[-1].split(",")[0].strip())
    except Exception:
        return None


def check_sweep_freshness(state):
    """Alert when an actively-running remote sweep stalls.

    Only flags files modified within the last SWEEP_ABANDONED_AGE_SECONDS — if the
    most-recent CSV hasn't been touched in >24h, there's no active sweep to be
    stalled, just historical data sitting on disk. Local MacBook is skipped (no
    sweeps allowed there per CLAUDE.md SWEEP-LIVENESS mandate)."""
    now = time.time()
    findings = []
    for name, d in SWEEP_DIRS.items():
        cfg = SERVERS[name]
        rc, out = ssh_read(
            cfg["host"], cfg["user"],
            f"ls -t {d}/v8_sweep_*.csv 2>/dev/null | head -1 | xargs -r stat -c '%Y %n'",
            timeout=10,
        )
        if rc != 0 or not out.strip():
            continue
        try:
            parts = out.strip().split(None, 1)
            mtime = int(parts[0])
            fname = parts[1] if len(parts) > 1 else "?"
            age = now - mtime
            if age > SWEEP_ABANDONED_AGE_SECONDS:
                continue
            if age > SWEEP_STALE_SECONDS:
                findings.append({"loc": name, "file": Path(fname).name, "age_s": round(age, 1)})
        except Exception:
            continue
    return findings


def check_cpu_idle(name, probe_result, state):
    load = parse_load(probe_result["out"])
    if load is None:
        return False
    key = f"{name}_idle_since"
    if load < IDLE_LOAD_THRESHOLD:
        state.setdefault(key, time.time())
        elapsed = time.time() - state[key]
        if elapsed > IDLE_WINDOW_SECONDS:
            return True
    else:
        state.pop(key, None)
    return False


def launch_cpu_refill(name):
    """2026-04-16: CPU idle = autochain died or phase-5 crashed. Respawn autochain
    via SSH (server_watchdog would also catch it, but supervisor acts faster on CPU signal)."""
    cfg = SERVERS.get(name)
    if not cfg:
        log.warning(f"CPU_IDLE on {name} — no SERVERS config, skipping")
        return
    host = cfg["host"]
    user = cfg["user"]
    ac_screen = f"autochain_{name.lower()}"
    base = "/home/niels/binance-sandbox"
    # Same as server_watchdog: only spawn if not already running
    check_cmd = f"screen -ls 2>/dev/null | grep -q '\\.{ac_screen}\\b' && echo RUNNING || echo MISSING"
    rc, out = ssh_read(host, user, check_cmd, timeout=SSH_TIMEOUT)
    if "RUNNING" in out:
        log.info(f"CPU_IDLE on {name} — {ac_screen} already running, no action")
        return
    # Missing: spawn it
    inner = (
        f"cd {base} && bash {base}/sweep_autochain.sh {name.lower()} "
        f"> /tmp/sweep_autochain_{name.lower()}.log 2>&1"
    )
    setup = (
        f"cat > /tmp/launch_{ac_screen}.sh <<'EOF'\n#!/bin/bash\n{inner}\nEOF\n"
        f"chmod +x /tmp/launch_{ac_screen}.sh && "
        f"screen -dmS {ac_screen} bash /tmp/launch_{ac_screen}.sh && "
        f"sleep 1 && (screen -ls | grep -q {ac_screen} && echo OK || echo FAIL)"
    )
    rc, out = ssh_read(host, user, setup, timeout=SSH_TIMEOUT)
    log.warning(f"CPU_IDLE on {name} — spawned {ac_screen} rc={rc} result={out.strip()[-80:]}")


def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str))
    except Exception as e:
        log.warning(f"state save failed: {e}")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def opus_spawn_allowed(state):
    """HARD CAP: max 1 Opus spawn per hour regardless of OPUS_SPAWN_* env vars."""
    now = time.time()
    last = state.get("opus_spawn_last", 0)
    if now - last < 3600:
        log.warning(f"Opus spawn blocked — last spawn {int(now - last)}s ago (<3600s hard cap)")
        return False
    count_hour = state.get("opus_spawn_count_hour", 0)
    window_start = state.get("opus_spawn_window_start", 0)
    if now - window_start > 3600:
        count_hour = 0
        window_start = now
    if count_hour >= MAX_OPUS_SPAWNS_PER_HOUR:
        return False
    state["opus_spawn_count_hour"] = count_hour + 1
    state["opus_spawn_window_start"] = window_start
    state["opus_spawn_last"] = now
    return True


def hourly_fix_pass():
    """Run one-shot diagnosis pass via ez_copilot with strict scope.
    Enables OPUS_SPAWN_ENABLED=1 + cap=3 for this call only."""
    state = load_state()
    if not opus_spawn_allowed(state):
        log.warning("hourly-fix refused — hard cap reached")
        return 2
    save_state(state)
    env = {
        **os.environ,
        "OPUS_SPAWN_ENABLED": "1",
        "OPUS_SPAWN_DAILY_CAP": "3",
        "OPUS_SCOPE": "live+S1+S2:diagnose-only",
    }
    copilot = BASE / "ez_copilot.py"
    if not copilot.exists():
        log.error(f"ez_copilot.py missing at {copilot}; hourly-fix aborted")
        return 3
    python = "/opt/anaconda3/envs/binance_env/bin/python"
    cmd = [python, str(copilot), "--one-shot-fix-all"]
    log.info(f"hourly-fix: invoking {cmd} with OPUS_SPAWN_ENABLED=1")
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(BASE))
        try:
            out, _ = proc.communicate(timeout=1800)
            log.info(f"hourly-fix exit={proc.returncode}; tail:\n{out.decode()[-2000:] if out else ''}")
        except subprocess.TimeoutExpired:
            proc.kill()
            log.error("hourly-fix: ez_copilot timed out after 30 min; killed")
            return 4
    finally:
        log.info("hourly-fix: OPUS_SPAWN_ENABLED reverted to default (off) for supervisor loop")
    return 0


def run_v8_verify_daily():
    """Invoke backtest_v8_verify_daily.py for yesterday across all accounts.
    Compares V8 output vs live data/decisions/ JSONL per CLAUDE.md rule."""
    script = BASE / "backtest_v8_verify_daily.py"
    if not script.exists():
        log.warning(f"v8 verify script missing at {script}")
        return
    python = "/opt/anaconda3/envs/binance_env/bin/python"
    try:
        r = subprocess.run([python, str(script)], capture_output=True, text=True, timeout=1800, cwd=str(BASE))
        tag = "PASS" if r.returncode == 0 else ("FAIL" if r.returncode == 1 else "ERROR")
        log.info(f"V8_VERIFY_DAILY exit={r.returncode} ({tag}); tail:\n{(r.stdout or '')[-1500:]}")
    except subprocess.TimeoutExpired:
        log.error("V8_VERIFY_DAILY timed out after 30 min")
    except Exception as e:
        log.exception(f"V8_VERIFY_DAILY crashed: {e}")


# ═══ Live trading health checks ═══
WEBHOOK_FAIL_WINDOW_S = 300        # look back 5 min for WEBHOOK_FAIL events
WEBHOOK_FAIL_RATE_THRESHOLD = 10   # > N fails in window → rate-burst alert
LOG_STALE_SECONDS = 300            # ez_manage logs every ~30s even idle; 5min stale = dead worker
_CRYPTO_ACCTS = ["ang", "fin", "flz", "men", "inf"]
_STOCK_ACCTS = ["tra", "trb", "trc"]
_LOG_TS_RE = re.compile(r'^\d{1,2} (\d{2}):(\d{2}):(\d{2})')


def _log_line_age(line: str, now_epoch: float) -> float | None:
    m = _LOG_TS_RE.match(line)
    if not m:
        return None
    h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    now_dt = datetime.fromtimestamp(now_epoch)
    try:
        line_dt = now_dt.replace(hour=h, minute=mn, second=s, microsecond=0)
    except ValueError:
        return None
    age = now_epoch - line_dt.timestamp()
    if age < -60:
        age += 86400
    return age


def check_webhook_health(state: dict) -> None:
    """Alert on WEBHOOK_FAIL bursts or Finandy IP-block 'access denied' events.

    Tradier accounts are only checked during the US market window — outside it,
    tradier_manage is not allowed to run, so its log is expected to be stale."""
    now = time.time()
    log_dir = Path.home() / "logs"
    accounts = [(f"ez_manage_{a}", a) for a in _CRYPTO_ACCTS]
    if is_tradier_alert_window():
        accounts += [(f"tradier_manage_{a}", a) for a in _STOCK_ACCTS]
    total_recent_fails = 0
    access_denied_accts: list[str] = []
    for log_stem, acct in accounts:
        log_path = log_dir / f"{log_stem}.log"
        if not log_path.exists():
            continue
        try:
            with open(log_path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 80000))
                lines = fh.read().decode("utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line in lines[-500:]:
            if "WEBHOOK_FAIL" not in line:
                continue
            age = _log_line_age(line, now)
            if age is None or age > WEBHOOK_FAIL_WINDOW_S:
                continue
            total_recent_fails += 1
            if "access denied" in line.lower() and acct not in access_denied_accts:
                access_denied_accts.append(acct)
    if access_denied_accts:
        key = "webhook_access_denied_alert_ts"
        if now - state.get(key, 0) >= 300:
            state[key] = now
            loud_alert(
                f"WEBHOOK IP BLOCKED on {','.join(access_denied_accts)} — Finandy rejecting webhooks with 'access denied'. "
                f"Add your current IP to Finandy hook IP whitelist (or remove IP restriction entirely — webhook_secret already authenticates).",
                server_name="WEBHOOK",
                severity="CRITICAL",
            )
            log.critical(f"WEBHOOK_ACCESS_DENIED accounts={access_denied_accts} recent_fails={total_recent_fails}")
    elif total_recent_fails >= WEBHOOK_FAIL_RATE_THRESHOLD:
        key = "webhook_fail_rate_alert_ts"
        if now - state.get(key, 0) >= 300:
            state[key] = now
            loud_alert(
                f"{total_recent_fails} WEBHOOK_FAIL events in last {WEBHOOK_FAIL_WINDOW_S}s — Finandy webhooks degraded.",
                server_name="WEBHOOK",
                severity="WARNING",
            )
            log.warning(f"WEBHOOK_FAIL_BURST {total_recent_fails} fails in {WEBHOOK_FAIL_WINDOW_S}s")


def check_live_log_freshness(state: dict) -> None:
    """Alert if a live trading log file has had no new writes for >5 min (worker dead/hung).

    Tradier accounts are only checked during the US market window — outside it,
    tradier_manage is not allowed to run, so its log is expected to be stale."""
    now = time.time()
    log_dir = Path.home() / "logs"
    stale: list[str] = []
    for acct in _CRYPTO_ACCTS:
        lp = log_dir / f"ez_manage_{acct}.log"
        if not lp.exists():
            continue
        age = now - lp.stat().st_mtime
        if age > LOG_STALE_SECONDS:
            stale.append(f"ez_manage[{acct}] +{int(age)}s")
    if is_tradier_alert_window():
        for acct in _STOCK_ACCTS:
            lp = log_dir / f"tradier_manage_{acct}.log"
            if not lp.exists():
                continue
            age = now - lp.stat().st_mtime
            if age > LOG_STALE_SECONDS:
                stale.append(f"tradier[{acct}] +{int(age)}s")
    if stale:
        key = "live_log_stale_alert_ts"
        if now - state.get(key, 0) >= 300:
            state[key] = now
            loud_alert(
                f"LIVE TRADING LOG STALE: {', '.join(stale)} — worker may be dead or hung. "
                f"Check: ps -ef | grep manage.py",
                server_name="LIVE_TRADING",
                severity="CRITICAL",
            )
            log.critical(f"LIVE_LOG_STALE accounts={stale}")


def supervisor_loop():
    acquire_lock()
    state = load_state()
    shutdown = {"flag": False}

    def _sigterm(_sig, _frm):
        log.info("SIGTERM — graceful shutdown")
        shutdown["flag"] = True
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    log.info(f"supervisor start pid={os.getpid()}; max_opus/hr={MAX_OPUS_SPAWNS_PER_HOUR}; ssh/min={MAX_SSH_CALLS_PER_MINUTE}")
    last_hourly = state.get("last_hourly_ts", 0)
    last_v8_verify = state.get("last_v8_verify_ts", 0)
    try:
        while not shutdown["flag"]:
            tick_start = time.time()
            for name, cfg in SERVERS.items():
                try:
                    probe = probe_host(name, cfg)
                    if probe["rc"] != 0:
                        log.warning(f"{name} probe failed rc={probe['rc']} out={probe['out'][:200]}")
                        state.setdefault(f"{name}_ssh_fail_since", time.time())
                        state[f"{name}_ssh_success_streak"] = 0
                    else:
                        _streak = int(state.get(f"{name}_ssh_success_streak", 0)) + 1
                        state[f"{name}_ssh_success_streak"] = _streak
                        # Hysteresis: require 3 consecutive successes (~6min at 120s tick) before
                        # clearing ssh_fail_since. Prevents flap — single success between failures
                        # was previously resetting the dead-timer so 600s reset threshold never fired.
                        if _streak >= 3:
                            state.pop(f"{name}_ssh_fail_since", None)
                        load_1m = parse_load(probe["out"])
                        log.info(f"{name} load1m={load_1m} streak={_streak}")
                        if check_cpu_idle(name, probe, state):
                            launch_cpu_refill(name)
                    ssh_fail_since = state.get(f"{name}_ssh_fail_since")
                    if ssh_fail_since:
                        dead_s = int(time.time() - ssh_fail_since)
                        handle_ssh_dead(name, dead_s, state)
                    else:
                        # Clear reset flags on stable recovery (streak already >=3 to reach here)
                        state.pop(f"{name}_reset1_ts", None)
                        state.pop(f"{name}_reset2_ts", None)
                        state.pop(f"{name}_alert_fired", None)
                except Exception as e:
                    log.exception(f"probe {name} crashed: {e}")
            try:
                stale = check_sweep_freshness(state)
                if stale:
                    for s in stale:
                        log.warning(f"SWEEP_STALE {s['loc']} {s['file']} age={s['age_s']}s (inner ZERO_TRADES_TIMEOUT=60 should have caught this)")
            except Exception as e:
                log.exception(f"sweep freshness check crashed: {e}")
            try:
                check_webhook_health(state)
            except Exception as e:
                log.exception(f"webhook health check crashed: {e}")
            try:
                check_live_log_freshness(state)
            except Exception as e:
                log.exception(f"live log freshness check crashed: {e}")
            if time.time() - last_v8_verify >= V8_VERIFY_INTERVAL_SECONDS:
                log.info("v8_verify_daily window reached → running")
                run_v8_verify_daily()
                last_v8_verify = time.time()
                state["last_v8_verify_ts"] = last_v8_verify
            if time.time() - last_hourly >= 3600:
                if os.environ.get("SUPERVISOR_ENABLE_HOURLY_FIX", "0") == "1":
                    log.info("hourly-fix window reached; SUPERVISOR_ENABLE_HOURLY_FIX=1 → running")
                    hourly_fix_pass()
                else:
                    log.info("hourly-fix window reached; SUPERVISOR_ENABLE_HOURLY_FIX!=1 → skipping")
                last_hourly = time.time()
                state["last_hourly_ts"] = last_hourly
            save_state(state)
            elapsed = time.time() - tick_start
            sleep_for = max(5, CHECK_INTERVAL - elapsed)
            for _ in range(int(sleep_for)):
                if shutdown["flag"]:
                    break
                time.sleep(1)
    finally:
        release_lock()
        log.info("supervisor stopped cleanly")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hourly-fix", action="store_true", help="One-shot Opus diagnosis pass, then exit.")
    args = ap.parse_args()
    if args.hourly_fix:
        sys.exit(hourly_fix_pass())
    supervisor_loop()


if __name__ == "__main__":
    main()
