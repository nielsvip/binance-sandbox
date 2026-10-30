#!/usr/bin/env python3
"""npz_premarket_temp_bars — the three premarket 15m bars before the stock open: real klines + TEMPORARY live NPZ rows.

USER 2026-10-29: "klines exist from pre-market 8:45 ET; 3 temporary NPZ bars are added for 8:45, 9:00 and 9:15 ... both 7D trc
and 30D trb trades start at 9:30 ... the 3 temp NPZ bars must be erased before the 9:45 vec trader decisions". USER 2026-10-30:
"DO THAT NOW"; "MAKE sure these kline bars do arrive and are used for the calculations of npz"; "UTC ONLY ALWAYS EVERYWHERE".

All times here are UTC epochs. The bars are the three 15m bars that end at the session open (open - 2700 / -1800 / -900 s, from the
exchange calendar's UTC open). For a 13:30Z open that is 12:45Z, 13:00Z and 13:15Z.

KLINES (S5, cron every minute 12-15Z): each bar that has closed (+5 s) and is not yet in the host's Tradier 15m kline file is taken
       from Tradier /markets/timesales (interval 15min, session_filter all; bar identified by Tradier's UTC epoch `timestamp`) and
       written ONCE into klines_cache/tradier/<SYM>_15m.json through closed_bar_feed.merge_closed_history (the same locked writer
       the closed-bar bridge uses). It stays there: every later NPZ row (incl. the open bar's permanent row) is computed on it.
ROWS   for each such bar one TEMPORARY row is computed from those klines (npz_tail_service._compute_row_process, same incremental
       compute as the tail service) and appended via npz_tail_store.append_tail_row. Ledger data/npz_premarket_temp/<YYYYMMDD>.json.
ERASE  from open + 600 s, under the store lock every temp row is removed from the sidecar and the zip (the compactor folds rows into
       the zip within minutes); RTH rows are never touched; re-read and verified. The klines are NOT erased.
GUARD  data/npz_premarket_temp/ACTIVE exists while temp rows may be in the store; S5's STOCK_PUSHBACK cron skips its rsync to S1
       while it is fresh (a pushed zip would carry the temp rows to S1 and from there to every worker).
OFF    data/npz_premarket_temp/OFF stops new klines and rows (erase still runs).

The engine skips every bar before the session open (v12_quick_engine rth_mask: no entry, no exit), so the rows create no premarket
trade. They make the open-bar state current (npz_tail = the bar ending at the open, inside the trader's 30-minute freshness
window), so tra/trb/trc plan at the open instead of 15 minutes later. No full NPZ calculation; every row is computed once.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
UTC = dt.timezone.utc
OFFSETS = (2700, 1800, 900)  # the three 15m bars ending at the session open
ERASE_AFTER_OPEN_S = 600  # open + 10 min: five minutes before the open bar closes and its decisions are made
SETTLE_S = 5
WORKERS = int(os.environ.get("PM_TEMP_WORKERS", "12"))  # measured 10-30: 121 rows in 14.6 s at 12 workers, 23.3 s at 6 (load 19)


def log(msg):
    print(f"[{dt.datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
    os.replace(tmp, path)


def utc_day(epoch):
    return dt.datetime.fromtimestamp(float(epoch), UTC).date()


def session_open(day):
    """UTC epoch of the exchange open on this UTC date (the US session never crosses a UTC midnight)."""
    from tools.npz_cutoffs import stock_session_bounds

    bounds = stock_session_bounds(day)
    return int(bounds[0]) if bounds else None


def temp_epochs(day):
    opened = session_open(day)
    return [opened - offset for offset in OFFSETS] if opened else []


def iso(epoch):
    return dt.datetime.fromtimestamp(int(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def symbols(live_root):
    """The stock symbols the S5 traders (tra/trb/trc) trade or hold, and the two state watchers' side files."""
    out = set()
    for book in ("tra", "trb"):
        for side in ("long", "short"):
            for symbol in read_json(Path(live_root) / f"symbols_{book}_{side}.json", []) or []:
                out.add(str(symbol).upper())
    for path in (Path.home() / "fwd_stocks_syms.txt", Path.home() / "fwd_trc7d_syms.txt"):
        try:
            out.update(s.rsplit("_", 1)[0].upper() for s in path.read_text().split() if s.endswith(("_LONG", "_SHORT")))
        except OSError:
            pass
    for path in (Path(live_root) / "data" / "vector_direct" / "held_stocks.json", Path.home() / "trc7d_root" / "data" / "vector_direct" / "held_stocks.json"):
        for side in (read_json(path, {}) or {}).get("sides") or []:
            out.add(str(side).rsplit("_", 1)[0].upper())
    return sorted(out)


