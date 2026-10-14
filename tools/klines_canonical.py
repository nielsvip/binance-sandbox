#!/usr/bin/env python3
"""klines_canonical — ONE exchange-faithful kline store per crypto symbol/TF (USER 2026-10-14 "REAL prices, not some approximation").

Store: <BASE>/klines_canonical/{SYMBOL}_{TF}.json, TF in 15m/1h/4h/D. Rows = Binance USD-M futures /fapi/v1/klines for EXACTLY that symbol
(a USDC file never holds USDT-perp bars), exchange-native interval (no local resampling), CLOSED bars only (close_time < now - settle),
volume = k[5] (base-asset volume as the exchange defines it), timestamp = bar open time (UTC). Same 6-key JSON row format as every other
klines cache, so backtest_v8_precompute.load_klines reads it unchanged.

Why (truth audit 2026-10-14, 25 symbols x 15m/1h/4h/D vs fapi): the old caches mixed (a) USDT-perp bars saved under USDC names
(gateway_data_broadcaster maps USDT streams to USDC keys) — 40k of 98k sampled gateway bars, (b) ez_prices 3m->15m/1h/4h/D resamples with
closed/label='right' (each bar shifted by one 3m step; D labelled a day late), (c) unclosed snapshots frozen by first-write-wins appends,
(d) float32 round-trips in the S1 deep repo. _pick_best_source then switched between those copies per build.

Writers: ONLY this tool. Grow-only: rows are never deleted, the span never shrinks; a stored bar's values change only when the exchange
returns different values for that same closed bar (a canonical correction, logged). Fetching runs on the Mac (S1 never fetches, its IP
shares the live stack); S1/s2 receive the store by rsync (tools/klines_canonical_push.sh).

  python3 tools/klines_canonical.py fill   [--symbols A,B] [--tfs 15m,1h,4h,D] [--since 2025-08-01] [--rps 1.0]
  python3 tools/klines_canonical.py tail   [--symbols ...]               # one incremental pass (bars after the stored last bar)
  python3 tools/klines_canonical.py daemon [--rps 4]                     # tail at every 15m boundary + settle (flock singleton)
  python3 tools/klines_canonical.py verify --dir DIR [--json out]        # compare a cache dir against the store (per kind)
  python3 tools/klines_canonical.py repair --dir DIR [--apply]           # correct DIR's bars to the store, grow-only, gz backup first
"""
import argparse
import datetime as dt
import fcntl
import gzip
import json
import os
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("KLINES_CANONICAL_ROOT", str(Path(__file__).resolve().parents[1])))
STORE = Path(os.environ.get("KLINES_CANONICAL_DIR", str(ROOT / "klines_canonical")))
FAPI = "https://fapi.binance.com/fapi/v1/klines"
TFS = {"15m": ("15m", 900), "1h": ("1h", 3600), "4h": ("4h", 14400), "D": ("1d", 86400)}
SETTLE_MS = 2000
STATE = ROOT / "data" / "klines_canonical_state.json"
LOG = ROOT / "data" / "klines_canonical_log.jsonl"
BAN = ROOT / "data" / "klines_canonical_ban.json"
LOCK = Path(os.environ.get("KLINES_CANONICAL_LOCK", "/tmp/klines_canonical_daemon.lock"))
WLOCK = Path(os.environ.get("KLINES_CANONICAL_WLOCK", "/tmp/klines_canonical_write.lock"))
PUSH = ROOT / "tools" / "klines_canonical_push.sh"
WEIGHT_PAUSE = int(os.environ.get("KLINES_CANONICAL_WEIGHT_PAUSE", "1100"))
KEYS = ("open", "high", "low", "close", "volume")
sys.path.insert(0, str(ROOT / "tools"))
try:
    from excluded_tokenized_stocks import is_denied as _excl_denied
except Exception:
    def _excl_denied(_symbol):
        return False


def log(msg):
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def journal(rec):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def ts_ms(s):
    return int(dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() * 1000)


def row_of(k):
    return {"timestamp": iso(int(k[0])), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}


