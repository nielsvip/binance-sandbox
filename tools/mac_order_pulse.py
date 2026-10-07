#!/usr/bin/env python3
"""mac_order_pulse — Mac-side order-activity pulse for the S1 position supervisor.

Live's own contract (ez_manage.OrderExecutionMonitor: 1s tick, 9s timeout) says a
position update MUST confirm within seconds of an order send. The 60s heartbeat cannot
see that, so this daemon tail-scans the 8 live manage logs every 2s for broker-confirmed
order markers and ships a tiny pulse file to S1 over a persistent ControlMaster socket.

Signals per account (MAX of all three — any trading activity tightens S1 to 3s):
  1. LEDGER (universal ground truth, both venues): data/history/{acct}/*.jsonl last-line "ts".
     Every fill path appends here (market/maker/sync); the engine itself counts these as
     "executed fills, NOT proposed decisions" (ez_manage OVERRIDE_GUARD comment).
  2. SEND markers (zero-lag execution entry, attempts included — an account churning
     attempts is HOT even when fills lag):
     crypto:  ~/logs/ez_manage_{acct}.log  ->  [EXEC_TRACE] ... STEP1_LOCK (inside
               execute_now, past the heartbeat block) + MARKET_SENT ... orderId=<digits>
     stocks:  ~/logs/tradier_manage_{tra,trb,trc}.log  ->  [TRADE] ... (any Status:
               SENT/SUBMITTED/BLOCKED_*/TIMEOUT_* — every queue_trade_action outcome logs one)
S1 reads data/mac_order_pulse.json and tightens its position-stall threshold to 3s for
ORDER_WINDOW_S after any activity (QUIET mode otherwise). Read-only everywhere; fail-open
(missing/unreadable source -> that account omitted, never fabricated).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_PATH = "/Users/niels/Documents/binance"
OUT_PATH = f"{BASE_PATH}/data/mac_order_pulse.json"
LOG_DIR = Path("/Users/niels/logs")
FULL_SCAN_CAP = 8_000_000
TICK_GROWTH_CAP = 10_000_000
TICK_S = 2
LIVENESS_S = 15
CM_SOCKET = "/Users/niels/.ssh/cm-pulse-s1"
SSH_KEY = "/Users/niels/.ssh/id_ed25519"
S1_PATHS = ("/home/niels/binance/data/mac_order_pulse.json", "/home/niels/binance-sandbox/data/mac_order_pulse.json")
CRYPTO_LOGS = {a: LOG_DIR / f"ez_manage_{a}.log" for a in ("ang", "inf", "flz", "men", "fin")}
STOCK_LOGS = {a: LOG_DIR / f"tradier_manage_{a}.log" for a in ("tra", "trb", "trc")}
RE_CRYPTO_EXEC = re.compile(r"\[EXEC_TRACE\] \S+: STEP1_LOCK")
RE_CRYPTO_SENT = re.compile(r"MARKET_SENT qty=\S+ orderId=(\d+)")
RE_STOCK_ANY = re.compile(r"\[TRADE\] ")
RE_STOCK_SENT = re.compile(r"\[TRADE\] SENT: .*\| ID: (\S+)")
RE_LEDGER_TS = re.compile(r'"ts":"([^"]+)"')
HISTORY_DIR = Path(f"{BASE_PATH}/data/history")
LEDGER_TAIL_BYTES = 2048
LEDGER_REGLOB_S = 30
PULSE_ACCOUNTS = ("ang", "inf", "flz", "men", "fin", "tra", "trb", "trc")
RE_EZ_TS = re.compile(r"^(\d{2}) (\d{2}:\d{2}:\d{2})")
RE_TR_TS = re.compile(r"^(\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _with_year(mdy_hms, fmt, now):
    try:
        dt = datetime.strptime(f"{now.year} {mdy_hms}", f"%Y {fmt}")
    except ValueError:
        return None
    epoch = dt.timestamp()
    if epoch > now.timestamp() + 86400:
        try:
            epoch = datetime.strptime(f"{now.year - 1} {mdy_hms}", f"%Y {fmt}").timestamp()
        except ValueError:
            return None
    return epoch


def parse_ez_ts(line, now):
    """Day-only ts (`06 23:50:22` = day + time). Resolve month as the latest candidate
    (this month, else last month) not in the future — correct across month boundaries."""
    m = RE_EZ_TS.match(line)
    if not m:
        return None
    day, hms = m.group(1), m.group(2)
    now_ts = now.timestamp()
    cands = []
    for year, month in ((now.year, now.month), (now.year if now.month > 1 else now.year - 1, now.month - 1 if now.month > 1 else 12)):
        try:
            cands.append(datetime.strptime(f"{year} {month:02d} {day} {hms}", "%Y %m %d %H:%M:%S").replace(tzinfo=now.tzinfo).timestamp())
        except ValueError:
            continue
    past = [c for c in cands if c <= now_ts + 60]
    return max(past) if past else None


def parse_tradier_ts(line, now):
    m = RE_TR_TS.match(line)
    if not m:
        return None
    return _with_year(m.group(1), "%m-%d %H:%M:%S", now)


def match_crypto(line):
    if RE_CRYPTO_EXEC.search(line):
        return True
    return RE_CRYPTO_SENT.search(line) is not None


def match_stock(line):
    return RE_STOCK_ANY.search(line) is not None


def scan_bytes(text, match_fn, ts_fn, now):
    best = None
    for line in text.splitlines():
        if not match_fn(line):
            continue
        ts = ts_fn(line, now)
        if ts is not None and (best is None or ts > best):
            best = ts
    return best


def scan_log(path, match_fn, ts_fn, now, cap=FULL_SCAN_CAP):
    """One-shot: epoch of the newest activity line in the last `cap` bytes, else None."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - cap))
            blob = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    return scan_bytes(blob, match_fn, ts_fn, now)


