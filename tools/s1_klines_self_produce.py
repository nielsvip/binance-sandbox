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
  1. fetch the exchange's CLOSED bars (Binance fapi, weight 1, limit <= 99): the just-closed bar, the two before it, and
     any gap back to the file's last bar (up to 98 bars)
  2. append the ones the big store lacks; CORRECT the ones it holds with different OHLCV (a copy on S1 can carry a
     forming bar written as final — measured on the gateway's 1h files 2026-10-27)
  3. only if REST fails: append missing bars from a final copy already on S1 (klines_cache_macbook, klines_cache/gateway,
     klines_canonical); never a correction from a copy
Anything older or out of order is left to klines_append. The file is rewritten atomically (tmp + os.replace) under the
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


def differs(old, new):
    return any(abs(float(old.get(k, "nan")) - float(new[k])) > 1e-9 * max(1.0, abs(float(new[k]))) for k in ("open", "high", "low", "close", "volume"))


def write_tail(path, rows, window=1 << 16):
    """under the shared per-file lock, atomically: append exchange rows newer than the file's last bar, and correct rows
    already in the file's tail whose OHLCV differs from the exchange's CLOSED values (row count unchanged). Measured
    2026-10-27: the gateway writes the forming 1h bar with no provisional flag and klines_append froze it as final
    (BTCUSDC 1h 16:00 volume 365.185 vs 985.307 closed), so a timestamp being present is not proof the bar is right.
    Returns (appended, corrected), or (-1, 0) when the lock was not obtained, (-2, 0) when the file is not a JSON list."""
    lock = locked(path)
    if lock is None:
        return -1, 0
    try:
        data = path.read_bytes().rstrip()
        if not data.endswith(b"]") or len(data) < 2:
            return -2, 0
        last = tail_last_epoch(path)
        cut = max(0, len(data) - window)
        head, tail = data[:cut], data[cut:]
        corrected = 0
        for row in rows:
            if last is None or epoch_of(row["timestamp"]) > last:
                continue
            m = re.search(rb'\{[^{}]*"timestamp":\s*"' + re.escape(row["timestamp"].encode()) + rb'"[^{}]*\}', tail)
            if not m:
                continue
            try:
                old = json.loads(m.group(0))
            except ValueError:
                continue
            if not differs(old, row):
                continue
            new = dict(old, **{k: row[k] for k in ("open", "high", "low", "close", "volume")}, _source="s1_self_rest", _corrected_at=time.time())
            tail = tail[:m.start()] + json.dumps(new, separators=(",", ":")).encode() + tail[m.end():]
            corrected += 1
        fresh = sorted((r for r in rows if last is not None and epoch_of(r["timestamp"]) > last), key=lambda r: r["timestamp"])
        if not fresh and not corrected:
            return 0, 0
        body = (head + tail)[:-1].rstrip()
        sep = b"," if fresh and not body.endswith(b"[") else b""
        payload = b",".join(json.dumps(r, separators=(",", ":")).encode() for r in fresh)
        tmp = path.with_name(f".{path.name}.s1self.tmp")
        tmp.write_bytes(body + sep + payload + b"]")
        if tmp.stat().st_size < len(data) - 64 * max(1, corrected):
            tmp.unlink()
            return -2, 0
        os.replace(tmp, path)
        return len(fresh), corrected
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
    # the exchange is the truth for the just-closed bar AND the two before it (a copy on S1 may hold a forming bar written as final)
    first_open = bar_open - 2 * step if last is None else min(last + step, bar_open - 2 * step)
    error = "rest_paused"
    if not banned[0]:
        for attempt in range(6):
            try:
                rows = fetch(session, symbol, tf, first_open, bar_open, int(time.time() * 1000))
            except PermissionError as exc:
                banned[0] = True
                error = f"rest_banned {exc}"
                break
            except Exception as exc:
                error = f"rest_error {type(exc).__name__}: {exc}"[:200]
                time.sleep(0.5)
                continue
            if rows and epoch_of(rows[-1]["timestamp"]) == bar_open:
                try:
                    keep_self(name, rows)
                except Exception:
                    pass
                n, fixed = write_tail(target, rows)
                if n < 0:
                    return {"status": "write_failed", "code": n}
                status = "rest" if n else ("corrected" if fixed else "verified")
                return {"status": status, "n": n, "corrected": fixed, **({"at": time.time()} if n or fixed else {})}
            error = "rest_not_closed"
            time.sleep(0.5)
    # REST failed: fall back to a final copy already on S1 (missing bars only — never a correction from a copy)
    if last is not None and last >= bar_open:
        return {"status": "present_unverified", "error": error}
    want = set(range(last + step if last is not None else bar_open, bar_open + 1, step))
    for src in LOCAL_SOURCES:
        rows = [r for r in tail_rows(src / name) if sane(r) and epoch_of(r["timestamp"]) in want]
        if rows and max(epoch_of(r["timestamp"]) for r in rows) == bar_open:
            n, _ = write_tail(target, [r for r in rows if last is None or epoch_of(r["timestamp"]) > last])
            if n > 0:
                return {"status": "local", "src": src.name, "n": n, "error": error, "at": time.time()}
    return {"status": "missing", "error": error}


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
        if r["status"] not in ("verified", "corrected", "rest", "no_target"):
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
