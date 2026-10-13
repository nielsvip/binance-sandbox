#!/usr/bin/env python3
"""klines_multi_restore — USER 2026-10-13 "maybe all the missing klines are still stored somewhere??"
Grow-only union of S1's own klines files with deeper copies found anywhere (vault/.guard_snap/backups/s2/ext
drives staged as .json/.json.gz, and NPZ 15m OHLCV). Existing S1 rows ALWAYS win; sources only fill timestamps
the file lacks (sources earlier in the plan win among themselves). Every source must pass a consistency gate
(overlap closes agree, timestamp grid matches the target, junction price continuity) or it is rejected whole.
Writes ONLY via klines_guard.guarded_write; original gzipped to backups/ first; refuses below a free-disk floor.
S1 only. Dry-run by default.

    python3 tools/klines_multi_restore.py --plan plan.json [--apply] [--min-free-gb 2.0]
plan.json = [{"target": "tradier/CLS_15m.json", "tf": "15m", "sources": [{"kind": "json"|"npz"|"resample", "path": "..."}]}]  (resample = crypto-only exact aggregation of a finer file, complete buckets only)
"""
import argparse
import datetime as dt
import gzip
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
KC = ROOT / "klines_cache"
PERIOD = {"15m": 900, "1h": 3600, "4h": 14400, "D": 86400}
SCHEMA = ("open", "high", "low", "close", "volume")


def _key(s):
    return str(s)[:19]


def _epoch(k):
    try:
        return dt.datetime.fromisoformat(k[:19]).replace(tzinfo=dt.timezone.utc).timestamp()
    except Exception:
        return None


def _load_json(p):
    raw = Path(p).read_bytes()
    if str(p).endswith(".gz"):
        raw = gzip.decompress(raw)
    rows = json.loads(raw)
    return rows if isinstance(rows, list) else []


def _rows_from_npz(p):
    import numpy as np

    z = np.load(p, allow_pickle=False)
    need = ("timestamps", "open_15m", "high_15m", "low_15m", "close_15m", "volume_15m")
    if any(k not in z.files for k in need):
        return []
    t = z["timestamps"].astype("float64")
    t = t / 1000.0 if t[-1] > 1e11 else t
    if len(t) < 100 or float(np.median(np.diff(t[-500:]))) != 900.0:
        return []
    o, h, l, c, v = (z[k] for k in need[1:])
    out = []
    for i in range(len(t)):
        vals = (float(o[i]), float(h[i]), float(l[i]), float(c[i]), float(v[i]))
        if not all(np.isfinite(vals)) or vals[3] <= 0:
            continue
        out.append({"timestamp": dt.datetime.fromtimestamp(float(t[i]), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3], "volume": vals[4]})
    return out


