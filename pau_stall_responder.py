#!/usr/bin/env python3
"""pau_stall_responder.py — watches ez_positions_service.log + ez_positions_realtime.log
for the crash-on-hang markers emitted when process_account_update / handle_account_update
times out (>60s) or raises. When >=3 such events fire in a 30-min sliding window across
ANY accounts/sites, spawns a one-shot Claude Opus agent with a focused diagnostic prompt.

Cooldown: 1 hour between spawns (so a single bad deploy can't burn through API budget).
Lock file: ~/logs/.pau_stall_responder.lock (prevents two responders racing).
Run via launchd KeepAlive — see com.niels.pau-stall-responder.plist.
"""
import os
import re
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

BASE_PATH = Path("/Users/niels/Documents/binance")
LOG_DIR = Path.home() / "logs"
SERVICE_LOG = LOG_DIR / "ez_positions_service.log"
REALTIME_LOG_GLOB = "ez_positions_realtime*.log"
STATE_FILE = LOG_DIR / ".pau_stall_responder_state.txt"
LOCK_FILE = LOG_DIR / ".pau_stall_responder.lock"
COOLDOWN_FILE = LOG_DIR / ".pau_stall_responder_cooldown.ts"

WINDOW_SEC = 30 * 60
THRESHOLD = 3
COOLDOWN_SEC = 60 * 60
POLL_SEC = 15

MARKER_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\]\s+CRITICAL\s+"
    r"\[(?:fetch_positions|FETCH_LOOP|WS)\]\[([a-z]{3})\]\s+🚨🚨🚨\s+"
    r"(process_account_update|handle_account_update)\s+(HUNG > 60s|RAISED)"
)


def log(msg: str):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def acquire_lock() -> bool:
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            os.kill(old_pid, 0)
            log(f"another responder alive (pid={old_pid}); exiting")
            return False
        except (ValueError, OSError, ProcessLookupError):
            pass
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception:
        pass


def cooldown_active() -> bool:
    if not COOLDOWN_FILE.exists():
        return False
    try:
        last = float(COOLDOWN_FILE.read_text().strip())
    except Exception:
        return False
    return (time.time() - last) < COOLDOWN_SEC


def stamp_cooldown():
    COOLDOWN_FILE.write_text(str(time.time()))


def load_offset(path: Path) -> int:
    if not STATE_FILE.exists():
        return path.stat().st_size if path.exists() else 0
    try:
        d = dict(line.split("=", 1) for line in STATE_FILE.read_text().splitlines() if "=" in line)
        return int(d.get(str(path), path.stat().st_size if path.exists() else 0))
    except Exception:
        return path.stat().st_size if path.exists() else 0


def save_offsets(offsets: dict):
    STATE_FILE.write_text("\n".join(f"{p}={off}" for p, off in offsets.items()))


def parse_ts(ts_str: str) -> float:
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def scan_log(path: Path, offset: int, events: deque):
    if not path.exists():
        return offset
    size = path.stat().st_size
    if size < offset:
        offset = 0
    with path.open("r", errors="replace") as f:
        f.seek(offset)
        for line in f:
            m = MARKER_RE.search(line)
            if m:
                ts_str, account, fn, kind = m.group(1), m.group(2), m.group(3), m.group(4)
                try:
                    ts = parse_ts(ts_str)
                    events.append((ts, account, fn, kind, path.name))
                except Exception:
                    pass
        offset = f.tell()
    return offset


def prune_window(events: deque):
    cutoff = time.time() - WINDOW_SEC
    while events and events[0][0] < cutoff:
        events.popleft()