def tradier_keys(base):
    """Market-data tokens TRB then TRA (distinct accounts = distinct 120/min buckets). The ~120 symbol calls at the open bar are
    split across them so neither bucket runs dry; the bar guarantee's own fallback (TRA) fetches only a handful of bars."""
    keys = []
    for name in ("TRADIER_API_KEY_TRB", "TRADIER_API_KEY_TRA"):
        key = tradier_key(base, name)
        if key and key not in keys:
            keys.append(key)
    return keys or [k for k in [tradier_key(base)] if k]


def tradier_key(base, only=None):
    """Market-data token. TRB by default: the bar guarantee's own Tradier fallback uses TRA, so the two never share a rate bucket."""
    env = {}
    for path in (Path(base) / ".env", Path.home() / ".env", Path.home() / "binance" / ".env"):
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if "=" in line and not line.startswith("#"):
                    name, value = line.split("=", 1)
                    env.setdefault(name.strip(), value.strip().strip("'\""))
        except OSError:
            continue
    for name in ((only,) if only else (os.environ.get("PM_TEMP_TRADIER_KEY", ""), "TRADIER_API_KEY_TRB", "TRADIER_API_KEY_TRA", "TRADIER_API_KEY")):
        if name and (os.environ.get(name) or env.get(name)):
            return os.environ.get(name) or env.get(name)
    return None


def kline_file(base, symbol):
    """The host's Tradier 15m kline history (the file the NPZ row compute and the closed-bar bridge use)."""
    return Path(base) / "klines_cache" / "tradier" / f"{symbol}_15m.json"


def klines_have(base, symbol, epochs):
    """Which of `epochs` the host kline file already holds (UTC stamp of the bar open, read from the file tail)."""
    path = kline_file(base, symbol)
    try:
        with open(path, "rb") as stream:
            stream.seek(max(0, path.stat().st_size - 131072))
            tail = stream.read().decode("utf-8", errors="ignore")
    except OSError:
        return set()
    return {e for e in epochs if re.search(r'"timestamp"\s*:\s*"' + re.escape(iso(e)[:19]), tail)}


