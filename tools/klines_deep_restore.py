#!/usr/bin/env python3
"""USER 2026-10-13: S1 keeps ALL klines (>400D); nothing may shrink S1's own klines_cache/{SYM}_15m.json.
Restores the deep 15m history of S1's own crypto klines from S1's own NPZs (open/high/low/close/volume_15m,
timestamps — the exact bars the builder computed from), UNIONED with the current file: existing rows always win,
NPZ rows only fill timestamps the file lacks. Writes only via klines_guard.guarded_write (grow-only).
S1 only (refuses on macOS). Dry-run by default.

    python3 tools/klines_deep_restore.py [--symbols A,B] [--min-days 60] [--apply]
"""
import argparse
import datetime as dt
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
KC = ROOT / "klines_cache"
IND = ROOT / "backtest_v8" / "indicators"


def _iso(t):
    return dt.datetime.fromtimestamp(float(t), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def restore_one(sym, apply, min_gain_days):
    import numpy as np
    from klines_guard import guarded_write

    p = KC / f"{sym}_15m.json"
    z = np.load(IND / f"{sym}.npz", allow_pickle=True)
    need = ("timestamps", "open_15m", "high_15m", "low_15m", "close_15m", "volume_15m")
    if any(k not in z.files for k in need):
        return {"sym": sym, "action": "skip", "why": "npz lacks 15m ohlcv"}
    t = z["timestamps"].astype("float64")
    t = t / 1000.0 if t[-1] > 1e11 else t
    if len(t) < 100 or float(np.median(np.diff(t[-500:]))) != 900.0:
        return {"sym": sym, "action": "skip", "why": "npz base is not 15m"}
    try:
        old = json.loads(p.read_text()) if p.exists() else []
    except Exception as e:
        return {"sym": sym, "action": "skip", "why": f"unreadable cache {e}"[:80]}
    have = {r.get("timestamp") for r in old if isinstance(r, dict)}
    o, h, l, c, v = (z[k] for k in need[1:])
    add = []
    for i in range(len(t)):
        ts = _iso(t[i])
        if ts in have:
            continue
        vals = (float(o[i]), float(h[i]), float(l[i]), float(c[i]), float(v[i]))
        if not all(np.isfinite(vals)) or vals[3] <= 0:
            continue
        add.append({"timestamp": ts, "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3], "volume": vals[4]})
    merged = sorted(list(old) + add, key=lambda r: r.get("timestamp", ""))
    span_old = (len(old) / 96.0)
    span_new = (len(merged) / 96.0)
    res = {"sym": sym, "rows_before": len(old), "rows_after": len(merged), "added": len(add), "days_before": round(span_old), "days_after": round(span_new)}
    if span_new - span_old < min_gain_days:
        res["action"] = "already_deep"
        return res
    if not apply:
        res["action"] = "would_restore"
        return res
    ok, why = guarded_write(p, merged)
    res["action"] = "restored" if ok else f"guard_refused {why}"[:120]
    return res


def restore_from_json(src, apply, min_gain_days):
    """Union a deep klines JSON copy (e.g. staged from s2) into S1's own file of the same name: existing rows win, grow-only write."""
    from klines_guard import guarded_write

    p = KC / src.name
    try:
        new = json.loads(src.read_text())
        old = json.loads(p.read_text()) if p.exists() else []
    except Exception as e:
        return {"file": src.name, "action": "skip", "why": f"unreadable {e}"[:80]}
    have = {r.get("timestamp") for r in old if isinstance(r, dict)}
    add = [r for r in new if isinstance(r, dict) and r.get("timestamp") and r.get("timestamp") not in have]
    merged = sorted(list(old) + add, key=lambda r: r.get("timestamp", ""))
    from klines_guard import span_of

    res = {"file": src.name, "rows_before": len(old), "rows_after": len(merged), "added": len(add), "days_before": round(span_of(old)), "days_after": round(span_of(merged))}
    if span_of(merged) - span_of(old) < min_gain_days:
        res["action"] = "already_deep"
        return res
    if not apply:
        res["action"] = "would_restore"
        return res
    ok, why = guarded_write(p, merged)
    res["action"] = "restored" if ok else f"guard_refused {why}"[:120]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--min-days", type=float, default=30.0, help="only rewrite when the union adds at least this many days")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--npz-dir", default=None, help="read NPZs from this staging dir (e.g. copied from a worker) instead of S1's indicators")
    ap.add_argument("--source-dir", default=None, help="union deep klines JSONs from this staging dir instead of NPZs")
    a = ap.parse_args()
    if platform.system() == "Darwin":
        sys.exit("S1 only: the Mac keeps <=1800 klines by design")
    global IND
    if a.npz_dir:
        IND = Path(a.npz_dir).expanduser()
    if a.source_dir:
        tally = {}
        for src in sorted(Path(a.source_dir).glob("*.json")):
            try:
                r = restore_from_json(src, a.apply, a.min_days)
            except Exception as e:
                r = {"file": src.name, "action": "error", "why": f"{type(e).__name__}: {e}"[:120]}
            tally[r["action"].split(" ")[0]] = tally.get(r["action"].split(" ")[0], 0) + 1
            print(json.dumps(r), flush=True)
        print(json.dumps({"summary": tally, "apply": a.apply}), flush=True)
        return
    syms = a.symbols.split(",") if a.symbols else sorted(p.stem for p in IND.glob("*.npz") if p.stem.endswith(("USDT", "USDC")))
    tally = {}
    for s in syms:
        try:
            r = restore_one(s, a.apply, a.min_days)
        except Exception as e:
            r = {"sym": s, "action": "error", "why": f"{type(e).__name__}: {e}"[:120]}
        tally[r["action"].split(" ")[0]] = tally.get(r["action"].split(" ")[0], 0) + 1
        print(json.dumps(r), flush=True)
    print(json.dumps({"summary": tally, "apply": a.apply}), flush=True)


if __name__ == "__main__":
    main()
