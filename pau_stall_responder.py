#!/usr/bin/env python3
"""pau_stall_responder.py — multi-detector reliability watchdog.

Watches several live-system log streams for failure-mode clusters; when any
detector trips its threshold, spawns a one-shot Claude Opus agent with a
detector-specific diagnostic prompt. Each detector has its own sliding window,
threshold, and cooldown so a fire on one (e.g. OOM cycle) does NOT suppress
a fire on another (e.g. PAU stall) — they investigate different bugs.

Run via launchd KeepAlive — see com.niels.pau-stall-responder.plist.

Detectors (current):
  pau_stall      ≥3 PAU/handle_account_update HUNG/RAISED markers in 30 min
                 (the original tripwire — see feedback_pau_crash_on_hang.md)
  oom_cycle      ≥5 worker exits with code 137 (jetsam SIGKILL) in 30 min
                 → memory leak / runaway allocation
  ws_reset       ≥5 'WS frozen (no msg > 60s)' resets for a SINGLE account in
                 30 min → that account's user-stream is genuinely broken
  restore_cascade ≥10 UNIQUE symbols failing RESTORE_BACKUP ABSOLUTE FAILURE
                 in 30 min → symbols.json drifted or backup directory wiped

Each detector independent: own deque, own state file, own cooldown stamp,
own agent prompt. Adding a new detector = append a dict to DETECTORS.
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
# Resolve `claude` CLI absolutely so launchd's restricted PATH doesn't FileNotFound us.
# Order: ~/.local/bin (default install), explicit override via env, then PATH lookup.
CLAUDE_BIN = (
    os.environ.get("CLAUDE_BIN")
    or (str(Path.home() / ".local" / "bin" / "claude") if (Path.home() / ".local" / "bin" / "claude").exists() else None)
    or "claude"
)
SERVICE_LOG = LOG_DIR / "ez_positions_service.log"
REALTIME_GLOB = "ez_positions_realtime*.log"
WATCHDOG_GLOB = "ez_manage_*_watchdog.log"

LOCK_FILE = LOG_DIR / ".pau_stall_responder.lock"
OFFSETS_FILE = LOG_DIR / ".pau_stall_responder_offsets.txt"
POLL_SEC = 15

# ── Marker regexes ──────────────────────────────────────────────────────────
RE_PAU = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\]\s+CRITICAL\s+"
    r"\[(?:fetch_positions|FETCH_LOOP|WS)\]\[([a-z]{3})\]\s+🚨🚨🚨\s+"
    r"(process_account_update|handle_account_update)\s+(HUNG > [\d.]+s|RAISED)"
)
RE_OOM = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+Script exited with code:\s*137")
RE_WS_RESET = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\]\s+WARNING\s+\[([a-z]{3})\]\s+WS frozen"
)
RE_RESTORE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\]\s+CRITICAL\s+\[RESTORE_BACKUP\]\s+ABSOLUTE FAILURE:\s+"
    r"([a-z]{3}):([A-Z0-9]+)_(LONG|SHORT)"
)


def parse_ts(ts_str: str) -> float:
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def log(msg: str):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


# ── Detector definitions ────────────────────────────────────────────────────
def _pau_extract(line: str):
    m = RE_PAU.search(line)
    if not m:
        return None
    return {"ts": parse_ts(m.group(1)), "key": (m.group(2), m.group(3), m.group(4)),
            "summary": f"{m.group(2)} | {m.group(3)} | {m.group(4)}"}


def _oom_extract(line: str, src: str):
    m = RE_OOM.search(line)
    if not m:
        return None
    acct = "?"
    n = src.replace("ez_manage_", "").replace("_watchdog.log", "")
    if n in ("ang", "inf", "flz", "men", "fin"):
        acct = n
    return {"ts": parse_ts(m.group(1)), "key": acct, "summary": f"{acct} OOM-killed (exit 137)"}


def _ws_reset_extract(line: str):
    m = RE_WS_RESET.search(line)
    if not m:
        return None
    return {"ts": parse_ts(m.group(1)), "key": m.group(2), "summary": f"{m.group(2)} WS frozen >60s, resetting"}


def _restore_extract(line: str):
    m = RE_RESTORE.search(line)
    if not m:
        return None
    return {"ts": parse_ts(m.group(1)), "key": (m.group(2), m.group(3), m.group(4)),
            "summary": f"{m.group(2)}:{m.group(3)}_{m.group(4)} not in any backup"}


PAU_PROMPT = """The crash-on-hang protection in ez_positions_service.py / ez_positions_realtime.py
just fired {n} times in the last {window_min} minutes. process_account_update /
handle_account_update are the CORE of position state — repeated stalls mean a real bug.