def fetch_premarket(key, symbol, day, epochs, now):
    """The closed bars among `epochs` from Tradier /markets/timesales (15min, session_filter all) for that date; each bar is matched
    on Tradier's own UTC epoch `timestamp`, never on its local `time` string. {epoch: kline row} or (None, why)."""
    import requests

    params = {"symbol": symbol, "interval": "15min", "start": f"{day.isoformat()} 00:00", "end": f"{day.isoformat()} 23:59", "session_filter": "all"}
    for attempt in range(3):
        r = requests.get("https://api.tradier.com/v1/markets/timesales", params=params, timeout=10,
                         headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        if r.status_code == 429:
            time.sleep(2 + 3 * attempt)
            continue
        if r.status_code != 200:
            return None, f"HTTP {r.status_code} {r.text[:80]}"
        try:
            available = int(r.headers.get("X-Ratelimit-Available") or 999)
            expiry = float(r.headers.get("X-Ratelimit-Expiry") or 0) / 1000.0
        except ValueError:
            available, expiry = 999, 0
        if available < 15 and expiry > time.time():
            time.sleep(min(30.0, expiry - time.time() + 0.5))  # leave the bucket to the live lanes sharing it
        data = ((r.json() or {}).get("series") or {}).get("data") or []
        data = data if isinstance(data, list) else [data]
        out = {}
        for d in data:
            stamp = int(d.get("timestamp") or 0)
            if stamp in epochs and stamp + 900 + SETTLE_S <= now:
                out[stamp] = {"timestamp": iso(stamp), "open": float(d["open"]), "high": float(d["high"]), "low": float(d["low"]),
                              "close": float(d["close"]), "volume": float(d.get("volume") or 0), "_closed": True, "_provisional": False,
                              "_closed_verified_at": time.time(), "_source": "tradier_timesales_15min_premarket"}
        return out, ""
    return None, "HTTP 429 three times"


def land_klines(base, symbol, bars):
    """Write each bar ONCE into the host kline history (closed_bar_feed.merge_closed_history, the bridge's locked writer).
    Returns the epochs now present."""
    from closed_bar_feed import merge_closed_history

    path = kline_file(base, symbol)
    if not path.exists():
        raise FileNotFoundError(f"{symbol}: no Tradier 15m kline history {path}")
    have = klines_have(base, symbol, set(bars))
    for epoch in sorted(set(bars) - have):
        merge_closed_history(path, bars[epoch])
    return klines_have(base, symbol, set(bars))


def _add_one(task):
    """Land the fetched klines of one symbol, then append its temp rows in time order (stops at the first failure)."""
    base, ind, symbol, bars, epochs = task
    import npz_tail_service as T
    from npz_tail_store import append_tail_row

    bars = {int(k): v for k, v in bars.items()}
    try:
        present = land_klines(base, symbol, bars) if bars else klines_have(base, symbol, set(epochs))
    except Exception as exc:
        return [(symbol, min(epochs), f"klines {type(exc).__name__}: {exc}")]
    out = []
    for epoch in epochs:
        if epoch not in present:
            out.append((symbol, epoch, "kline not in the host file"))
            break
        row = T._compute_row_process((symbol, int(epoch), str(base), "tradier"))
        if not row:
            out.append((symbol, epoch, "compute returned nothing"))
            break
        final = dict(row, timestamps=int(epoch), _premarket_temp=True)
        for tf in ("15m", "5m", "3m", "1m"):
            final[f"timestamp_{tf}"] = int(epoch)
        try:
            ok = append_tail_row(str(Path(ind) / f"{symbol}.npz"), final, now=time.time())
        except Exception as exc:
            out.append((symbol, epoch, f"append {type(exc).__name__}: {exc}"))
            break
        out.append((symbol, epoch, "appended" if ok else "append refused"))
        if not ok:
            break
    return out


def _erase_one(task):
    ind, symbol, epochs = task
    from npz_tail_store import _store_lock, tail_path_for_npz, tail_rows

    path = Path(ind) / f"{symbol}.npz"
    bad = {int(e) for e in epochs}
    result = {"sidecar": 0, "zip": 0}
    if not path.exists():
        return symbol, dict(result, error="no npz")
    try:
        with _store_lock(path):
            sidecar = tail_path_for_npz(path)
            if sidecar.exists():
                keep = []
                for line in sidecar.read_bytes().splitlines(keepends=True):
                    try:
                        stamp = int(json.loads(line).get("timestamps", 0))
                    except Exception:
                        keep.append(line)
                        continue
                    if stamp in bad:
                        result["sidecar"] += 1
                    else:
                        keep.append(line)
                if result["sidecar"]:
                    tmp = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.erase")
                    tmp.write_bytes(b"".join(keep))
                    os.replace(tmp, sidecar)
            with np.load(str(path), allow_pickle=True) as archive:
                data = {key: archive[key] for key in archive.files}
            ts = np.asarray(data["timestamps"]).astype(np.int64).reshape(-1)
            scale = 1000 if len(ts) and int(ts[-1]) > 10**11 else 1
            drop = np.isin(ts // scale, np.asarray(sorted(bad), dtype=np.int64))
            if drop.any():
                n = len(ts)
                out = {key: (value[~drop] if isinstance(value, np.ndarray) and value.ndim > 0 and len(value) == n else value) for key, value in data.items()}
                # own writer: npz_live_generator._save_npz_atomic refuses any row-count drop (never-shrink), and these rows are ours
                tmp = path.with_name(f".{path.name}.{os.getpid()}.erase.npz")
                np.savez_compressed(str(tmp), **out)
                os.replace(str(tmp), str(path))
                result["zip"] = int(drop.sum())
                with np.load(str(path), allow_pickle=True) as archive:
                    after = np.asarray(archive["timestamps"]).astype(np.int64).reshape(-1)
                if len(after) != n - result["zip"] or np.isin(after // scale, list(bad)).any() or not np.array_equal(after, ts[~drop]):
                    return symbol, dict(result, error="verify failed after zip rewrite")
            if any(int(r["timestamps"]) in bad for r in tail_rows(path)):
                return symbol, dict(result, error="verify failed: temp row still in sidecar")
    except Exception as exc:
        return symbol, dict(result, error=f"{type(exc).__name__}: {exc}")
    return symbol, result


class Run:
    def __init__(self, a):
        self.base = Path(a.base).expanduser()
        self.live = Path(a.live_root).expanduser()
        self.ind = Path(a.ind_dir).expanduser() if a.ind_dir else self.base / "backtest_v8" / "indicators"
        self.out = Path(a.out_dir).expanduser() if a.out_dir else self.base / "data" / "npz_premarket_temp"
        self.test = bool(a.now)
        self.now = float(a.now) if a.now else time.time()
        self.day = dt.date.fromisoformat(a.day) if a.day else utc_day(self.now)
        self.only = {s.strip().upper() for s in (a.symbols or "").split(",") if s.strip()}
        self.workers = max(1, a.workers)
        self.no_klines = a.no_klines

    def ledger_path(self, day):
        return self.out / f"{day.strftime('%Y%m%d')}.json"

    def ledger(self, day):
        return read_json(self.ledger_path(day), None) or {"day": day.isoformat(), "epochs": temp_epochs(day), "added": {}, "skipped": {}, "events": []}

    def universe(self):
        names = symbols(self.live)
        if self.only:
            names = [s for s in names if s in self.only] or sorted(self.only)
        return [s for s in names if (self.ind / f"{s}.npz").exists()]

    def add(self):
        import npz_tail_service as T

        epochs = temp_epochs(self.day)
        if not epochs:
            return {"skip": f"{self.day} has no session"}
        if (self.out / "OFF").exists():
            return {"skip": "OFF switch"}
        due = [e for e in epochs if e + 900 + SETTLE_S <= self.now]
        if not due:
            return {"skip": "no premarket bar has closed yet"}
        led = self.ledger(self.day)
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "ACTIVE").write_text(json.dumps({"day": self.day.isoformat(), "at": self.now}) + "\n")
        keys = tradier_keys(self.base)
        if not keys:
            return {"error": "no Tradier market-data key"}
        skipped, wanted, need_fetch = {}, {}, {}
        for symbol in self.universe():
            have = {int(e) for e in led["added"].get(symbol, [])}
            tip = T.tip_of(str(self.ind / f"{symbol}.npz"))
            todo = []
            for epoch in due:
                if epoch in have:
                    continue
                if tip is not None and int(tip) >= epoch:
                    skipped.setdefault(symbol, {})[str(epoch)] = f"store tip {iso(tip)} is not before {iso(epoch)}"
                    continue
                todo.append(epoch)
            if todo:
                wanted[symbol] = todo
                missing = set(todo) - klines_have(self.base, symbol, set(todo))
                if missing and not self.no_klines:
                    need_fetch[symbol] = missing
        t0 = time.time()
        order = {symbol: i for i, symbol in enumerate(sorted(need_fetch))}
        latency = led.setdefault("fetch_latency_s", {})  # epoch -> {symbol: seconds after the bar close when Tradier served it}

        def fetch(symbol):
            key = keys[order[symbol] % len(keys)]
            try:
                return symbol, fetch_premarket(key, symbol, self.day, need_fetch[symbol], self.now if self.test else time.time())
            except Exception as exc:
                return symbol, (None, f"{type(exc).__name__}: {str(exc)[:100]}")

        def task_for(symbol, bars):
            # rows go in time order; a bar that is in neither the host file nor Tradier stops that symbol's chain this pass
            ready = []
            for epoch in wanted[symbol]:
                if epoch in bars or epoch not in need_fetch.get(symbol, ()):
                    ready.append(epoch)
                else:
                    break
            return (str(self.base), str(self.ind), symbol, {e: bars[e] for e in ready if e in bars}, ready) if ready else None

        # a symbol whose bar Tradier does not serve yet is retried inside this pass (+3/+6/+10/+15/+25/+40 s), and each symbol's
        # row is computed as soon as its bar is in, so one slow symbol never holds the others back
        futures, last_why, fetch_s = [], {}, 0.0
        with ProcessPoolExecutor(max_workers=self.workers) as cpu, ThreadPoolExecutor(max_workers=8) as net:
            for symbol in [s for s in wanted if s not in need_fetch]:
                task = task_for(symbol, {})
                if task:
                    futures.append(cpu.submit(_add_one, task))
            pending = set(need_fetch)
            for wait_s in (0, 3, 6, 10, 15, 25, 40):
                if not pending:
                    break
                if wait_s and not self.test:
                    time.sleep(max(0.0, t0 + wait_s - time.time()))
                for symbol, (bars, why) in net.map(fetch, sorted(pending)):
                    if bars is None:
                        last_why[symbol] = f"fetch: {why}"
                        continue
                    if not all(epoch in bars for epoch in need_fetch[symbol]):
                        last_why[symbol] = "Tradier returned no closed bar yet"
                        if not any(epoch in bars for epoch in need_fetch[symbol]):
                            continue
                    for epoch in bars:
                        latency.setdefault(str(epoch), {})[symbol] = round(time.time() - (epoch + 900), 1)
                    pending.discard(symbol)
                    task = task_for(symbol, bars)
                    if task:
                        futures.append(cpu.submit(_add_one, task))
            fetch_s = round(time.time() - t0, 1)
            for symbol in pending:
                for epoch in need_fetch[symbol]:
                    skipped.setdefault(symbol, {})[str(epoch)] = last_why.get(symbol, "not fetched")
            results = [r for f in futures for r in f.result()]
        tasks = futures  # one per symbol with rows to append this pass
        for symbol, epoch, status in results:
            if status == "appended":
                led["added"].setdefault(symbol, []).append(int(epoch))
                skipped.get(symbol, {}).pop(str(epoch), None)
            else:
                skipped.setdefault(symbol, {})[str(epoch)] = status
        led["skipped"] = {k: v for k, v in skipped.items() if v}
        appended = sum(1 for r in results if r[2] == "appended")
        landed = sum(len(need_fetch[s]) for s in need_fetch if s not in pending)
        led["events"].append({"at": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "add", "appended": appended, "klines_fetched": landed,
                              "fetched_symbols": len(need_fetch), "fetch_s": fetch_s, "total_s": round(time.time() - t0, 1)})
        atomic_json(self.ledger_path(self.day), led)
        per_bar = {iso(e)[:16] + "Z": sum(1 for v in led["added"].values() if e in v) for e in epochs}
        return {"appended": appended, "klines_fetched": landed, "tasks": len(tasks), "failed": [r for r in results if r[2] != "appended"][:20],
                "rows_per_bar": per_bar, "symbols": len(self.universe()), "fetch_s": fetch_s, "total_s": round(time.time() - t0, 1)}

    def erase(self, day=None):
        day = day or self.day
        led = self.ledger(day)
        epochs = [int(e) for e in led.get("epochs") or temp_epochs(day)]
        if not epochs:
            return {"skip": f"{day} has no temp bars"}
        names = sorted(set(self.universe()) | set(led.get("added", {})))
        tasks = [(str(self.ind), s, epochs) for s in names if (self.ind / f"{s}.npz").exists()]
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=min(self.workers, max(1, len(tasks)))) as pool:
            results = dict(pool.map(_erase_one, tasks))
        errors = {s: r for s, r in results.items() if r.get("error")}
        removed = {s: r for s, r in results.items() if r["sidecar"] or r["zip"]}
        verified = not errors
        stamp = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        led.setdefault("events", []).append({"at": stamp, "kind": "erase", "symbols": len(tasks), "removed_symbols": len(removed),
                                              "rows_zip": sum(r["zip"] for r in results.values()), "rows_sidecar": sum(r["sidecar"] for r in results.values()),
                                              "errors": errors, "erase_s": round(time.time() - t0, 1)})
        if verified:
            led["erased_verified_utc"] = stamp
        atomic_json(self.ledger_path(day), led)
        if verified and not self.pending_days(exclude=day):
            try:
                (self.out / "ACTIVE").unlink()
            except FileNotFoundError:
                pass
        return {"verified": verified, "symbols": len(tasks), "removed_symbols": len(removed), "rows_zip": sum(r["zip"] for r in results.values()),
                "rows_sidecar": sum(r["sidecar"] for r in results.values()), "errors": errors, "erase_s": round(time.time() - t0, 1)}

    def pending_days(self, exclude=None):
        out = []
        for path in sorted(self.out.glob("20??????.json")):
            led = read_json(path, {}) or {}
            day = dt.date.fromisoformat(led.get("day") or f"{path.stem[:4]}-{path.stem[4:6]}-{path.stem[6:]}")
            if day != exclude and led.get("added") and not led.get("erased_verified_utc"):
                out.append(day)
        return out

    def tick(self):
        report = {}
        for day in self.pending_days(exclude=self.day):
            report[f"erase_{day}"] = self.erase(day)
        opened = session_open(self.day)
        if opened is None:
            return dict(report, skip=f"{self.day} has no session")
        if self.now < opened - 1800 + SETTLE_S:
            return dict(report, skip="before the first premarket bar closes")
        if self.now < opened + ERASE_AFTER_OPEN_S:
            # cron starts this at hh:mm:00; a bar closing within the next 20 s is waited for, so the pass runs at close + 5 s
            soon = [e + 900 + SETTLE_S for e in temp_epochs(self.day) if 0 < e + 900 + SETTLE_S - self.now <= 20]
            if soon and not self.test:
                time.sleep(min(soon) - self.now)
                self.now = time.time()
            return dict(report, add=self.add())
        led = read_json(self.ledger_path(self.day), {}) or {}
        if led.get("added") and not led.get("erased_verified_utc"):
            return dict(report, erase=self.erase())
        if (self.out / "ACTIVE").exists() and not self.pending_days():
            (self.out / "ACTIVE").unlink()
        return dict(report, skip="after open + 600 s, nothing to erase")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("tick", "add", "erase"))
    ap.add_argument("--base", default=str(ROOT), help="host root: klines_cache/tradier is read AND written here")
    ap.add_argument("--live-root", default=str(Path.home() / "binance"))
    ap.add_argument("--ind-dir", default="", help="NPZ store (default <base>/backtest_v8/indicators); a scratch copy for tests")
    ap.add_argument("--out-dir", default="", help="ledger dir (default <base>/data/npz_premarket_temp)")
    ap.add_argument("--day", default="", help="session date YYYY-MM-DD, UTC (default: today UTC)")
    ap.add_argument("--now", default="", help="UTC epoch override for tests")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--no-klines", action="store_true", help="never fetch or write klines (tests on bars already in the file)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    a = ap.parse_args()
    run = Run(a)
    result = getattr(run, a.cmd)()
    log(f"{a.cmd} day={run.day} {json.dumps(result, default=str)[:4000]}")
    return 0 if not (isinstance(result, dict) and (result.get("errors") or (result.get("erase") or {}).get("errors"))) else 2


if __name__ == "__main__":
    raise SystemExit(main())
