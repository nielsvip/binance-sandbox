#!/usr/bin/env python3
"""s1_klines_self_produce — S1 makes its own closed klines the moment the Mac/gateway copies are not already on the box.

USER 2026-10-27: "klines are of vital importance that is why they can never arrive even a second late make sure s1 starts
producing its own klines as soon as it does not get one from [the Mac] or gateway backup".

WHY. S1's big store (klines_cache_backtest -> klines_cache) only advanced through klines_append (cron */5, 400-500 s per
full pass) fed by the Mac pusher and the gateway pull. Measured 2026-10-27: the Mac pusher failed EVERY cycle from 00:00Z
to 16:36Z (its process could not resolve uid 501, then the s1-int tunnel refused), the gateway's 15m files land ~3 min
after the close, so a closed bar reached the big store minutes after it existed on the exchange.

WHAT. A loop that wakes GRACE_S after every 15m boundary and, for every live crypto symbol and every TF that closed at
that boundary (15m always, 1h/4h/D on their own boundaries):
  1. bar already in the big store            -> nothing to do
  2. final bar present in a copy already on S1 (klines_cache_macbook, klines_cache/gateway, klines_canonical) -> append it
  3. otherwise                                 -> fetch it from Binance fapi (weight 1, limit <= 99) and append it
Appends are pure tail appends of bars NEWER than the file's last bar (gaps up to 98 bars are filled in the same call;
anything older or out of order is left to klines_append). The file is rewritten atomically (tmp + os.replace) under the
per-file flock `<file>.lock` that klines_append.append_one also takes, so neither writer can drop the other's bar. Every
fetched bar is also kept in klines_cache_s1self/ (a source klines_append reads), so a bar that could not be appended now
is appended by the next klines_append run. 1m/3m are left to klines_append.

Run: tools/s1_klines_self_produce.py --loop        (cron every minute under flock -n: restarts itself if it dies)
     tools/s1_klines_self_produce.py --once --boundary <epoch>   (one boundary, for testing)
Report: data/reports/s1_klines_self_latest.json, history data/reports/s1_klines_self.jsonl
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import fcntl
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
TARGET = ROOT / "klines_cache_backtest"
LOCAL_SOURCES = (ROOT / "klines_cache_macbook", ROOT / "klines_cache" / "gateway", ROOT / "klines_canonical")
SELF = ROOT / "klines_cache_s1self"
REPORT = ROOT / "data" / "reports" / "s1_klines_self_latest.json"
HISTORY = ROOT / "data" / "reports" / "s1_klines_self.jsonl"
FAPI = "https://fapi.binance.com/fapi/v1/klines"
TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "D": 86400}
TF_API = {"15m": "15m", "1h": "1h", "4h": "4h", "D": "1d"}
GRACE_S = float(os.environ.get("S1_KLINES_SELF_GRACE_S", "2"))
STALE_DAYS = float(os.environ.get("S1_KLINES_SELF_STALE_DAYS", "2"))
THREADS = int(os.environ.get("S1_KLINES_SELF_THREADS", "12"))
SELF_KEEP = 672
TS_RE = re.compile(rb'"timestamp":\s*"([0-9T:\-\.]+)Z?"')
try:
    from excluded_tokenized_stocks import is_denied
except Exception:
    def is_denied(_symbol):
        return False


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def epoch_of(text):
    text = text.rstrip("Z")
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in text else "%Y-%m-%dT%H:%M:%S"
    return int(dt.datetime.strptime(text, fmt).replace(tzinfo=dt.timezone.utc).timestamp())


def tail_last_epoch(path, nbytes=4096):
    """epoch of the last timestamp in a JSON-list kline file, read from the tail only (files reach 14 MB)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            found = TS_RE.findall(f.read())
        return epoch_of(found[-1].decode()) if found else None
    except (OSError, ValueError):
        return None