def _rows_resampled(p, tf):
    """crypto only: exact UTC-aligned aggregation of a finer authentic file (3m/5m/1m/15m); a bucket is emitted only when
    EVERY sub-bar is present (no partial/forming/gap bars)."""
    rows = _source_rows("json", p, tf)
    if not rows:
        return []
    ks = sorted(rows)
    ep = [_epoch(k) for k in ks]
    import statistics

    sub = int(statistics.median([b - a for a, b in zip(ep[-200:], ep[-199:])]))
    per = PERIOD[tf]
    if sub <= 0 or per % sub or sub >= per:
        return []
    need = per // sub
    buckets = {}
    for k, e in zip(ks, ep):
        if e is None or int(e) % sub:
            continue
        buckets.setdefault(int(e) // per * per, []).append((e, rows[k]))
    out = []
    for b, items in buckets.items():
        if len(items) != need:
            continue
        items.sort(key=lambda x: x[0])
        out.append({"timestamp": dt.datetime.fromtimestamp(b, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "open": items[0][1]["open"], "high": max(v["high"] for _, v in items), "low": min(v["low"] for _, v in items), "close": items[-1][1]["close"], "volume": sum(v["volume"] for _, v in items)})
    return out


def _clean(rows):
    out = {}
    for r in rows:
        if not isinstance(r, dict) or not r.get("timestamp"):
            continue
        try:
            vals = [float(r[k]) for k in SCHEMA]
        except Exception:
            continue
        if not (vals[3] > 0 and vals[1] >= vals[2] > 0):
            continue
        out[_key(r["timestamp"])] = dict(zip(SCHEMA, vals))
    return out


def _final_bar_key(day, tf, venue):
    """key of the bar whose close is the day's close: crypto UTC day, stocks 15m = 15:45 ET bar."""
    if venue == "crypto":
        sec = 86400 - PERIOD[tf]
        return f"{day}T{sec // 3600:02d}:{sec % 3600 // 60:02d}:00"
    return None


def _day_proof(src_new, tf, venue, dref):
    """compare the source's day-closing bars with S1's own deep D file; (n, good)."""
    if not dref or tf == "D":
        return (0, 0)
    n = good = 0
    if venue == "crypto":
        for day, dclose in dref.items():
            k = _final_bar_key(day, tf, venue)
            v = src_new.get(k)
            if v is None:
                continue
            n += 1
            good += abs(v["close"] / dclose - 1.0) <= 0.01
        return (n, good)
    if tf != "15m":
        return (0, 0)
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    for k, v in src_new.items():
        e = _epoch(k)
        if e is None:
            continue
        d = dt.datetime.fromtimestamp(e, et)
        if d.hour == 15 and d.minute == 45 and d.strftime("%Y-%m-%d") in dref:
            n += 1
            good += abs(v["close"] / dref[d.strftime("%Y-%m-%d")] - 1.0) <= 0.01
    return (n, good)


def _gate(src, have, tf, venue, dref=None):
    """Return (ok, why). src/have: key->vals. Proof order: overlap closes >=20 bars; else day-close match vs S1's own D
    file >=20 days; else adjacency (gap <=2 bars) with a bounded junction jump. Anything else is rejected whole."""
    if not src:
        return (False, "empty")
    per = PERIOD[tf]
    res_have = {}
    for k in list(have)[-3000:]:
        e = _epoch(k)
        if e is not None:
            r = int(e) % per
            res_have[r] = res_have.get(r, 0) + 1
    common = {r for r, n in res_have.items() if n >= max(2, 0.01 * sum(res_have.values()))}
    new_keys = [k for k in src if k not in have]
    if not new_keys:
        return (False, "adds nothing")
    on_grid = sum(1 for k in new_keys if _epoch(k) is not None and int(_epoch(k)) % per in common)
    if on_grid < 0.95 * len(new_keys):
        return (False, f"grid mismatch {on_grid}/{len(new_keys)} on target residues {sorted(common)[:4]}")
    ov = [k for k in src if k in have]
    if len(ov) >= 20:
        good = sum(1 for k in ov if abs(src[k]["close"] - have[k]["close"]) <= 0.005 * have[k]["close"])
        if good < 0.9 * len(ov):
            return (False, f"overlap disagrees {good}/{len(ov)} within 0.5%")
        return (True, f"ok overlap {good}/{len(ov)} adds {len(new_keys)}")
    n, good = _day_proof({k: src[k] for k in new_keys}, tf, venue, dref)
    if n >= 20:
        if good < 0.9 * n:
            return (False, f"day-close proof failed {good}/{n} within 1% of S1 D file")
        return (True, f"ok day-close proof {good}/{n} adds {len(new_keys)}")
    first_have = min(have)
    older = [k for k in src if k < first_have]
    if not older:
        return (False, f"no overlap/day proof (overlap {len(ov)}, day {n})")
    last_old = max(older)
    gap = (_epoch(first_have) - _epoch(last_old)) / per
    jump = abs(have[first_have]["open"] / src[last_old]["close"] - 1.0)
    lim = 0.35 if venue == "crypto" else 0.45
    if gap > 2 or jump > lim:
        return (False, f"no proof: overlap {len(ov)}, day {n}, junction gap {gap:.0f} bars jump {jump:.0%}")
    return (True, f"ok adjacent junction jump {jump:.1%} adds {len(new_keys)}")


def _dayref(tgt, venue):
    """S1's own sibling D file -> {day: close} (crypto UTC date, stocks ET date; last row per date)."""
    p = tgt.parent / (tgt.name.rsplit("_", 1)[0] + "_D.json")
    try:
        rows = _clean(_load_json(p))
    except Exception:
        return {}
    out = {}
    if venue == "crypto":
        for k, v in rows.items():
            out[k[:10]] = v["close"]
        return out
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    for k in sorted(rows):
        e = _epoch(k)
        if e is not None:
            out[dt.datetime.fromtimestamp(e, et).strftime("%Y-%m-%d")] = rows[k]["close"]
    return out


def _salvage(p):
    """rows from a truncated/corrupt JSON array: every complete {...} object that parses."""
    import re

    raw = Path(p).read_bytes()
    out = []
    for m in re.finditer(rb"\{[^{}]*\}", raw):
        try:
            r = json.loads(m.group(0))
        except Exception:
            continue
        if isinstance(r, dict) and r.get("timestamp"):
            out.append(r)
    return out


def _in_session(k):
    """stock 15m convention of S1's tradier cache = tradier_indicators.filter_strict_market_hours(is_fast_tf=True): 08:30-16:00 ET."""
    from zoneinfo import ZoneInfo

    e = _epoch(k)
    if e is None:
        return False
    t = dt.datetime.fromtimestamp(e, ZoneInfo("America/New_York")).time()
    return dt.time(8, 30) <= t <= dt.time(16, 0) and dt.datetime.fromtimestamp(e, ZoneInfo("America/New_York")).weekday() < 5


def _fmt(target_rows):
    for r in target_rows[:5]:
        ts = r.get("timestamp", "") if isinstance(r, dict) else ""
        if len(ts) > 19:
            return ts[19:]
    return ".000000Z"


def _span(keys):
    a, b = _epoch(min(keys)), _epoch(max(keys))
    return (b - a) / 86400 if a and b else 0.0


_CACHE = {}


def _source_rows(kind, path, tf):
    """parsed+cleaned source rows, memoised per (kind, path, tf) for consecutive targets of one symbol."""
    key = (kind, path, tf if kind == "resample" else "")
    if key not in _CACHE:
        if len(_CACHE) > 24:
            _CACHE.clear()
        rows = _rows_from_npz(path) if kind == "npz" else (_rows_resampled(path, tf) if kind == "resample" else _load_json(path))
        _CACHE[key] = _clean(rows)
    return _CACHE[key]


def restore(item, apply, min_free_gb, bk_dir):
    from klines_guard import guarded_write

    tgt = KC / item["target"]
    tf = item["tf"]
    venue = "crypto" if Path(item["target"]).name.split("_")[0].upper().endswith(("USDT", "USDC")) else "stocks"
    res = {"target": item["target"]}
    if not tgt.exists():
        return {**res, "action": "skip", "why": "target missing"}
    corrupt = False
    try:
        cur = _load_json(tgt)
    except Exception:
        cur = _salvage(tgt)
        corrupt = True
        res["corrupt_salvaged_rows"] = len(cur)
    have = _clean(cur)
    if not have:
        return {**res, "action": "skip", "why": "target unparseable/empty"}
    dref = _dayref(tgt, venue)
    res.update(rows_before=len(cur), first_before=min(have), span_before=round(_span(have.keys()), 1))
    add = {}
    used = []
    for s in item["sources"]:
        try:
            if s["kind"] == "resample" and venue != "crypto":
                raise ValueError("resample is crypto-only (stock HTF bins are ET-session based)")
            src = dict(_source_rows(s["kind"], s["path"], tf))
        except Exception as e:
            used.append({"path": s["path"], "ok": False, "why": f"unreadable {type(e).__name__}"})
            continue
        if venue == "stocks" and tf == "15m":
            src = {k: v for k, v in src.items() if _in_session(k)}
        base = dict(have)
        base.update(add)
        ok, why = _gate(src, have if len(have) >= 50 else base, tf, venue, dref)
        n = 0
        if ok:
            for k, v in src.items():
                if k not in have and k not in add:
                    add[k] = v
                    n += 1
        used.append({"path": s["path"], "ok": ok, "why": why, "added": n})
    res["sources"] = used
    if not add and not corrupt:
        return {**res, "action": "no_gain"}
    allk = set(have) | set(add)
    if corrupt:
        res["corrupt"] = True
    res.update(added=len(add), first_after=min(allk), span_after=round(_span(allk), 1))
    if not apply:
        return {**res, "action": "would_restore"}
    free = shutil.disk_usage(str(KC)).free / 1e9
    if free < min_free_gb:
        return {**res, "action": "refused_disk", "why": f"free {free:.1f}GB < {min_free_gb}"}
    b = bk_dir / (item["target"] + ".gz")
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_bytes(gzip.compress(tgt.read_bytes()))
    suf = _fmt(cur)
    try:
        cur = _load_json(tgt)
    except Exception:
        cur = _salvage(tgt)
    have_now = {_key(r.get("timestamp")) for r in cur if isinstance(r, dict) and r.get("timestamp")}
    extra = [{"timestamp": k + suf, **v} for k, v in add.items() if k not in have_now]
    merged = sorted(list(cur) + extra, key=lambda r: _key(r.get("timestamp", "")) if isinstance(r, dict) else "")
    ok, why = guarded_write(tgt, merged)
    chk = _load_json(tgt) if ok else []
    res.update(action="restored" if ok and len(chk) >= len(merged) else f"guard_refused {why}"[:160], rows_after=len(chk) if ok else len(cur), backup=str(b))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-free-gb", type=float, default=2.0)
    ap.add_argument("--only", default=None, help="comma list of targets to run")
    a = ap.parse_args()
    if platform.system() == "Darwin":
        sys.exit("S1 only: the Mac keeps <=1800 klines by design")
    plan = json.loads(Path(a.plan).read_text())
    if a.only:
        keep = set(a.only.split(","))
        plan = [p for p in plan if p["target"] in keep or Path(p["target"]).stem.rsplit("_", 1)[0] in keep]
    bk = ROOT / "backups" / f"before_klines_multi_restore_{time.strftime('%Y%m%d%H%M', time.gmtime())}"
    tally = {}
    for item in plan:
        try:
            r = restore(item, a.apply, a.min_free_gb, bk)
        except Exception as e:
            r = {"target": item.get("target"), "action": "error", "why": f"{type(e).__name__}: {e}"[:160]}
        k = r["action"].split(" ")[0]
        tally[k] = tally.get(k, 0) + 1
        print(json.dumps(r), flush=True)
        if k == "refused_disk":
            break
    print(json.dumps({"summary": tally, "apply": a.apply, "backup_dir": str(bk)}), flush=True)


if __name__ == "__main__":
    main()
