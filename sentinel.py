#!/usr/bin/env python3
# metrics_guard-clean: Sharpe used internally only, never emitted to user surface (audited 2026-04-30).
"""
sentinel.py — aggressive sweep/log/infra watcher.

Fires an incident (→ iTerm2 fix-agent tab on macbook) when ANY of:
  - DUPE_RESULTS: two sweep configs have identical Sharpe+PnL+Trades+W+L
  - DEAD_PARAMS: Sharpe stdev <0.01 across 10+ configs
  - ZERO_TRADES: sweep has >=5 configs, and ≥80% have trades=0
  - LOW_CPU:     a managed machine CPU idle >15% (i.e. utilization <85%)
  - LOG_ERROR:   Traceback/-1003/banned/poisoned in recent logs
  - HTTP_DOWN:   probe fails on 5050/5051

Single-instance (PID lock). Per-cycle heartbeat log. Tight timeouts.
Every agent spawn is verified (iTerm tab count before/after).
"""
import csv
import glob
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

HOST = socket.gethostname()
HOME = Path(os.path.expanduser("~"))
BASE = Path(__file__).resolve().parent
DATA = BASE / "data" / "sentinel"
INCIDENT_DIR = DATA / "incidents"
STATE_FILE = DATA / "state.json"
BACKUP_DIR = BASE / "backups" / "sentinel"
PID_FILE = Path("/tmp/sentinel.pid")
ACK_FILE = Path("/tmp/sentinel.ack")
for p in (INCIDENT_DIR, BACKUP_DIR, DATA):
    p.mkdir(parents=True, exist_ok=True)

POLL_SEC = int(os.environ.get("SENTINEL_POLL_SEC", "30"))
DEAD_STDEV = float(os.environ.get("SENTINEL_DEAD_STDEV", "0.01"))
DEAD_MIN_ROWS = int(os.environ.get("SENTINEL_DEAD_MIN_ROWS", "10"))
AUTO_FIX = os.environ.get("SENTINEL_AUTO_FIX", "1") == "1"
MAX_CONCURRENT_AGENTS = int(os.environ.get("SENTINEL_MAX_AGENTS", "2"))  # throttle: never spawn >N claude agents at once
FRESH_SEC = int(os.environ.get("SENTINEL_FRESH_SEC", "21600"))
HTTP_PORTS = [int(p) for p in os.environ.get("SENTINEL_HTTP_PORTS", "5050,5051").split(",") if p]
EXTERNAL_SITES = [
    # Public-facing websites on gateway 157.90.168.35 — must stay reachable from anywhere
    "https://niels.com/",
    "https://www.niels.com/",
    "https://niels.co/",
    "https://www.niels.co/",
    "https://flasherz.co/",
    "https://www.flasherz.co/",
    "http://niels.com/",
    "http://niels.co/",
    "http://flasherz.co/",
]
CPU_MIN = float(os.environ.get("SENTINEL_CPU_MIN", "85.0"))
SUBPROC_TO = int(os.environ.get("SENTINEL_SUBPROC_TIMEOUT", "8"))
ZERO_TRADES_MIN_CONFIGS = 5
ZERO_TRADES_RATIO = 0.80
DUPE_MIN_TRADES = int(os.environ.get("SENTINEL_DUPE_MIN_TRADES", "10"))
STUCK_SWEEP_AGE_SEC = int(os.environ.get("SENTINEL_STUCK_AGE_SEC", "300"))  # CSV not updated in 5min while screen alive = stuck