def tail_rows(path, nbytes=65536):
    """final rows parsed from the tail of a kline file (copies on S1 are small; the tail holds the newest bars)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            blob = f.read().decode("utf-8", "ignore")
    except OSError:
        return []
    rows = []
    for chunk in blob.split("{")[1:]:
        end = chunk.find("}")
        if end < 0:
            continue
        try:
            row = json.loads("{" + chunk[:end + 1])
        except ValueError:
            continue
        if row.get("provisional") or row.get("_provisional") or not all(k in row for k in ("timestamp", "open", "high", "low", "close", "volume")):
            continue
        rows.append(row)
    return rows


def sane(row):
    try:
        o, h, l, c, v = (float(row[k]) for k in ("open", "high", "low", "close", "volume"))
    except (KeyError, TypeError, ValueError):
        return False
    return all(math.isfinite(x) for x in (o, h, l, c, v)) and min(o, h, l, c) > 0 and v >= 0 and l <= min(o, c) and h >= max(o, c)


def universe():
    """crypto symbols whose big-store 15m file is alive (last bar within STALE_DAYS); dead/delisted files are not fetched."""
    now = time.time()
    out = []
    for p in TARGET.glob("*_15m.json"):
        symbol = p.name[:-len("_15m.json")]
        if "_" in symbol or is_denied(symbol):
            continue
        last = tail_last_epoch(p)
        if last is not None and now - last <= STALE_DAYS * 86400:
            out.append(symbol)
    return sorted(out)


def fetch(session, symbol, tf, first_open, bar_open, now_ms):
    """closed bars [first_open, bar_open] from fapi; [] if the exchange does not have them closed yet."""
    step = TF_SECONDS[tf]
    limit = min(99, (bar_open - first_open) // step + 2)
    r = session.get(FAPI, params={"symbol": symbol, "interval": TF_API[tf], "limit": int(limit)}, timeout=5)
    if r.status_code in (418, 429):
        raise PermissionError(f"HTTP {r.status_code} {r.text[:120]}")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:120]}")
    rows = []
    for k in r.json():
        start, close_ms = int(k[0]) // 1000, int(k[6])
        if start < first_open or start > bar_open or close_ms >= now_ms:
            continue
        row = {"timestamp": iso(start), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
               "_closed": True, "_provisional": False, "_closed_verified_at": time.time(), "_source": "s1_self_rest"}
        if sane(row):
            rows.append(row)
    return rows


def locked(path, timeout=30.0):
    """the per-file lock klines_append.append_one takes (same path rule), so neither writer drops the other's bar."""
    os.makedirs("/tmp/klines_file_locks", exist_ok=True)
    lock = open("/tmp/klines_file_locks/" + str(path.relative_to(TARGET)).replace("/", "__") + ".lock", "a")
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock
        except BlockingIOError:
            if time.time() > deadline:
                lock.close()
                return None
            time.sleep(0.05)


def append_tail(path, rows):
    """atomically append rows newer than the file's last bar; returns the number appended (never rewrites older bars)."""
    lock = locked(path)
    if lock is None:
        return -1
    try:
        last = tail_last_epoch(path)
        fresh = [r for r in rows if last is None or epoch_of(r["timestamp"]) > last]
        if not fresh or last is None:
            return 0
        data = path.read_bytes()
        end = data.rstrip()
        if not end.endswith(b"]") or len(end) < 2:
            return -2
        body = end[:-1].rstrip()
        sep = b"," if not body.endswith(b"[") else b""
        payload = b",".join(json.dumps(r, separators=(",", ":")).encode() for r in sorted(fresh, key=lambda r: r["timestamp"]))
        tmp = path.with_name(f".{path.name}.s1self.tmp")
        tmp.write_bytes(body + sep + payload + b"]")
        if tmp.stat().st_size <= len(data.rstrip()):
            tmp.unlink()
            return -2
        os.replace(tmp, path)
        return len(fresh)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def keep_self(name, rows):
    path = SELF / name
    old = tail_rows(path, nbytes=1 << 22) if path.exists() else []
    merged = {r["timestamp"]: r for r in old + rows}
    out = [merged[k] for k in sorted(merged)][-SELF_KEEP:]
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")))
    os.replace(tmp, path)