WHERE TO LOOK FIRST (most likely culprits, in order):
1. ez_positions_service.py:_process_account_update_impl — synchronous disk I/O in the
   asyncio event loop (`restore_position_from_backups` → glob.glob + open + json.load
   × ~50 backup files × every orphan symbol × 5 accounts). Wrap with asyncio.to_thread.
2. ez_positions_service.py:_sync_memory_with_master_symbols (called every 180s by
   cleanup_positions) — same problem.
3. quick_price() (~line 966) — `await read_client.get(redis_key)` has no timeout.
4. _broadcast_positions_to_redis — `redis_manager.publish(...)` and `.set(...)` unbounded.
5. handle_account_update inner per-position loop hanging on handle_augmentation/handle_reduction.

TRIAGE: tail -200 /Users/niels/logs/ez_positions_service.log | grep -E 'CRASHING|HUNG|RESTORE|REDIS'
        redis-cli -p 6379 ping; redis-cli -p 6380 ping
        Compare timing — always cleanup_positions cycle (every 180s)? always WS arrival?

DO NOT remove the wait_for/os._exit safety net. DO NOT raise the timeout — the work is
wrong, not the timeout."""

OOM_PROMPT = """ez_manage workers were SIGKILLed with exit code 137 ({n} times in {window_min}
minutes) — Mac jetsam OOM-killing them. RSS is exceeding the 1.5GB MAX_RSS_KB ceiling
in run_with_watchdog.sh OR whole-system memory pressure is driving jetsam.

WHERE TO LOOK FIRST:
1. Memory leak in long-running ez_manage state — growing dicts, accumulating asyncio
   tasks, redis connections never closed. Check: `partial_profit_lock_state`,
   `_position_update_timestamps`, hedge state, V3 state machines.
2. Accumulating reentry queue / unbounded task creation (`asyncio.create_task` without
   bookkeeping in monitor_reductions_priority etc).
3. Look for top-RAM workers: `ps aux | awk '/python.*ez_manage/ {{print $2, $4, $11, $13}}'`
   then on the worst offender: `top -pid <PID> -l 1` and compare RSS over time.
4. Check tracemalloc / objgraph if it's a leak shape problem.

TRIAGE: ls -la /Users/niels/logs/ez_manage_*_watchdog.log | head; tail -50 of each.
        sysctl vm.swapusage; vmstat 5 5
        Look for jetsam pressure: `log show --last 30m --predicate 'eventMessage contains "jetsam"' | head`

DO NOT just bump MAX_RSS_KB — find what's leaking. Do NOT add `gc.collect()` as a fix
(that hides leaks instead of fixing them). Concrete leak fix > masking knob."""

WS_PROMPT = """Account {key}'s Binance user-stream WebSocket has reset >5 times in the last
{window_min} minutes ('WS frozen (no msg > 60s)'). The user-data WS feeds ACCOUNT_UPDATE
events — when it dies, REST poll is the only path keeping positions fresh, and WS-driven
fast paths (handle_account_update) go dark.

WHERE TO LOOK FIRST:
1. Listen-key rotation: Binance listen keys expire every 60 min and require periodic
   keepalive POSTs. If keepalive fails, WS keeps emitting nothing until reset.
   Check `ez_positions_service.py` for listen_key keepalive task and its log lines.
2. Network: is the account hitting a stale tunnel? Other accounts unaffected?
3. API key for that account: revoked? IP-restricted to a stale IP? Check
   `tail -100 /Users/niels/logs/ez_positions_service.log | grep -E 'listenKey|user-stream|{key}'`
4. Account rate-limit ban (-1003 on the keepalive POST) → WS continues but listen key
   silently invalid.

TRIAGE: grep -E 'listenKey|listen_key' /Users/niels/logs/ez_positions_service.log | tail -30
        Verify keepalive POST is firing every <60min and returning 200.
        Check whether OTHER accounts are healthy — if all 5 are dead, it's network/keys; if
        only {key} is dead, it's account-specific.

DO NOT just lengthen the freeze threshold. The reset is the right behavior; we need to
know why the underlying stream goes silent."""

RESTORE_PROMPT = """{n} UNIQUE symbols failed RESTORE_BACKUP ABSOLUTE FAILURE in the last
{window_min} minutes. This means the API returned positions for symbols whose data is not
on disk, not in any account's backups, and not in any other account's main file. This
usually indicates one of:

WHERE TO LOOK FIRST:
1. symbols.json was edited and now contains symbols whose position files don't exist.
   Diff `symbols.json` against the LONG/SHORT positions files in each account dir.
2. _sync_memory_with_master_symbols is creating expectations for symbols whose actual
   positions were never opened (so no disk file ever existed).
3. backups/ directory was wiped or rotated more aggressively than expected.
4. API_ORPHAN_ADOPTED path at ez_positions_service.py:~6336 should be creating in-memory
   placeholder positions for these — verify it's actually firing, that adoption persists
   across cycles, and the new positions get saved to disk so next restart finds them.

