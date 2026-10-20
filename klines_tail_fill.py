"""klines_tail_fill — stay up to date without ever asking Binance for more than MAX_REST_BARS bars.

USER 2026-10-20: "just get the bars you need then - anyway make sure we NEVER EVER need to get > 5 bars again as we are
STAYING UP TO DATE". Klines are backed up on S1 + 3 external drives, so a gap is never a reason to hit the API.

Order for every live cache file (ez_klines + ez_prices share config.KLINES_CACHE_DIR):
  1. local stores on this host (gateway mirror, canonical) — free
  2. S1 (klines_cache/gateway, then klines_cache) in ONE batched rsync — free, Mac only
  3. REST for exactly the bars still missing: own last bar (re-finalised) .. the forming bar
Only rows NEWER than the file's own last bar are ever taken from another store, so S1's pre-listing USDT history under
USDC names can never leak in.
"""
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_REST_BARS = 5
TF_MS = {"1m": 60000, "3m": 180000, "5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000, "4h": 14400000, "D": 86400000, "1d": 86400000}
LOCAL_DIRS = ("klines_cache/gateway", "klines_cache_gateway", "klines_canonical", "klines_cache_macbook")
S1_HOST = os.environ.get("KLINES_S1_HOST", "s1-int")
S1_DIR = os.environ.get("KLINES_S1_DIR", "binance-sandbox/klines_cache")
S1_SUBDIRS = ("gateway/", "")
_TS_RE = re.compile(rb'"timestamp"\s*:\s*"([0-9T:\-\.]+)Z?"')
_s1_last_try = {}


def ts_ms(ts):
    s = str(ts).rstrip("Z").replace(" ", "T")[:19]
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)