def spawn_agent(events: list):
    summary_lines = [
        f"  - {datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S')} UTC | {acct} | {fn} {kind} | {src}"
        for ts, acct, fn, kind, src in events
    ]
    summary = "\n".join(summary_lines)
    log_file = LOG_DIR / f"pau_stall_agent_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
    prompt = f"""You are a trading-system reliability agent. The crash-on-hang protection in
ez_positions_service.py / ez_positions_realtime.py just fired {len(events)} times in the last
{WINDOW_SEC // 60} minutes. process_account_update / handle_account_update are the CORE of position
state — every handle_* function (handle_unchanged_position / handle_augmentation / handle_reduction)
runs from there and feeds the rest of the trading system. Repeated stalls mean a real bug.

RECENT STALL EVENTS:
{summary}

YOUR JOB: diagnose the ROOT CAUSE inside process_account_update or handle_account_update and fix it
so the worker stops needing to crash. The crash-on-hang is a safety net, not a solution.

WHERE TO LOOK FIRST (most likely culprits, in order):
1. ez_positions_service.py:_process_account_update_impl — runs heavy SYNCHRONOUS disk I/O in
   the asyncio event loop (`restore_position_from_backups` → glob.glob + open + json.load × ~50
   backup files × every orphan symbol × 5 accounts). Wrap with asyncio.to_thread.
2. ez_positions_service.py:_sync_memory_with_master_symbols (called by cleanup_positions every
   180s) — same problem, blocks event loop while it scans symbols.json × 2 sides × disk reads.
3. quick_price() (ez_positions_service.py:~966) — `await read_client.get(redis_key)` has no
   timeout; if Redis stalls, every position update hangs.
4. _broadcast_positions_to_redis — `await redis_manager.publish(...)` and `.set(...)` unbounded.
5. handle_account_update (line ~2408) — its inner for-loop awaits handle_augmentation /
   handle_reduction per position; if any one hangs, the whole batch hangs.

INVESTIGATION STEPS:
- `tail -200 /Users/niels/logs/ez_positions_service.log | grep -E 'CRASHING|HUNG|RESTORE_BACKUP|REDIS'`
- Check Redis health: `redis-cli -p 6379 ping` and `redis-cli -p 6380 ping`
- Check disk I/O latency on /Users/niels/Documents/binance
- Check what account is hitting it most (look at the events table above)
- Compare timing: is it always cleanup_positions cycle (every 180s)? always WS arrival? always REST poll?

WHAT TO FIX:
- Wrap any synchronous disk read inside `_process_account_update_impl` and helpers
  with `await asyncio.to_thread(lambda: ...)` so the event loop stays responsive.
- Add per-call timeouts on `read_client.get()` / `redis_manager.publish()` / `.set()`
  (5s is plenty — beyond that something is genuinely broken).
- If the spam comes from missing positions triggering restore_position_from_backups every
  fetch, fix the orphan adoption so it sticks (the API_ORPHAN_ADOPTED path at line ~6336
  should prevent re-restoration but might be losing the position between cycles).

RULES (NON-NEGOTIABLE — see CLAUDE.md):
- BACKUP BEFORE EDITING: `cp ez_positions_service.py backups/before_<desc>_$(date -u +%Y%m%d%H%M).py`
- After editing, syntax-check: `python3 -c "import py_compile; py_compile.compile('ez_positions_service.py', doraise=True)"`
- After editing, sync to S1+S2: `rsync -az --existing --update ez_positions_service.py ez_positions_realtime.py s1-int:/home/niels/binance-sandbox/`
  and `... s2-int:/home/niels/binance-sandbox/`. Verify md5 parity.
- Do NOT remove the crash-on-hang wrappers (asyncio.wait_for / os._exit). They are the safety net.
- Do NOT raise the 60s timeout. If real work needs >60s the work is wrong, not the timeout.
- After patching, restart the 5 ez_manage workers gracefully so the fix takes effect:
  `pkill -TERM -f "python.*-u ez_manage.py --account"` (watchdog respawns within ~60s).

WORKING DIRECTORY: {BASE_PATH}
PYTHON: /opt/anaconda3/envs/binance_env/bin/python
LOG FOR YOUR OUTPUT: {log_file}

Investigate, fix, sync, restart, and write a 1-paragraph summary at the end of {log_file}
explaining the root cause + what you changed + what to watch for to confirm the fix held.
"""
    log(f"spawning Claude Opus agent (events={len(events)}, log={log_file})")
    try:
        with open(log_file, "w") as out:
            out.write(f"[responder] spawned at {datetime.now(timezone.utc).isoformat()}\n")
            out.write(f"[responder] events:\n{summary}\n\n")
            out.flush()
            proc = subprocess.Popen(
                ["claude", "-p", prompt, "--model", "opus",
                 "--output-format", "text",
                 "--dangerously-skip-permissions",
                 "--no-session-persistence"],
                cwd=str(BASE_PATH),
                stdout=out, stderr=subprocess.STDOUT,
            )
        log(f"agent spawned pid={proc.pid}")
        stamp_cooldown()
        return True
    except FileNotFoundError:
        log("'claude' CLI not found — cannot spawn agent")
        return False
    except Exception as e:
        log(f"spawn failed: {e}")
        return False


def main():
    if not acquire_lock():
        sys.exit(0)
    log(f"started (window={WINDOW_SEC}s threshold={THRESHOLD} cooldown={COOLDOWN_SEC}s poll={POLL_SEC}s)")
    events = deque()
    offsets = {}
    try:
        while True:
            paths = [SERVICE_LOG] + sorted(LOG_DIR.glob(REALTIME_LOG_GLOB))
            for p in paths:
                if not p.exists():
                    continue
                key = str(p)
                off = offsets.get(key, load_offset(p))
                offsets[key] = scan_log(p, off, events)
            save_offsets(offsets)
            prune_window(events)
            if len(events) >= THRESHOLD:
                if cooldown_active():
                    log(f"threshold hit ({len(events)} events) but cooldown active; not spawning")
                else:
                    accts = {e[1] for e in events}
                    sites = {e[2] for e in events}
                    log(f"THRESHOLD: {len(events)} stalls in {WINDOW_SEC // 60}m across accounts={sorted(accts)} sites={sorted(sites)}")
                    if spawn_agent(list(events)):
                        events.clear()
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