IS_MAC = sys.platform == "darwin"
CRITICAL_FILES = [
    "ez_manage.py", "ez_positions_service.py", "ez_positions_quick.py",
    "tradier_manage.py", "tradier_positions.py", "config.py", "config_tradier.py",
    "utils.py", "backtest_v8_engine.py", "backtest_v8_sweep.py",
]
SWEEP_CSV_GLOBS = [
    str(BASE / "backtest_v8" / "sweeps" / "v8_sweep_*.csv"),
    str(HOME / "binance" / "backtest_v8" / "sweeps" / "v8_sweep_*.csv"),
    str(HOME / "binance-sandbox" / "backtest_v8" / "sweeps" / "v8_sweep_*.csv"),
]
LOG_GLOBS = [
    str(HOME / "logs" / "*.log"),
    str(HOME / "binance" / "logs" / "*.log"),
    str(BASE / "logs" / "*.log"),
    "/tmp/v8_*.log",
    "/tmp/sentinel.log",
]
LOG_PATTERNS = ["Traceback (most recent", "-1003", "banned", "poisoned", "duplicate order", "orphaned symbol", "FATAL", "MemoryError"]
LOG_IGNORE = ["test ", "expected error", "INFO"]

REMOTE_HOSTS = [
    ("s1", "niels@s1-int"),
    ("s2", "niels@s2-int"),
]
MACHINES_FOR_CPU = [
    ("local", None),
    ("s1", "niels@s1-int"),
    ("s2", "niels@s2-int"),
]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


_RUN_ENV = {**os.environ, "HOME": os.path.expanduser("~")}


def run(cmd, timeout=SUBPROC_TO, shell=False):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=shell, env=_RUN_ENV)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 255, str(e)


def ssh(host, cmd, timeout=SUBPROC_TO):
    return run(
        ["ssh",
         "-o", f"ConnectTimeout={min(timeout-2,5)}",
         "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         "-o", "ControlMaster=no", "-o", "ControlPath=none",
         "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
         host, cmd],
        timeout=timeout,
    )


def acquire_lock():
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            os.kill(old, 0)
            log(f"already running pid={old}; exit")
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


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state))


def snapshot_critical(tag):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snap = BACKUP_DIR / f"{ts}_{tag}"
    snap.mkdir(parents=True, exist_ok=True)
    for f in CRITICAL_FILES:
        src = BASE / f
        if src.exists():
            try:
                (snap / f).write_bytes(src.read_bytes())
            except Exception:
                pass
    return snap


def iterm_tab_count():
    if not IS_MAC:
        return 0
    rc, out = run(["osascript", "-e", 'tell application "iTerm2" to count tabs of every window'], timeout=3)
    if rc != 0:
        return -1
    try:
        # Returns per-window counts joined, sum them
        return sum(int(x.strip().rstrip(",")) for x in out.split(",") if x.strip())
    except Exception:
        return -1


def active_agent_count():
    """Count currently running claude --dangerously-skip-permissions processes."""
    try:
        r = subprocess.run(["pgrep", "-f", "claude.*dangerously-skip-permissions"], capture_output=True, text=True)
        return len([l for l in r.stdout.splitlines() if l.strip()])
    except Exception:
        return 0


def spawn_iterm_agent(prompt, title_tag):
    """Spawn claude agent in new iTerm2 tab. Returns True if tab count increased."""
    if not IS_MAC or not AUTO_FIX:
        log(f"spawn skipped (mac={IS_MAC} auto_fix={AUTO_FIX})")
        return False
    active = active_agent_count()
    if active >= MAX_CONCURRENT_AGENTS:
        log(f"spawn throttled: {active}/{MAX_CONCURRENT_AGENTS} agents already running — skipping {title_tag}")
        return False
    prompt_file = INCIDENT_DIR / f"{title_tag}.prompt.txt"
    prompt_file.write_text(prompt)
    launcher = INCIDENT_DIR / f"{title_tag}.sh"
    launcher.write_text(
        "#!/bin/bash\n"
        f"cd {BASE}\n"
        f"echo 'SENTINEL FIX AGENT — {title_tag}'\n"
        f"claude --dangerously-skip-permissions \"$(cat {prompt_file})\"\n"
    )
    launcher.chmod(0o755)
    before = iterm_tab_count()
    applescript = (
        'tell application "iTerm2"\n'
        '  activate\n'
        '  if (count of windows) = 0 then\n'
        '    create window with default profile\n'
        '  end if\n'
        '  tell current window\n'
        '    create tab with default profile\n'
        '    tell current session of current tab\n'
        f'      write text "bash {launcher}"\n'
        '    end tell\n'
        '  end tell\n'
        'end tell'
    )
    rc, out = run(["osascript", "-e", applescript], timeout=5)
    time.sleep(1)
    after = iterm_tab_count()
    ok = (rc == 0) and (after > before)
    log(f"spawn {'OK' if ok else 'FAIL'}: rc={rc} tabs {before}->{after} title={title_tag} err={out[:120]}")
    return ok