def universe():
    """NPZ/board universe first (data/klines_canonical_universe.json), then the live symbols.json — the 15m tail of the board set lands first."""
    first, rest = [], []
    try:
        first = list(json.loads((ROOT / "data" / "klines_canonical_universe.json").read_text()))
    except Exception:
        pass
    try:
        rest = list(json.loads((ROOT / "symbols.json").read_text()))
    except Exception:
        pass
    out, seen = [], set()
    for s in sorted(first) + sorted(rest):
        if s in seen or not s.endswith(("USDT", "USDC")) or _excl_denied(s):
            continue
        seen.add(s)
        out.append(s)
    return out


class Api:
    def __init__(self, rps):
        self.gap = 1.0 / max(rps, 0.05)
        self.last = 0.0
        self.calls = 0
        self.weight = 0

    def klines(self, symbol, tf, limit, start=None, end=None):
        if BAN.exists():
            try:
                until = json.loads(BAN.read_text()).get("until_ms", 0)
                if until > time.time() * 1000:
                    raise RuntimeError(f"banned until {iso(until)}")
            except (ValueError, OSError):
                pass
        while time.time() - self.last < self.gap:
            time.sleep(0.02)
        self.last = time.time()
        p = {"symbol": symbol, "interval": TFS[tf][0], "limit": int(limit)}
        if start is not None:
            p["startTime"] = int(start)
        if end is not None:
            p["endTime"] = int(end)
        req = urllib.request.Request(FAPI + "?" + urllib.parse.urlencode(p), headers={"User-Agent": "klines-canonical"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                self.weight = int(r.headers.get("X-MBX-USED-WEIGHT-1M", "0") or 0)
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf8", "ignore")
            if e.code in (418, 429):
                retry = int(e.headers.get("Retry-After", "120") or 120)
                BAN.parent.mkdir(parents=True, exist_ok=True)
                BAN.write_text(json.dumps({"code": e.code, "until_ms": int((time.time() + retry) * 1000), "body": body}))
                raise RuntimeError(f"HTTP {e.code} rate limit — stopped, Retry-After {retry}s")
            if e.code == 400 and "Invalid symbol" in body:
                return None
            raise RuntimeError(f"HTTP {e.code} {body}")
        self.calls += 1
        if self.weight > WEIGHT_PAUSE:
            log(f"used weight {self.weight} > {WEIGHT_PAUSE} — pausing 60 s")
            time.sleep(60)
        return data


def load_rows(path):
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, list) else []
    except Exception:
        return []


def write_rows(path, rows):
    """atomic grow-only write: refuses fewer rows or a shorter span than the file already has."""
    p = Path(path)
    old = load_rows(p) if p.exists() else []
    if old:
        if len(rows) < len(old):
            return False, f"REFUSED shrink {len(old)}->{len(rows)}"
        if ts_ms(rows[0]["timestamp"]) > ts_ms(old[0]["timestamp"]) or ts_ms(rows[-1]["timestamp"]) < ts_ms(old[-1]["timestamp"]):
            return False, "REFUSED span shrink"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(rows))
    os.replace(tmp, p)
    return True, f"wrote {len(rows)}"


def merge_exchange(path, klines, now_ms):
    """merge exchange klines (closed only) into the store file under the store write lock. Returns (added, corrected, rows)."""
    WLOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(WLOCK, "a+") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        return _merge_exchange(path, klines, now_ms)


def _merge_exchange(path, klines, now_ms):
    rows = load_rows(path)
    by = {}
    for r in rows:
        try:
            by[ts_ms(r["timestamp"])] = r
        except Exception:
            continue
    added = corrected = 0
    for k in klines:
        if int(k[6]) >= now_ms - SETTLE_MS:
            continue
        t = int(k[0])
        nr = row_of(k)
        o = by.get(t)
        if o is None:
            added += 1
        elif any(float(o.get(c, 0) or 0) != nr[c] for c in KEYS):
            corrected += 1
            journal({"at": iso(now_ms), "event": "canonical_correction", "file": Path(path).name, "ts": nr["timestamp"], "old": {c: o.get(c) for c in KEYS}, "new": {c: nr[c] for c in KEYS}})
        else:
            continue
        by[t] = nr
    if not (added or corrected):
        return 0, 0, rows
    out = [by[t] for t in sorted(by)]
    ok, why = write_rows(path, out)
    if not ok:
        log(f"{Path(path).name}: {why}")
        return 0, 0, rows
    return added, corrected, out