def handle(session, symbol, tf, boundary, banned):
    step = TF_SECONDS[tf]
    bar_open = boundary - step
    name = f"{symbol}_{tf}.json"
    target = TARGET / name
    if not target.exists():
        return {"status": "no_target"}
    last = tail_last_epoch(target)
    if last is not None and last >= bar_open:
        return {"status": "present"}
    first_open = bar_open if last is None else last + step
    want = set(range(first_open, bar_open + 1, step))
    for src in LOCAL_SOURCES:
        rows = [r for r in tail_rows(src / name) if sane(r) and epoch_of(r["timestamp"]) in want]
        if rows and max(epoch_of(r["timestamp"]) for r in rows) == bar_open:
            n = append_tail(target, rows)
            if n > 0:
                return {"status": "local", "src": src.name, "n": n, "at": time.time()}
    if banned[0]:
        return {"status": "rest_paused"}
    for attempt in range(6):
        try:
            rows = fetch(session, symbol, tf, first_open, bar_open, int(time.time() * 1000))
        except PermissionError as exc:
            banned[0] = True
            return {"status": "rest_banned", "error": str(exc)}
        except Exception as exc:
            if attempt == 5:
                return {"status": "rest_error", "error": f"{type(exc).__name__}: {exc}"[:200]}
            time.sleep(0.5)
            continue
        if rows and epoch_of(rows[-1]["timestamp"]) == bar_open:
            try:
                keep_self(name, rows)
            except Exception:
                pass
            n = append_tail(target, rows)
            return {"status": "rest" if n > 0 else "append_failed", "n": n, "at": time.time()}
        time.sleep(0.5)
    return {"status": "rest_not_closed"}


def run_boundary(boundary, symbols):
    tfs = [tf for tf, step in TF_SECONDS.items() if boundary % step == 0]
    jobs = [(s, tf) for s in symbols for tf in tfs]
    banned = [False]
    sessions = {}

    def work(job):
        import threading
        key = threading.get_ident()
        if key not in sessions:
            sessions[key] = requests.Session()
        try:
            return job, handle(sessions[key], job[0], job[1], boundary, banned)
        except Exception as exc:
            return job, {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:200]}

    with cf.ThreadPoolExecutor(THREADS) as pool:
        results = list(pool.map(work, jobs))
    counts, lat, problems = {}, [], []
    for (symbol, tf), r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if "at" in r:
            lat.append(r["at"] - boundary)
        if r["status"] not in ("present", "local", "rest", "no_target"):
            problems.append({"symbol": symbol, "tf": tf, **r})
    lat.sort()
    rec = {"generated_utc": iso(time.time()), "boundary_utc": iso(boundary), "tfs": tfs, "symbols": len(symbols), "counts": counts,
           "written": len(lat), "latency_s_p50": round(lat[len(lat) // 2], 2) if lat else None, "latency_s_max": round(lat[-1], 2) if lat else None,
           "problems": problems[:40], "n_problems": len(problems)}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    os.replace(tmp, REPORT)
    with HISTORY.open("a") as f:
        f.write(json.dumps({k: v for k, v in rec.items() if k != "problems"}) + "\n")
    print(f"[s1-self] {rec['boundary_utc']} tfs={tfs} symbols={len(symbols)} {counts} written={len(lat)} p50={rec['latency_s_p50']}s max={rec['latency_s_max']}s problems={len(problems)}", flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--boundary", type=int, default=0)
    a = ap.parse_args()
    SELF.mkdir(exist_ok=True)
    symbols, symbols_at = universe(), time.time()
    print(f"[s1-self] start universe={len(symbols)} grace={GRACE_S}s threads={THREADS}", flush=True)
    if a.once:
        run_boundary(a.boundary or int(time.time() // 900 * 900), symbols)
        return
    while a.loop:
        boundary = int(time.time() // 900 * 900) + 900
        time.sleep(max(0.0, boundary + GRACE_S - time.time()))
        run_boundary(boundary, symbols)
        if time.time() - symbols_at > 3600:
            symbols, symbols_at = universe(), time.time()


if __name__ == "__main__":
    main()