TRIAGE: head -1 /Users/niels/Documents/binance/symbols.json | tr ',' '\\n' | wc -l
        ls /Users/niels/Documents/binance/inf/backups/ | wc -l
        grep -c 'API_ORPHAN_ADOPTED\\|ORPHAN_EXCHANGE_POSITION' /Users/niels/logs/ez_positions_service.log

The cascade itself blocks the asyncio event loop (synchronous disk reads × N orphans)
and is probably also indirectly causing PAU stalls. Fix the upstream cause; don't add
another retry layer."""

DETECTORS = [
    {
        "name": "pau_stall",
        "log_paths": [SERVICE_LOG, "@realtime"],
        "extract": _pau_extract,
        "window_sec": 30 * 60,
        "threshold": 3,
        "cooldown_sec": 60 * 60,
        "unique_keys": False,
        "prompt": PAU_PROMPT,
    },
    {
        "name": "oom_cycle",
        "log_paths": ["@watchdogs"],
        "extract": _oom_extract,
        "window_sec": 30 * 60,
        "threshold": 5,
        "cooldown_sec": 60 * 60,
        "unique_keys": False,
        "prompt": OOM_PROMPT,
    },
    {
        "name": "ws_reset",
        "log_paths": [SERVICE_LOG],
        "extract": _ws_reset_extract,
        "window_sec": 30 * 60,
        # 15 per-account in 30 min ≈ 1 reset every 2 min sustained = genuine WS death.
        # Lower threshold (e.g. 5) trips on baseline noise — current accounts run ~20-30/30min steady.
        "threshold": 15,
        "cooldown_sec": 60 * 60,
        "unique_keys": False,
        "per_key_threshold": True,
        "prompt": WS_PROMPT,
    },
    {
        "name": "restore_cascade",
        "log_paths": [SERVICE_LOG],
        "extract": _restore_extract,
        "window_sec": 30 * 60,
        # 30 unique symbols (across all accounts) — the chronic orphan set is ~10-20.
        # A genuine cascade (symbols.json drift / backup wipe) produces 50+ unique fast.
        "threshold": 30,
        "cooldown_sec": 60 * 60,
        "unique_keys": True,
        "prompt": RESTORE_PROMPT,
    },
]


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


def cooldown_file(name: str) -> Path:
    return LOG_DIR / f".pau_stall_responder_cooldown_{name}.ts"


def cooldown_active(name: str, secs: int) -> bool:
    p = cooldown_file(name)
    if not p.exists():
        return False
    try:
        return (time.time() - float(p.read_text().strip())) < secs
    except Exception:
        return False


def stamp_cooldown(name: str):
    cooldown_file(name).write_text(str(time.time()))


def load_offsets() -> dict:
    if not OFFSETS_FILE.exists():
        return {}
    try:
        d = {}
        for line in OFFSETS_FILE.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k] = int(v)
        return d
    except Exception:
        return {}


def save_offsets(offsets: dict):
    OFFSETS_FILE.write_text("\n".join(f"{k}={v}" for k, v in offsets.items()))


def resolve_paths(spec_list: list) -> list:
    paths = []
    for spec in spec_list:
        if isinstance(spec, Path):
            paths.append(spec)
        elif spec == "@realtime":
            paths.extend(sorted(LOG_DIR.glob(REALTIME_GLOB)))
        elif spec == "@watchdogs":
            paths.extend(sorted(LOG_DIR.glob(WATCHDOG_GLOB)))
    return paths


def scan(path: Path, offset: int, extract_fn, events: deque, src_kw: bool):
    if not path.exists():
        return offset
    size = path.stat().st_size
    if size < offset:
        offset = 0
    with path.open("r", errors="replace") as f:
        f.seek(offset)
        for line in f:
            ev = extract_fn(line, path.name) if src_kw else extract_fn(line)
            if ev is not None:
                events.append(ev)
        offset = f.tell()
    return offset


def prune(events: deque, window_sec: int):
    # Filter ALL events, not just front-pop — deque may not be globally sorted when
    # we accumulate from multiple log files (each file is sorted, but interleaving isn't).
    cutoff = time.time() - window_sec
    keep = [e for e in events if e["ts"] >= cutoff]
    events.clear()
    events.extend(keep)


def threshold_tripped(detector: dict, events: deque):
    if detector.get("per_key_threshold"):
        from collections import Counter
        c = Counter(e["key"] for e in events)
        for k, n in c.items():
            if n >= detector["threshold"]:
                return [e for e in events if e["key"] == k], k
        return None, None
    if detector.get("unique_keys"):
        keys = {e["key"] for e in events}
        if len(keys) >= detector["threshold"]:
            return list(events), None
        return None, None
    if len(events) >= detector["threshold"]:
        return list(events), None
    return None, None


def spawn_agent(detector: dict, events: list, hot_key=None):
    name = detector["name"]
    window_min = detector["window_sec"] // 60
    summary_lines = [
        f"  - {datetime.fromtimestamp(e['ts'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC | {e['summary']}"
        for e in events
    ]
    summary = "\n".join(summary_lines)
    log_file = LOG_DIR / f"reliability_agent_{name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
    body = detector["prompt"].format(
        n=len(events),
        window_min=window_min,
        key=hot_key or "?",
    )
    full_prompt = f"""You are a trading-system reliability agent. The '{name}' detector