class LogTailer:
    """Incremental tailer: reads only bytes appended since the last poll (rotation-safe)."""

    def __init__(self, path, match_fn, ts_fn):
        self.path, self.match_fn, self.ts_fn = path, match_fn, ts_fn
        self.inode, self.offset, self.carry, self.best = None, 0, b"", None

    def poll(self, now):
        try:
            st = os.stat(self.path)
        except OSError:
            return self.best
        if self.inode is None or st.st_ino != self.inode or st.st_size < self.offset:
            self.inode, self.offset, self.carry = st.st_ino, max(0, st.st_size - FULL_SCAN_CAP), b""
        end = min(st.st_size, self.offset + TICK_GROWTH_CAP)
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read(end - self.offset)
        except OSError:
            return self.best
        self.offset = end
        buf = self.carry + chunk
        parts = buf.split(b"\n")
        self.carry = parts.pop()
        found = scan_bytes(b"\n".join(parts).decode("utf-8", errors="replace"), self.match_fn, self.ts_fn, now)
        if found is not None and (self.best is None or found > self.best):
            self.best = found
        return self.best


def parse_ledger_ts(text):
    """Epoch of the newest "ts" in a jsonl tail blob, else None."""
    best = None
    for m in RE_LEDGER_TS.finditer(text):
        try:
            ts = datetime.fromisoformat(m.group(1)).timestamp()
        except ValueError:
            continue
        if best is None or ts > best:
            best = ts
    return best


class LedgerTailer:
    """Per-account fill-ledger watcher: stats cached *.jsonl list, reads the tail only of
    files that grew since the last poll. Re-globs every LEDGER_REGLOB_S for new symbols."""

    def __init__(self, acct, history_dir=HISTORY_DIR):
        self.acct, self.dir = acct, Path(history_dir) / acct
        self.sizes, self.best, self.last_glob = {}, None, 0.0

    def _tail_ts(self, path):
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                fh.seek(max(0, size - LEDGER_TAIL_BYTES))
                return parse_ledger_ts(fh.read().decode("utf-8", errors="replace"))
        except OSError:
            return None

    def poll(self, now):
        try:
            fresh_glob = now.timestamp() - self.last_glob >= LEDGER_REGLOB_S
            files = sorted(self.dir.glob("*.jsonl")) if (fresh_glob or not self.sizes) else None
            if files is not None:
                self.last_glob = now.timestamp()
                known = {str(p) for p in files}
                for stale in [k for k in self.sizes if k not in known]:
                    del self.sizes[stale]
            else:
                files = [Path(k) for k in self.sizes]
            for path in files:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                key = str(path)
                if self.sizes.get(key) != size:
                    self.sizes[key] = size
                    ts = self._tail_ts(path)
                    if ts is not None and (self.best is None or ts > self.best):
                        self.best = ts
        except OSError:
            pass
        return self.best


