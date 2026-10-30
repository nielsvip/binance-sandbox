#!/usr/bin/env python3
"""npz_premarket_temp_bars — TEMPORARY 08:45 / 09:00 / 09:15 ET rows in the LIVE stock NPZ store, erased before 09:45 ET.

USER 2026-10-29: "klines exist from pre-market 8:45 ET; 3 temporary NPZ bars are added for 8:45, 9:00 and 9:15 ... both 7D trc
and 30D trb trades start at 9:30 ... the 3 temp NPZ bars must be erased before the 9:45 vec trader decisions". USER 2026-10-30:
"DO THAT NOW".

ADD    (09:00:05 .. 09:40 ET, cron every minute on S5): for every tradeable stock symbol, each wanted bar (bar-open label) that has
       CLOSED, whose Tradier 15m kline exists in a file written after that close, and that is newer than the store tip, gets ONE
       row from the same incremental compute the tail service uses (npz_tail_service._compute_row_process) appended through
       npz_tail_store.append_tail_row (sidecar; the compactor may fold it into the zip). Ledger data/npz_premarket_temp/<day>.json.
ERASE  (from 09:40 ET; any later run repeats it until verified): under the store lock every temp row of that day is removed from
       the sidecar and from the zip; RTH rows are never touched; the result is re-read and verified.
GUARD  data/npz_premarket_temp/ACTIVE exists while temp rows may be in the store; S5's STOCK_PUSHBACK cron skips its rsync to S1
       while it is fresh (a pushed zip would carry the temp rows to S1 and from there to every worker).
OFF    data/npz_premarket_temp/OFF stops new rows (erase still runs).

What the rows do: the engine masks every bar labelled before 09:30 ET (no entry, no exit; v12_quick_engine rth_mask), so they
create no premarket trade. They make the 09:30 state current (npz_tail = 09:15 bar, inside the trader's 30-minute freshness
window), so tra/trb/trc plan at the 09:30 open instead of 09:45, with the premarket bars as indicator context. No full NPZ
calculation; every row is computed once.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
NY = ZoneInfo("America/New_York")
OFFSETS = (2700, 1800, 900)  # bar-open labels 08:45, 09:00, 09:15 ET before a 09:30 open
ERASE_AFTER_OPEN_S = 600  # 09:40 ET: erased with five minutes to spare before the 09:30 bar closes
SETTLE_S = 5
WORKERS = int(os.environ.get("PM_TEMP_WORKERS", "6"))


def log(msg):
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


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


def session_open(day):
    from tools.npz_cutoffs import stock_session_bounds

    bounds = stock_session_bounds(day)
    return int(bounds[0]) if bounds else None


def temp_epochs(day):
    opened = session_open(day)
    return [opened - offset for offset in OFFSETS] if opened else []


def iso(epoch):
    return dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


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


def tradier_key(base):
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
    for name in (os.environ.get("PM_TEMP_TRADIER_KEY", ""), "TRADIER_API_KEY_TRB", "TRADIER_API_KEY_TRA", "TRADIER_API_KEY"):
        if name and (os.environ.get(name) or env.get(name)):
            return os.environ.get(name) or env.get(name)
    return None


def fetch_premarket(key, symbol, day, epochs, now):
    """Today's premarket 15min bars from Tradier timesales (session_filter=all), only those of `epochs` that have closed.
    The Tradier klines on the hosts carry today's premarket bars only from the next day (klines_canonical stocks-fetch fetches
    completed sessions), so the 08:45-09:15 bars are fetched here at their close. {epoch: kline row} or (None, why)."""
    import requests

    params = {"symbol": symbol, "interval": "15min", "start": f"{day.isoformat()} 08:45", "end": f"{day.isoformat()} 09:30", "session_filter": "all"}
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
                out[stamp] = {"timestamp": dt.datetime.fromtimestamp(stamp, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                              "open": float(d["open"]), "high": float(d["high"]), "low": float(d["low"]), "close": float(d["close"]),
                              "volume": float(d.get("volume") or 0), "_closed": True, "_provisional": False,
                              "_source": "tradier_timesales_15min_premarket_temp"}
        return out, ""
    return None, "HTTP 429 three times"


def private_klines(base, private, symbol, bars):
    """A kline view for this symbol only: the host's own files for 5m/1h/4h/D (symlinks, never written) and a private 15m
    copy that adds today's fetched premarket bars. The shared history stays RTH-only, so the 09:30 bar's permanent row
    (computed at 09:45 by the tail service) sees exactly the inputs it would have seen without this tool."""
    from npz_live_generator import _resolve_kline_path

    folder = Path(private) / "klines_cache" / "tradier"
    folder.mkdir(parents=True, exist_ok=True)
    for tf in ("5m", "1h", "4h", "D"):
        real = _resolve_kline_path(symbol, tf, "tradier", Path(base))
        link = folder / f"{symbol}_{tf}.json"
        if real is not None and not link.exists():
            link.symlink_to(real)
    real = _resolve_kline_path(symbol, "15m", "tradier", Path(base))
    if real is None:
        raise FileNotFoundError(f"{symbol}: no Tradier 15m kline file")
    doc = json.loads(real.read_text())
    rows = doc if isinstance(doc, list) else doc.get("klines", [])
    first = min(bars)
    from closed_bar_feed import timestamp_epoch

    rows = [row for row in rows if timestamp_epoch(row["timestamp"]) < first] + [bars[e] for e in sorted(bars)]
    target = folder / f"{symbol}_15m.json"
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows))
    os.replace(tmp, target)
    return Path(private)


def _add_one(task):
    """Append the fetched bars of one symbol in time order; stops at the first failure (a later bar never jumps it)."""
    base, ind, private, symbol, bars, epochs = task
    import npz_tail_service as T
    from npz_tail_store import append_tail_row

    bars = {int(k): v for k, v in bars.items()}
    try:
        kbase = private_klines(base, private, symbol, bars)
    except Exception as exc:
        return [(symbol, min(epochs), f"private klines {type(exc).__name__}: {exc}")]
    out = []
    for epoch in epochs:
        row = T._compute_row_process((symbol, int(epoch), str(kbase), "tradier"))
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
    from npz_tail_store import _store_lock, tail_path_for_npz

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
            from npz_tail_store import tail_rows

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
        self.day = dt.date.fromisoformat(a.day) if a.day else dt.datetime.fromtimestamp(self.now, dt.timezone.utc).astimezone(NY).date()
        self.only = {s.strip().upper() for s in (a.symbols or "").split(",") if s.strip()}
        self.workers = max(1, a.workers)

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
            return {"skip": f"{self.day} is not a NYSE session"}
        if (self.out / "OFF").exists():
            return {"skip": "OFF switch"}
        due = [e for e in epochs if e + 900 + SETTLE_S <= self.now]
        if not due:
            return {"skip": "no wanted bar has closed yet"}
        led = self.ledger(self.day)
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "ACTIVE").write_text(json.dumps({"day": self.day.isoformat(), "at": self.now}) + "\n")
        key = tradier_key(self.base)
        if not key:
            return {"error": "no Tradier market-data key"}
        tasks, skipped, wanted = [], {}, {}
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
        t0 = time.time()
        from concurrent.futures import ThreadPoolExecutor

        def fetch(symbol):
            try:
                return symbol, fetch_premarket(key, symbol, self.day, set(epochs), self.now if self.test else time.time())
            except Exception as exc:
                return symbol, (None, f"{type(exc).__name__}: {str(exc)[:100]}")

        with ThreadPoolExecutor(max_workers=4) as pool:
            fetched = dict(pool.map(fetch, sorted(wanted)))
        fetch_s = round(time.time() - t0, 1)
        bars_dir = self.out / f"{self.day.strftime('%Y%m%d')}_bars"
        for symbol, todo in wanted.items():
            bars, why = fetched.get(symbol) or (None, "not fetched")
            if bars is None:
                for epoch in todo:
                    skipped.setdefault(symbol, {})[str(epoch)] = f"fetch: {why}"
                continue
            ready = {}
            for epoch in todo:
                if epoch in bars:
                    ready[epoch] = bars[epoch]
                else:
                    # rows are append-only: a bar Tradier does not return while a later one exists is given up (absent at the source)
                    skipped.setdefault(symbol, {})[str(epoch)] = "Tradier returned no bar"
            if ready:
                kept = read_json(bars_dir / f"{symbol}.json", {}) or {}
                kept.update({str(k): v for k, v in bars.items()})
                atomic_json(bars_dir / f"{symbol}.json", kept)
                # the private 15m view carries every premarket bar fetched so far (earlier temp rows' bars included)
                view = {int(k): v for k, v in kept.items() if int(k) <= max(ready)}
                tasks.append((str(self.base), str(self.ind), str(self.out / "kl" / symbol), symbol, view, sorted(ready)))
        results = []
        if tasks:
            with ProcessPoolExecutor(max_workers=min(self.workers, len(tasks))) as pool:
                results = [r for rows in pool.map(_add_one, tasks) for r in rows]
        for symbol, epoch, status in results:
            if status == "appended":
                led["added"].setdefault(symbol, []).append(int(epoch))
                skipped.get(symbol, {}).pop(str(epoch), None)
            else:
                skipped.setdefault(symbol, {})[str(epoch)] = status
        led["skipped"] = {k: v for k, v in skipped.items() if v}
        appended = sum(1 for r in results if r[2] == "appended")
        led["events"].append({"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "add", "appended": appended, "tasks": len(tasks), "fetched": len(wanted), "fetch_s": fetch_s, "total_s": round(time.time() - t0, 1)})
        atomic_json(self.ledger_path(self.day), led)
        per_bar = {iso(e): sum(1 for v in led["added"].values() if e in v) for e in epochs}
        return {"appended": appended, "tasks": len(tasks), "failed": [r for r in results if r[2] != "appended"][:20], "rows_per_bar": per_bar, "symbols": len(self.universe()), "fetched": len(wanted), "fetch_s": fetch_s, "total_s": round(time.time() - t0, 1)}

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
        if verified:
            import shutil

            shutil.rmtree(self.out / "kl", ignore_errors=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
            return dict(report, skip=f"{self.day} is not a NYSE session")
        if self.now < opened - 1800 + SETTLE_S:
            return dict(report, skip="before 09:00 ET")
        if self.now < opened + ERASE_AFTER_OPEN_S:
            # cron starts this at hh:mm:00; a wanted bar closing within the next 20 s is waited for, so the pass runs at close + 5 s
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
        return dict(report, skip="after 09:40 ET, nothing to erase")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("tick", "add", "erase"))
    ap.add_argument("--base", default=str(ROOT))
    ap.add_argument("--live-root", default=str(Path.home() / "binance"))
    ap.add_argument("--ind-dir", default="", help="NPZ store (default <base>/backtest_v8/indicators); a scratch copy for tests")
    ap.add_argument("--out-dir", default="", help="ledger dir (default <base>/data/npz_premarket_temp)")
    ap.add_argument("--day", default="", help="NYSE session date YYYY-MM-DD (default today in New York)")
    ap.add_argument("--now", default="", help="epoch override for tests")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--workers", type=int, default=WORKERS)
    a = ap.parse_args()
    run = Run(a)
    result = getattr(run, a.cmd)()
    log(f"{a.cmd} day={run.day} {json.dumps(result, default=str)[:4000]}")
    return 0 if not (isinstance(result, dict) and (result.get("errors") or (result.get("erase") or {}).get("errors"))) else 2


if __name__ == "__main__":
    raise SystemExit(main())
