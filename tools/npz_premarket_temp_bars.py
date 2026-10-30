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
import re
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


def kline_ready(base, symbol, epoch):
    """The completed bar `epoch` is in the Tradier 15m kline file and the file was written after that bar closed."""
    from npz_live_generator import _resolve_kline_path

    path = _resolve_kline_path(symbol, "15m", "tradier", Path(base))
    if path is None:
        return False, "no Tradier 15m kline file"
    if path.stat().st_mtime < epoch + 900:
        return False, f"kline file last written before the {iso(epoch)} bar closed"
    stamp = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:00")
    with open(path, "rb") as stream:
        stream.seek(max(0, path.stat().st_size - 262144))
        tail = stream.read().decode("utf-8", errors="ignore")
    if not re.search(r'"timestamp"\s*:\s*"' + re.escape(stamp), tail):
        return False, f"kline has no {iso(epoch)} bar yet"
    return True, ""


def _add_one(task):
    """Append the ready bars of one symbol in time order; stops at the first failure (a later bar never jumps it)."""
    base, ind, symbol, epochs = task
    import npz_tail_service as T
    from npz_tail_store import append_tail_row

    out = []
    for epoch in epochs:
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
        tasks, skipped = [], {}
        for symbol in self.universe():
            have = {int(e) for e in led["added"].get(symbol, [])}
            tip = T.tip_of(str(self.ind / f"{symbol}.npz"))
            ready = []
            for epoch in due:
                if epoch in have:
                    continue
                if tip is not None and int(tip) >= epoch:
                    skipped.setdefault(symbol, {})[str(epoch)] = f"store tip {iso(tip)} is not before {iso(epoch)}"
                    continue
                ok, why = kline_ready(self.base, symbol, epoch)
                if not ok:
                    # a later bar whose kline file was written after its own close and still lacks this one -> this one is absent at
                    # the source and is given up (rows are append-only); otherwise it is retried next minute
                    skipped.setdefault(symbol, {})[str(epoch)] = why
                    continue
                ready.append(epoch)
            if ready:
                tasks.append((str(self.base), str(self.ind), symbol, ready))
        t0 = time.time()
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
        led["events"].append({"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "add", "appended": appended, "tasks": len(tasks), "compute_s": round(time.time() - t0, 1)})
        atomic_json(self.ledger_path(self.day), led)
        per_bar = {iso(e): sum(1 for v in led["added"].values() if e in v) for e in epochs}
        return {"appended": appended, "tasks": len(tasks), "failed": [r for r in results if r[2] != "appended"][:20], "rows_per_bar": per_bar, "symbols": len(self.universe()), "compute_s": round(time.time() - t0, 1)}

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