just tripped: {len(events)} events in the last {window_min} minutes.

RECENT EVENTS:
{summary}

YOUR JOB: diagnose the ROOT CAUSE and fix it. The detector is a tripwire, not a solution.

{body}

RULES (NON-NEGOTIABLE — see CLAUDE.md):
- BACKUP BEFORE EDITING: cp <file> backups/before_<desc>_$(date -u +%Y%m%d%H%M).py
- Compile-check after edit: python3 -c "import py_compile; py_compile.compile('<file>', doraise=True)"
- If you edit any of the 6 critical files (ez_manage / ez_positions_quick /
  ez_positions_service / tradier_manage / config / config_tradier), rsync to S1+S2 and
  verify md5 parity:
    rsync -az --existing --update <file> s1-int:/home/niels/binance-sandbox/
    rsync -az --existing --update <file> s2-int:/home/niels/binance-sandbox/
- Do NOT git reset/restore/checkout. Only move forward.
- Do NOT close positions at a loss (STRICT_NO_LOSS active).
- After patching, restart the affected workers gracefully so the fix takes effect:
    ez_manage:   pkill -TERM -f "python.*-u ez_manage.py --account"  (watchdog respawns)
    service:     same — service is bootstrapped inside each ez_manage process

WORKING DIRECTORY: {BASE_PATH}
PYTHON: /opt/anaconda3/envs/binance_env/bin/python
LOG FOR YOUR OUTPUT: {log_file}

Investigate, fix, sync, restart, and write a 1-paragraph summary at the end of {log_file}
with: root cause, what you changed, what to watch for to confirm the fix held."""
    log(f"[{name}] spawning agent (events={len(events)} hot_key={hot_key} log={log_file})")
    try:
        with open(log_file, "w") as out:
            out.write(f"[responder] detector={name} spawned at {datetime.now(timezone.utc).isoformat()}\n")
            out.write(f"[responder] events:\n{summary}\n\n")
            out.flush()
            proc = subprocess.Popen(
                [CLAUDE_BIN, "-p", full_prompt, "--model", "opus",
                 "--output-format", "text",
                 "--dangerously-skip-permissions",
                 "--no-session-persistence"],
                cwd=str(BASE_PATH),
                stdout=out, stderr=subprocess.STDOUT,
            )
        log(f"[{name}] agent pid={proc.pid}")
        stamp_cooldown(name)
        return True
    except FileNotFoundError:
        log("'claude' CLI not found — cannot spawn agent")
        return False
    except Exception as e:
        log(f"[{name}] spawn failed: {e}")
        return False


def main():
    if not acquire_lock():
        sys.exit(0)
    log(f"started — {len(DETECTORS)} detectors: {[d['name'] for d in DETECTORS]} (poll={POLL_SEC}s)")
    state = {
        d["name"]: {
            "events": deque(),
            "extract": d["extract"],
            "src_kw": d["extract"].__code__.co_argcount == 2,
        }
        for d in DETECTORS
    }
    offsets = load_offsets()
    try:
        first_pass = True
        while True:
            for det in DETECTORS:
                paths = resolve_paths(det["log_paths"])
                for p in paths:
                    if not p.exists():
                        continue
                    okey = f"{det['name']}::{p}"
                    if first_pass and okey not in offsets:
                        offsets[okey] = p.stat().st_size
                        continue
                    offsets[okey] = scan(p, offsets.get(okey, 0), det["extract"],
                                         state[det["name"]]["events"],
                                         state[det["name"]]["src_kw"])
                prune(state[det["name"]]["events"], det["window_sec"])
                events_now, hot_key = threshold_tripped(det, state[det["name"]]["events"])
                if events_now:
                    if cooldown_active(det["name"], det["cooldown_sec"]):
                        log(f"[{det['name']}] threshold hit ({len(events_now)} events) but cooldown active — skipping spawn")
                    else:
                        log(f"[{det['name']}] THRESHOLD TRIPPED — spawning")
                        if spawn_agent(det, events_now, hot_key):
                            state[det["name"]]["events"].clear()
            save_offsets(offsets)
            first_pass = False
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