def fill_symbol_tf(api, symbol, tf, since_ms, store=STORE):
    """page backwards from now to since_ms (or the listing), then forward-merge; closed bars only."""
    path = store / f"{symbol}_{tf}.json"
    sec_ms = TFS[tf][1] * 1000
    have = load_rows(path)
    have_ts = set()
    for r in have:
        try:
            have_ts.add(ts_ms(r["timestamp"]))
        except Exception:
            pass
    first_have = min(have_ts) if have_ts else None
    got = []
    end = None
    pages = 0
    if first_have is not None:
        if first_have <= since_ms:
            r = tail_symbol_tf(api, symbol, tf, store=store)
            return {"symbol": symbol, "tf": tf, "status": "covered", "pages": 0, "added": r["added"] if r else 0, "corrected": r["corrected"] if r else 0, "rows": len(load_rows(path))}
        end = first_have + sec_ms * 3  # extend history backwards from the stored head (3-bar overlap re-verifies the seam); tail covers newer
    while True:
        d = api.klines(symbol, tf, 1000, end=end)
        if d is None:
            return {"symbol": symbol, "tf": tf, "status": "invalid_symbol"}
        pages += 1
        if not d:
            break
        got.extend(d)
        oldest = int(d[0][0])
        if oldest <= since_ms or len(d) < 1000:
            break
        end = oldest - 1
    now_ms = int(time.time() * 1000)
    got = [k for k in got if int(k[0]) >= since_ms - sec_ms]
    a, c, rows = merge_exchange(path, got, now_ms)
    return {"symbol": symbol, "tf": tf, "status": "ok", "pages": pages, "added": a, "corrected": c, "rows": len(rows)}