def build_prompt(incident_path, kind, detail, snap):
    hints = {
        "DUPE_RESULTS": (
            f"INCIDENT TYPE: DUPE_RESULTS on csv={detail.get('csv')}. "
            f"Configs with identical metrics: {detail.get('config_names', [])[:5]}. "
            f"Fingerprint (sharpe,pnl,trades,wins,losses) = {detail.get('fingerprint')}. "
            "A switch that DIFFERS between these configs is DEAD. Per CLAUDE.md DEATH PENALTY: "
            "IMPLEMENT the dead switch in backtest_v8_engine.py override section "
            "(module, class, dataclass default, AND all instances). FORBIDDEN: removing the switch "
            "from backtest_v8_sweep.py, disabling it, marking it dead."
        ),
        "DEAD_PARAMS": (
            f"INCIDENT TYPE: DEAD_PARAMS csv={detail.get('csv')} sharpe_stdev={detail.get('sharpe_stdev')}. "
            "Multiple knobs produce no variance — implement them (never prune)."
        ),
        "ZERO_TRADES": (
            f"INCIDENT TYPE: ZERO_TRADES csv={detail.get('csv')} zero_pct={detail.get('zero_pct')}. "
            "Sweep is producing 0 trades for most configs — entry gates are blocking everything. "
            "Investigate backtest_v8_engine.py entry path (real_eta, should_enter, WT_DC_ENTRY_THRESHOLD). "
            "For crypto mode: check V8 crypto known gap — W/M timeframes missing, field type mismatches."
        ),
        "LOW_CPU": (
            f"INCIDENT TYPE: LOW_CPU machine={detail.get('machine')} cpu_util={detail.get('cpu_util')}%. "
            f"Expected ≥{CPU_MIN}%. Something is underutilizing cycles. Check stuck/waiting backtest "
            "procs (are workers blocked on I/O or deadlocked?), respawn missing sweeps."
        ),
        "LOG_ERROR": (
            f"INCIDENT TYPE: LOG_ERROR log={detail.get('log')}. Hits: {detail.get('hits', [])[:3]}. "
            "Fix underlying error, do not suppress."
        ),
        "HTTP_DOWN": (
            f"INCIDENT TYPE: HTTP_DOWN port={detail.get('port')} err={detail.get('error')}. "
            "Restart the service."
        ),
        "SITE_DOWN": (
            f"INCIDENT TYPE: SITE_DOWN url={detail.get('url')} err={detail.get('error')}. "
            "Public website unreachable. Likely causes: (1) UFW on gateway 157.90.168.35 blocking 80/443 "
            "from public internet (check `sudo ufw status`), (2) nginx not running on gateway, "
            "(3) DNS misconfiguration. Fix: ensure UFW allows tcp 80,443 from anywhere; restart nginx if needed."
        ),
        "STUCK_SWEEP": (
            f"INCIDENT TYPE: STUCK_SWEEP csv={detail.get('csv')} rows={detail.get('rows')} stuck_sec={detail.get('stuck_sec')}. "
            "Sweep screen is alive but CSV has not grown. Diagnose: (1) backtest_v8_engine.py hung on NPZ load, "
            "(2) ez_manage/tradier_manage import failure, (3) subprocess.run deadlock in sweep pool, "
            "(4) for crypto mode, known V8 crypto bug (W/M TFs, field type mismatches). "
            "Kill the hung screen + respawn with --workers 1 to isolate the hang."
        ),
    }
    hint = hints.get(kind, "")
    return (
        f"HANDS_OFF sentinel auto-fix. {hint} "
        f"Incident JSON: {incident_path}. Pre-fix backup of live code: {snap}. "
        f"MANDATORY: cp <file> backups/before_sentinel_fix_$(date +%Y%m%d%H%M).py BEFORE any edit. "
        f"NEVER revert live code (CLAUDE.md DEATH PENALTY). NEVER prune sweep knobs. "
        f"After fix: rm {ACK_FILE} so sentinel resumes."
    )