def ledger_best_one_shot(acct, history_dir=HISTORY_DIR):
    best = None
    try:
        paths = sorted(Path(history_dir).glob(f"{acct}/*.jsonl"))
    except OSError:
        return None
    for path in paths:
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                fh.seek(max(0, size - LEDGER_TAIL_BYTES))
                ts = parse_ledger_ts(fh.read().decode("utf-8", errors="replace"))
        except OSError:
            continue
        if ts is not None and (best is None or ts > best):
            best = ts
    return best


def collect_pulse(now=None, tailers=None):
    now = now or datetime.now().astimezone()
    epoch_now = now.timestamp()
    per_account = {}
    if tailers is None:
        for acct, path in CRYPTO_LOGS.items():
            cands = [t for t in (scan_log(path, match_crypto, parse_ez_ts, now), ledger_best_one_shot(acct)) if t is not None]
            if cands:
                per_account[acct] = max(cands)
        for acct, path in STOCK_LOGS.items():
            cands = [t for t in (scan_log(path, match_stock, parse_tradier_ts, now), ledger_best_one_shot(acct)) if t is not None]
            if cands:
                per_account[acct] = max(cands)
    else:
        log_tailers, ledger_tailers = tailers
        for acct, tailer in log_tailers.items():
            cands = [t for t in (tailer.poll(now), ledger_tailers[acct].poll(now)) if t is not None]
            if cands:
                per_account[acct] = max(cands)
    last = max(per_account.values()) if per_account else None
    return {"epoch": epoch_now, "last_order_epoch": last, "last_order_age_s": (epoch_now - last) if last is not None else None, "per_account": per_account}


def build_tailers():
    log_tailers = {}
    for acct, path in CRYPTO_LOGS.items():
        log_tailers[acct] = LogTailer(path, match_crypto, parse_ez_ts)
    for acct, path in STOCK_LOGS.items():
        log_tailers[acct] = LogTailer(path, match_stock, parse_tradier_ts)
    ledger_tailers = {acct: LedgerTailer(acct) for acct in PULSE_ACCOUNTS}
    return log_tailers, ledger_tailers


def write_pulse(payload):
    tmp = f"{OUT_PATH}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, OUT_PATH)


_CM_HOST = None


def _cm_alive_for(host):
    r = subprocess.run(["ssh", "-S", CM_SOCKET, "-O", "check", host], capture_output=True, timeout=8)
    return r.returncode == 0


def _cm_ensure():
    global _CM_HOST
    if _CM_HOST and _cm_alive_for(_CM_HOST):
        return _CM_HOST
    for port, host in (("2201", "niels@localhost"), ("22", "niels@157.180.125.52")):
        subprocess.run(["ssh", "-M", "-S", CM_SOCKET, "-fN", "-p", port, "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", host], capture_output=True, timeout=15)
        if _cm_alive_for(host):
            _CM_HOST = host
            return host
    try:
        os.unlink(CM_SOCKET)
    except OSError:
        pass
    _CM_HOST = None
    return None


def push_to_s1():
    host = _cm_ensure()
    if host is None:
        return False
    shell = f"ssh -S {CM_SOCKET} -o BatchMode=yes -o ControlMaster=no"
    ok = True
    for remote in S1_PATHS:
        r = subprocess.run(["rsync", "-az", "--timeout=6", "-e", shell, OUT_PATH, f"{host}:{remote}"], capture_output=True, timeout=12)
        ok = ok and r.returncode == 0
    return ok


def main_loop():
    import fcntl
    lk = open("/tmp/mac_order_pulse.lock", "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("locked (previous pulse daemon active)", flush=True)
        return
    tailers = build_tailers()
    last_hash, last_push = None, 0.0
    while True:
        try:
            payload = collect_pulse(tailers=tailers)
            write_pulse(payload)
            digest = hashlib.md5(json.dumps(payload["per_account"], sort_keys=True).encode()).hexdigest()
            now = time.time()
            if digest != last_hash or now - last_push >= LIVENESS_S:
                push_to_s1()
                last_hash, last_push = digest, now
        except Exception as e:
            print(f"[pulse] loop error (continuing): {e!r}", flush=True)
        time.sleep(TICK_S)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        payload = collect_pulse()
        write_pulse(payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        main_loop()