def tail_symbol_tf(api, symbol, tf, recheck=3, store=STORE):
    """incremental: bars after the stored last bar + re-check the last `recheck` stored bars."""
    path = store / f"{symbol}_{tf}.json"
    rows = load_rows(path)
    if not rows:
        return None
    sec_ms = TFS[tf][1] * 1000
    last = ts_ms(rows[-1]["timestamp"])
    now_ms = int(time.time() * 1000)
    latest_open = (now_ms // sec_ms) * sec_ms
    if latest_open - sec_ms <= last and recheck <= 0:
        return {"symbol": symbol, "tf": tf, "added": 0, "corrected": 0}
    start = last - recheck * sec_ms
    need = int((latest_open - start) // sec_ms) + 2
    total_a = total_c = 0
    while need > 0:
        lim = min(1000, need)
        d = api.klines(symbol, tf, lim, start=start)
        if not d:
            break
        a, c, _ = merge_exchange(path, d, int(time.time() * 1000))
        total_a += a
        total_c += c
        if len(d) < lim:
            break
        start = int(d[-1][0]) + sec_ms
        need -= lim
    return {"symbol": symbol, "tf": tf, "added": total_a, "corrected": total_c}


def cmd_fill(a):
    syms = a.symbols.split(",") if a.symbols else universe()
    tfs = a.tfs.split(",")
    since = ts_ms(a.since + "T00:00:00Z")
    api = Api(a.rps)
    STORE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for i, s in enumerate(syms, 1):
        for tf in tfs:
            s_since = since
            if tf in ("4h", "D") and a.htf_since:
                s_since = ts_ms(a.htf_since + "T00:00:00Z")
            try:
                r = fill_symbol_tf(api, s, tf, s_since)
            except RuntimeError as e:
                log(f"STOP {s} {tf}: {e}")
                return 2
            except Exception as e:
                log(f"{s} {tf}: {type(e).__name__} {e}")
                continue
            log(f"[{i}/{len(syms)}] {s} {tf} {r} calls={api.calls} w={api.weight}")
    log(f"fill done {len(syms)} syms in {time.time()-t0:.0f}s calls={api.calls}")
    return 0


def cmd_tail(a, api=None, tfs=None):
    syms = a.symbols.split(",") if getattr(a, "symbols", None) else universe()
    api = api or Api(a.rps)
    tfs = tfs or list(TFS)
    tot = {"added": 0, "corrected": 0, "missing_store": 0, "errors": 0}
    for s in syms:
        for tf in tfs:
            try:
                r = tail_symbol_tf(api, s, tf)
            except RuntimeError as e:
                log(f"STOP tail {s} {tf}: {e}")
                tot["errors"] += 1
                return tot
            except Exception as e:
                tot["errors"] += 1
                log(f"tail {s} {tf}: {type(e).__name__} {e}")
                continue
            if r is None:
                tot["missing_store"] += 1
                continue
            tot["added"] += r["added"]
            tot["corrected"] += r["corrected"]
    return tot


def cmd_daemon(a):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another daemon holds the lock — exit")
        return 0
    api = Api(a.rps)
    log(f"daemon start rps={a.rps} store={STORE}")
    while True:
        now = time.time()
        nxt = (int(now) // 900 + 1) * 900 + a.settle_s
        time.sleep(max(0.0, nxt - time.time()))
        t = int(time.time()) - a.settle_s
        tfs = ["15m"] + [tf for tf in ("1h", "4h", "D") if t % TFS[tf][1] < 900]
        t0 = time.time()
        a.symbols = ""
        tot = cmd_tail(a, api=api, tfs=tfs)
        st = {"at": iso(int(time.time() * 1000)), "tfs": tfs, "took_s": round(time.time() - t0, 1), "calls": api.calls, "weight": api.weight, **tot}
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(st))
        except Exception:
            pass
        log(f"cycle {st}")
        if PUSH.exists() and os.environ.get("KLINES_CANONICAL_PUSH", "1") != "0":
            try:
                import subprocess
                subprocess.Popen(["/bin/bash", str(PUSH)], stdout=open(ROOT / "data" / "klines_canonical_push.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)
            except Exception as e:
                log(f"push spawn failed: {e}")
        if a.once:
            return 0


def compare_dir(cache_dir, store=STORE, tfs=tuple(TFS)):
    """per file: compared / exact / bad (+ kinds) / missing-in-span. Store = truth."""
    cache_dir = Path(cache_dir)
    res = {"files": 0, "compared": 0, "bad": 0, "missing_in_span": 0, "by_tf": {}}
    for sp in sorted(store.glob("*_*.json")):
        sym, tf = sp.name[:-5].rsplit("_", 1)
        if tf not in tfs:
            continue
        cp = cache_dir / sp.name
        if not cp.exists():
            continue
        truth = {}
        for r in load_rows(sp):
            truth[ts_ms(r["timestamp"])] = r
        rows = load_rows(cp)
        if not rows:
            continue
        res["files"] += 1
        bt = res["by_tf"].setdefault(tf, {"compared": 0, "bad": 0, "missing_in_span": 0})
        have = set()
        for r in rows:
            try:
                t = ts_ms(r["timestamp"])
            except Exception:
                continue
            have.add(t)
            x = truth.get(t)
            if x is None:
                continue
            res["compared"] += 1
            bt["compared"] += 1
            if any(abs(float(r.get(c, 0) or 0) - float(x[c])) > 1e-12 * max(1.0, abs(float(x[c]))) for c in KEYS):
                res["bad"] += 1
                bt["bad"] += 1
        if have and truth:
            lo, hi = min(have), max(have)
            m = sum(1 for t in truth if lo <= t <= hi and t not in have)
            res["missing_in_span"] += m
            bt["missing_in_span"] += m
    return res


def cmd_verify(a):
    r = compare_dir(a.dir)
    r["dir"] = str(a.dir)
    r["pct_bad"] = round(100.0 * r["bad"] / max(r["compared"], 1), 3)
    print(json.dumps(r, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(r, indent=1))
    return 0


def repair_file(cache_path, store_path):
    """correct every bar of cache_path that the store holds (values -> exchange) and insert store bars missing inside/after its span.
    Rows the store does not hold are kept untouched (grow-only). Returns (new_rows or None, n_corrected, n_inserted)."""
    rows = load_rows(cache_path)
    truth = load_rows(store_path)
    if not rows or not truth:
        return None, 0, 0
    by = {}
    order = []
    for r in rows:
        try:
            t = ts_ms(r["timestamp"])
        except Exception:
            continue
        if t not in by:
            order.append(t)
        by[t] = r
    lo = min(by)
    corrected = inserted = 0
    for x in truth:
        t = ts_ms(x["timestamp"])
        if t < lo:
            continue  # never prepend history the copy did not have (its owner decides depth); only correct + fill inside/after
        o = by.get(t)
        if o is None:
            by[t] = dict(x)
            inserted += 1
        elif any(abs(float(o.get(c, 0) or 0) - float(x[c])) > 1e-12 * max(1.0, abs(float(x[c]))) for c in KEYS):
            nr = dict(o)
            nr.update({c: x[c] for c in KEYS})
            nr["timestamp"] = o.get("timestamp", x["timestamp"])
            by[t] = nr
            corrected += 1
    if not (corrected or inserted):
        return None, 0, 0
    return [by[t] for t in sorted(by)], corrected, inserted


def cmd_repair(a):
    cache_dir = Path(a.dir).resolve()
    stamp = time.strftime("%Y%m%d%H%M", time.gmtime())
    plan = []
    for sp in sorted(STORE.glob("*_*.json")):
        cp = cache_dir / sp.name
        if not cp.exists():
            continue
        new, c, i = repair_file(cp, sp)
        if new is not None:
            plan.append((cp, new, c, i))
    tot_c = sum(p[2] for p in plan)
    tot_i = sum(p[3] for p in plan)
    log(f"repair {cache_dir}: {len(plan)} files, {tot_c} bars corrected, {tot_i} bars inserted (apply={a.apply})")
    if not a.apply or not plan:
        return 0
    bdir = ROOT / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    tgz = bdir / f"before_klines_canonical_repair_{cache_dir.name}_{stamp}.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        for cp, _, _, _ in plan:
            tf.add(str(cp), arcname=f"{cache_dir.name}/{cp.name}")
    log(f"backup {tgz} ({tgz.stat().st_size/1e6:.1f} MB)")
    try:
        from klines_guard import guarded_write
    except Exception:
        guarded_write = None
    done = refused = 0
    for cp, new, c, i in plan:
        if guarded_write is not None:
            ok, why = guarded_write(cp, new)
        else:
            ok, why = write_rows(cp, new)
        if ok:
            done += 1
            journal({"at": iso(int(time.time() * 1000)), "event": "canonical_repair", "file": str(cp), "corrected": c, "inserted": i})
        else:
            refused += 1
            log(f"refused {cp.name}: {why}")
    log(f"repair applied {done} files, refused {refused}; backup {tgz}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill")
    f.add_argument("--symbols", default="")
    f.add_argument("--tfs", default="15m,1h,4h,D")
    f.add_argument("--since", default="2025-08-01")
    f.add_argument("--htf-since", default="2022-01-01")
    f.add_argument("--rps", type=float, default=1.0)
    t = sub.add_parser("tail")
    t.add_argument("--symbols", default="")
    t.add_argument("--rps", type=float, default=4.0)
    d = sub.add_parser("daemon")
    d.add_argument("--rps", type=float, default=4.0)
    d.add_argument("--settle-s", type=int, default=8)
    d.add_argument("--once", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("--dir", required=True)
    v.add_argument("--json", default="")
    r = sub.add_parser("repair")
    r.add_argument("--dir", required=True)
    r.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.cmd == "fill":
        return cmd_fill(a)
    if a.cmd == "tail":
        print(json.dumps(cmd_tail(a)))
        return 0
    if a.cmd == "daemon":
        return cmd_daemon(a)
    if a.cmd == "verify":
        return cmd_verify(a)
    if a.cmd == "repair":
        return cmd_repair(a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