def write_incident(kind, detail):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fp = hashlib.md5(f"{kind}|{detail.get('csv','')}|{detail.get('log','')}|{detail.get('port','')}|{detail.get('machine','')}|{detail.get('fp_hash','')}".encode()).hexdigest()[:10]
    path = INCIDENT_DIR / f"incident_{ts}_{kind}_{fp}.json"
    path.write_text(json.dumps({"ts": ts, "host": HOST, "kind": kind, "detail": detail}, indent=2, default=str))
    return path, fp


# ─────────── DETECTORS ───────────

def detect_stuck_sweeps(state):
    """A sweep screen exists but has produced no new CSV rows for STUCK_SWEEP_AGE_SEC.
    On macbook we cover local sweeps; on remote we cover CSVs written to sandbox dirs
    (already in SWEEP_CSV_GLOBS). We also detect CSVs with 0 rows and a stale mtime."""
    incidents = []
    now = time.time()
    for g in SWEEP_CSV_GLOBS:
        for csv_path in glob.glob(g):
            try:
                mtime = os.path.getmtime(csv_path)
                if now - mtime > FRESH_SEC:
                    continue
                with open(csv_path) as f:
                    rows = list(csv.DictReader(f))
            except Exception:
                continue
            n = len(rows)
            key = f"stuck::{csv_path}"
            prev = state.get(key, {})
            if prev.get("rows") == n:
                age = now - prev.get("since", mtime)
                if age > STUCK_SWEEP_AGE_SEC:
                    incidents.append(("STUCK_SWEEP", {
                        "csv": csv_path, "rows": n,
                        "stuck_sec": int(age), "threshold": STUCK_SWEEP_AGE_SEC,
                    }))
            else:
                state[key] = {"rows": n, "since": now}
    # Also detect sweeps that NEVER started producing (0 rows + mtime > threshold)
    for g in SWEEP_CSV_GLOBS:
        for csv_path in glob.glob(g):
            try:
                mtime = os.path.getmtime(csv_path)
                size = os.path.getsize(csv_path)
                if now - mtime < STUCK_SWEEP_AGE_SEC:
                    continue
                if size == 0:
                    continue
                with open(csv_path) as f:
                    rows = list(csv.DictReader(f))
                if len(rows) == 0:
                    incidents.append(("STUCK_SWEEP", {
                        "csv": csv_path, "rows": 0,
                        "stuck_sec": int(now - mtime), "threshold": STUCK_SWEEP_AGE_SEC,
                        "note": "header-only CSV — sweep started but produced zero configs",
                    }))
            except Exception:
                continue
    return incidents