def last_ms(path):
    """last bar open time of a kline JSON list, read from the tail only (files are timestamp-ascending)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 2048))
            hits = _TS_RE.findall(fh.read())
        if hits:
            return ts_ms(hits[-1].decode())
    except (OSError, ValueError):
        pass
    return None


def bars_needed(path, interval, now_ms=None):
    """bars from the file's own last bar (inclusive, so it is re-finalised) up to the forming bar; None = no file."""
    step = TF_MS.get(interval)
    last = last_ms(path)
    if step is None or last is None:
        return None
    now_ms = now_ms or int(time.time() * 1000)
    return max(1, (now_ms - last) // step + 1)


def rows_after(path, after_ms):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for r in data:
        try:
            if isinstance(r, dict) and ts_ms(r["timestamp"]) > after_ms:
                out.append({k: r[k] for k in ("timestamp", "open", "high", "low", "close", "volume") if k in r})
        except (KeyError, ValueError):
            continue
    return out


def local_tail_rows(base, symbol, interval, after_ms, skip_dir=None):
    """rows newer than after_ms from this host's other stores (freshest store wins per timestamp)."""
    by_ts = {}
    for d in LOCAL_DIRS:
        p = Path(base) / d / f"{symbol}_{interval}.json"
        if skip_dir is not None and p.parent.resolve() == Path(skip_dir).resolve():
            continue
        if p.exists():
            for r in rows_after(p, after_ms):
                by_ts[r["timestamp"][:19]] = r
    return [by_ts[k] for k in sorted(by_ts)]


def load_inception(base):
    """data/klines_inception.json (written by ez_klines.mark_inception): '<symbol>_<tf>' -> {'earliest': listing bar}."""
    try:
        with open(Path(base) / "data" / "klines_inception.json") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def mark_inception(base, symbol, interval, earliest_iso):
    path = Path(base) / "data" / "klines_inception.json"
    inc = load_inception(base)
    inc[f"{symbol}_{interval}"] = {"earliest": str(earliest_iso)[:19], "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(inc, indent=1, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        pass


def at_inception(inc, symbol, interval, earliest_iso):
    """True while a file still starts at (or before) the bar Binance confirmed has nothing older."""
    rec = inc.get(f"{symbol}_{interval}")
    return bool(rec) and earliest_iso is not None and str(earliest_iso).replace(" ", "T")[:19] <= str(rec.get("earliest", ""))[:19]


def prefill(base, cache_dir, items, use_s1=True, log=None):
    """items = [(symbol, interval)]. Before any REST call: for each file more than MAX_REST_BARS behind, merge newer rows
    from local stores, then (one batched rsync) from S1. Returns {(symbol, interval): rows} to hand to the caller's own
    merge writer. Files with no own copy (cold) are seeded only from S1's gateway/ copy — exchange-native bars."""
    out, behind = {}, {}
    now_ms = int(time.time() * 1000)
    for symbol, interval in dict.fromkeys(items):
        if interval not in TF_MS:
            continue
        own = Path(cache_dir) / f"{symbol}_{interval}.json"
        need = bars_needed(own, interval, now_ms)
        if need is not None and need <= MAX_REST_BARS:
            continue
        cold = need is None
        own_last = 0 if cold else last_ms(own)
        rows = local_tail_rows(base, symbol, interval, own_last, skip_dir=cache_dir)
        step = TF_MS.get(interval)
        if rows:
            out[(symbol, interval)] = rows
            after = ts_ms(rows[-1]["timestamp"])
            # a cold file seeded locally becomes warm from the seed's first bar; a warm one must join its own last bar
            start = ts_ms(rows[0]["timestamp"]) if cold else own_last
            joined = cold or ts_ms(rows[0]["timestamp"]) <= own_last + step
            hole = joined and len(rows) + (0 if cold else 1) < (after - start) // step + 1
            if joined and not hole and (now_ms - after) // step + 1 <= MAX_REST_BARS:
                continue
            if cold:
                own_last, cold = start - 1, False
        # S1 is asked from the file's OWN last bar, so a hole between it and a local store's tail is filled too
        behind[f"{symbol}_{interval}.json"] = (symbol, interval, own_last, cold)
    if use_s1 and behind:
        warm = {n: v[2] for n, v in behind.items() if not v[3]}
        # cold seed from gateway/ only: S1's root copy carries pre-listing USDT bars under USDC names
        cold = {n: 0 for n, v in behind.items() if v[3]}
        got = s1_tail(warm) if warm else {}
        got_cold = s1_tail(cold, subdirs=("gateway/",)) if cold else {}
        for n, rows in list(got.items()) + list(got_cold.items()):
            key = behind[n][:2]
            out[key] = out.get(key, []) + rows
        if log:
            log(f"[tail_fill] {len(behind)} files >{MAX_REST_BARS} bars behind after local stores -> S1 supplied {len(got)}/{len(warm)} warm, {len(got_cold)}/{len(cold)} cold")
    return out


def s1_tail(wanted, timeout=90, subdirs=S1_SUBDIRS):
    """wanted = {'<symbol>_<tf>.json': after_ms}. ONE rsync from S1 (gateway/ copy wins over the root copy), returns
    {name: rows newer than after_ms}. A name tried in the last 10 minutes is skipped (S1 lags one 15m append cycle)."""
    now = time.time()
    names = [n for n in wanted if now - _s1_last_try.get(n, 0) > 600]
    if not names:
        return {}
    for n in names:
        _s1_last_try[n] = now
    out = {}
    with tempfile.TemporaryDirectory(prefix="klines_s1_tail_") as tmpdir:
        tmp = Path(tmpdir)
        lst = tmp / "files.txt"
        lst.write_text("".join(f"{sub}{n}\n" for n in names for sub in subdirs))
        ssh = "ssh -o BatchMode=yes -o ConnectTimeout=10"
        try:
            subprocess.run(["rsync", "-a", "--timeout=60", "--ignore-missing-args", "-e", ssh, f"--files-from={lst}", f"{S1_HOST}:{S1_DIR}/", str(tmp / "s1") + "/"], capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return {}
        for n in names:
            by_ts = {}
            for sub in reversed(subdirs):
                p = tmp / "s1" / f"{sub}{n}"
                if p.exists():
                    for r in rows_after(p, wanted[n]):
                        by_ts[r["timestamp"][:19]] = r
            if by_ts:
                out[n] = [by_ts[k] for k in sorted(by_ts)]
    return out