def detect_sweep_csvs(state):
    incidents = []
    now = time.time()
    for g in SWEEP_CSV_GLOBS:
        for csv_path in glob.glob(g):
            try:
                if now - os.path.getmtime(csv_path) > FRESH_SEC:
                    continue
                with open(csv_path) as f:
                    rows = list(csv.DictReader(f))
            except Exception:
                continue
            if len(rows) < 2:
                continue
            sharpe_col = next((c for c in rows[0].keys() if c.lower() == "sharpe"), None)
            trades_col = next((c for c in rows[0].keys() if c.lower() == "trades"), None)
            # ZERO_TRADES
            if trades_col and len(rows) >= ZERO_TRADES_MIN_CONFIGS:
                zeros = sum(1 for r in rows if str(r.get(trades_col, "0")).strip() in ("0", "0.0", ""))
                if zeros / len(rows) >= ZERO_TRADES_RATIO:
                    incidents.append(("ZERO_TRADES", {
                        "csv": csv_path, "rows": len(rows), "zero_rows": zeros,
                        "zero_pct": round(zeros / len(rows) * 100, 1),
                    }))
            # DEAD_PARAMS
            if sharpe_col and len(rows) >= DEAD_MIN_ROWS:
                try:
                    sharpes = [float(r[sharpe_col]) for r in rows if r.get(sharpe_col) not in (None, "", "nan")]
                except ValueError:
                    sharpes = []
                if len(sharpes) >= DEAD_MIN_ROWS:
                    sd = pstdev(sharpes)
                    if sd < DEAD_STDEV:
                        incidents.append(("DEAD_PARAMS", {
                            "csv": csv_path, "rows": len(rows),
                            "sharpe_stdev": sd, "sharpe_range": [min(sharpes), max(sharpes)],
                        }))
            # DUPE_RESULTS
            fp_cols = [c for c in rows[0].keys() if c.lower() in ("sharpe", "pnl", "trades", "wins", "losses")]
            if fp_cols and len(rows) >= 2:
                buckets = defaultdict(list)
                for r in rows:
                    fp = tuple(r.get(c, "") for c in fp_cols)
                    # Skip rows with too few trades to differentiate switches
                    # (1-trade configs produce identical fingerprints regardless of switch state)
                    try:
                        _t_cnt = int(float(str(r.get(trades_col, "0")).strip() or "0"))
                    except ValueError:
                        _t_cnt = 0
                    if _t_cnt < DUPE_MIN_TRADES:
                        continue
                    buckets[fp].append(r.get("name") or r.get("config") or "")
                for fp, names in buckets.items():
                    if len(names) < 2:
                        continue
                    fp_hash = hashlib.md5(str(fp).encode()).hexdigest()[:12]
                    incidents.append(("DUPE_RESULTS", {
                        "csv": csv_path, "fingerprint": list(fp),
                        "config_names": names[:10], "group_size": len(names),
                        "fp_hash": fp_hash,
                    }))
    return incidents


def detect_logs(state):
    incidents = []
    now = time.time()
    for g in LOG_GLOBS:
        for log_path in glob.glob(g):
            try:
                st = os.stat(log_path)
            except OSError:
                continue
            if now - st.st_mtime > FRESH_SEC:
                continue
            key = f"logpos::{log_path}"
            last_pos = state.get(key, st.st_size)
            if st.st_size < last_pos:
                last_pos = 0
            if st.st_size == last_pos:
                continue
            try:
                with open(log_path, "rb") as f:
                    f.seek(last_pos)
                    chunk = f.read(200_000).decode("utf-8", errors="replace")
            except Exception:
                continue
            state[key] = st.st_size
            hits = [ln[:300] for ln in chunk.splitlines()
                    if any(p in ln for p in LOG_PATTERNS) and not any(q in ln for q in LOG_IGNORE)]
            if hits:
                incidents.append(("LOG_ERROR", {"log": log_path, "hits": hits[:10], "count": len(hits)}))
    return incidents


def detect_remote_live_logs(state):
    """2026-06-03: scan S1's LIVE-TRADING logs (ez_manage_*/tradier_manage_*) for the SAME error
    patterns as local detect_logs, so once trading runs on S1 a Traceback/ban/-1003/duplicate-order/
    FATAL there raises a Mac desktop alert (LOG_ERROR) exactly like a local one. Read-only ssh; deduped
    per cycle via a content hash so the same matches aren't re-alerted every loop."""
    incidents = []
    pat = "|".join(x.replace("(", "\\(") for x in LOG_PATTERNS)
    rcmd = ("find /home/niels/logs -maxdepth 1 \\( -name 'ez_manage_*.log' -o -name 'tradier_manage_*.log' \\) -mmin -15 2>/dev/null "
            "| head -20 | xargs -r tail -n 400 2>/dev/null "
            "| grep -aE '" + pat + "' | grep -avE 'test |expected error|INFO' | tail -40")
    for host, target in REMOTE_HOSTS:
        if host != "s1":
            continue
        try:
            rc, out = ssh(target, rcmd, timeout=12)
        except Exception:
            continue
        if rc != 0 or not out or not out.strip():
            continue
        lines = [ln[:300] for ln in out.splitlines() if ln.strip()]
        if not lines:
            continue
        h = hashlib.md5("\n".join(lines[-20:]).encode()).hexdigest()[:12]
        if state.get("s1_livelog_hash") == h:
            continue
        state["s1_livelog_hash"] = h
        incidents.append(("LOG_ERROR", {"log": "s1:ez_manage/tradier_manage(LIVE)", "hits": lines[:10], "count": len(lines)}))
    return incidents


def detect_http(state):
    import urllib.request, urllib.error, ssl
    incidents = []
    ctx = ssl.create_default_context()
    for port in HTTP_PORTS:
        key = f"http::{port}"
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            state[key] = "ok"
        except urllib.error.HTTPError:
            state[key] = "ok"
        except Exception as e:
            prev = state.get(key, "unknown")
            state[key] = "down"
            if prev == "ok":
                incidents.append(("HTTP_DOWN", {"port": port, "error": str(e)[:200]}))
    # External public websites — critical, must be reachable from anywhere
    if IS_MAC:
        for url in EXTERNAL_SITES:
            key = f"site::{url}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "sentinel/1.0"})
                resp = urllib.request.urlopen(req, timeout=5, context=ctx)
                state[key] = f"ok_{resp.status}"
            except urllib.error.HTTPError as e:
                # 4xx/5xx means DNS + TCP + TLS worked — site is up, just a bad path/error
                # Flag only 5xx as incident (client errors may be intentional)
                if e.code >= 500:
                    prev = state.get(key, "unknown")
                    state[key] = f"http_{e.code}"
                    if not str(prev).startswith("http_5"):
                        incidents.append(("SITE_DOWN", {"url": url, "error": f"HTTP {e.code}"}))
                else:
                    state[key] = f"ok_{e.code}"
            except Exception as e:
                prev = state.get(key, "unknown")
                state[key] = "down"
                if str(prev).startswith("ok"):
                    incidents.append(("SITE_DOWN", {"url": url, "error": str(e)[:200]}))
    return incidents


def detect_cpu(state):
    incidents = []
    for name, host in MACHINES_FOR_CPU:
        # 1-line idle % via top; if idle > (100-CPU_MIN) then underutilized
        # Use /proc/stat diff for remote linux, or top -l1 for macOS local
        if host is None:
            if sys.platform == "darwin":
                rc, out = run(["top", "-l", "1", "-n", "0"], timeout=4)
                idle = None
                for ln in out.splitlines():
                    if "CPU usage" in ln and "idle" in ln:
                        try:
                            idle = float(ln.split("idle")[0].rsplit(",", 1)[-1].strip().rstrip("%"))
                        except Exception:
                            pass
                        break
            else:
                rc, out = run(["sh", "-c", "grep '^cpu ' /proc/stat"], timeout=3)
                idle = None
        else:
            rc, out = ssh(host, "awk '/^cpu /{u=$2+$4; t=$2+$4+$5; print (t-u)/t*100}' /proc/stat", timeout=5)
            try:
                idle = float(out.strip())
            except Exception:
                idle = None
        if idle is None:
            continue
        util = 100.0 - idle
        state[f"cpu::{name}"] = util
        prev_key = f"cpu_prev::{name}"
        prev = state.get(prev_key, -1)
        state[prev_key] = util
        # Only fire on sustained low: both this and previous cycle under threshold
        if util < CPU_MIN and prev != -1 and prev < CPU_MIN:
            incidents.append(("LOW_CPU", {"machine": name, "cpu_util": round(util, 1), "threshold": CPU_MIN}))
    return incidents


def pull_remote_incidents(state):
    if not IS_MAC:
        return
    for tag, host in REMOTE_HOSTS:
        local = DATA / f"incidents_{tag}"
        local.mkdir(parents=True, exist_ok=True)
        rc, _ = run(
            ["rsync", "-az", "--timeout=5",
             "-e", "ssh -o ConnectTimeout=5 -o BatchMode=yes -o ControlMaster=no -o ControlPath=none",
             f"{host}:/home/niels/binance/data/sentinel/incidents/", str(local) + "/"],
            timeout=10,
        )
        if rc != 0:
            continue
        for pending in local.glob("*.pending_macbook"):
            marker = INCIDENT_DIR / f"{tag}_{pending.stem}.seen"
            if marker.exists():
                continue
            marker.write_text(tag)
            try:
                data = json.loads(pending.read_text())
            except Exception:
                continue
            prompt = data.get("prompt", "")
            title = f"{tag}_{pending.stem}"
            spawn_iterm_agent(prompt, title)


# ─────────── MAIN LOOP ───────────

def scan_once(state, cycle):
    if ACK_FILE.exists():
        log(f"c{cycle} ACK present, skip")
        return 0
    all_incidents = []
    t0 = time.time()
    t_csv = t_log = t_http = t_cpu = t_rsync = 0.0
    try:
        a = time.time(); all_incidents.extend(detect_sweep_csvs(state)); t_csv = time.time() - a
        all_incidents.extend(detect_stuck_sweeps(state))
        a = time.time(); all_incidents.extend(detect_logs(state)); t_log = time.time() - a
        all_incidents.extend(detect_remote_live_logs(state))  # 2026-06-03: S1 live-trading log errors → Mac desktop alert
        a = time.time(); all_incidents.extend(detect_http(state)); t_http = time.time() - a
        a = time.time(); all_incidents.extend(detect_cpu(state)); t_cpu = time.time() - a
        a = time.time(); pull_remote_incidents(state); t_rsync = time.time() - a
    except Exception as e:
        log(f"c{cycle} detector error: {e}")

    # Dedup per (kind + source + fp)
    seen = set()
    unique = []
    for kind, detail in all_incidents:
        key_src = detail.get("csv") or detail.get("log") or str(detail.get("port", "")) or detail.get("machine", "")
        key = (kind, key_src, detail.get("fp_hash", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append((kind, detail))

    # Further suppress across polls using state
    seen_history_key = "incident_keys_seen"
    hist = set(state.get(seen_history_key, []))
    fresh = []
    for kind, detail in unique:
        key_src = detail.get("csv") or detail.get("log") or str(detail.get("port", "")) or detail.get("machine", "")
        hkey = f"{kind}|{key_src}|{detail.get('fp_hash','')}"
        if hkey in hist:
            continue
        hist.add(hkey)
        fresh.append((kind, detail))
    state[seen_history_key] = list(hist)[-500:]  # cap

    log(f"c{cycle} scan t={time.time()-t0:.1f}s (csv={t_csv:.1f} log={t_log:.1f} http={t_http:.1f} cpu={t_cpu:.1f} rsync={t_rsync:.1f}) total={len(all_incidents)} uniq={len(unique)} fresh={len(fresh)}")
    if not fresh:
        return 0
    snap = snapshot_critical(f"c{cycle}")
    for kind, detail in fresh:
        inc_path, fp = write_incident(kind, detail)
        prompt = build_prompt(inc_path, kind, detail, snap)
        title = f"{inc_path.stem}"
        if IS_MAC:
            spawn_iterm_agent(prompt, title)
        else:
            pending = INCIDENT_DIR / f"{inc_path.stem}.pending_macbook"
            pending.write_text(json.dumps({"incident": str(inc_path), "prompt": prompt, "snap": str(snap), "host": HOST}))
        log(f"INCIDENT {kind} fp={fp} -> {inc_path.name}")
    return len(fresh)


def main():
    if "--once" in sys.argv:
        state = load_state()
        scan_once(state, 0)
        save_state(state)
        return
    acquire_lock()
    try:
        state = load_state()
        log(f"sentinel up host={HOST} base={BASE} mac={IS_MAC} auto_fix={AUTO_FIX} poll={POLL_SEC}s cpu_min={CPU_MIN}")
        cycle = 0
        while True:
            cycle += 1
            try:
                scan_once(state, cycle)
                save_state(state)
            except Exception as e:
                log(f"c{cycle} fatal: {e}")
            time.sleep(POLL_SEC)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
