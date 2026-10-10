#!/usr/bin/env python3
"""Decode a legacy (leaky-marker) crypto NPZ back into klines_cache_gateway bars.

The legacy NPZ holds ~1yr of 15m OHLCV + HTF OHLCV columns + timestamp_* parent
keys. This script converts those rows into {SYM}_{15m,1h,4h,D,W,M}.json gateway
bars so the sanctioned causal precompute (backtest_v8_precompute, mode crypto)
can rebuild a full-history causal_v3 NPZ with ZERO downloads.

Safety: existing gateway bars are NEVER overwritten (merge adds decoded-only
timestamps, after backups/). Every decoded TF is verified bar-for-bar against
authentic overlap before merge; mismatch aborts that TF loudly.

2026-10-10 FINDING: legacy NPZ 15m/HTF OHLCV does NOT match authentic futures
klines (ENAUSDC: prices ~0.05% off, volume ~30x off — wrong venue/instrument
at build time). Decode of 15m/1h/4h/D from legacy is UNSOUND — use only
--only W,M (resample from authentic D) + fapi micro-backfill for the rest.

usage: npz_legacy_decode.py SYM [SYM...] [--write] [--ind-dir D] [--gateway-dir D]
  (no --write = verify-only report, no files touched)
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def iso(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load_bars(path):
    if not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path))
    except Exception:
        return {}
    out = {}
    for b in data:
        try:
            out[b["timestamp"]] = (float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), float(b["volume"]))
        except Exception:
            continue
    return out


def maxreldiff(a, b):
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    den = np.maximum(np.abs(b), 1e-12)
    return float(np.max(np.abs(a - b) / den)) if len(a) else 0.0


def decode_htf(z, tf):
    """One authentic parent bar per timestamp_{tf} group (last-row-wins OHLC + volume rule TBD by caller)."""
    ts = np.asarray(z["timestamps"]).astype("float64")
    pts = np.asarray(z[f"timestamp_{tf}"]).astype("float64")
    o = np.asarray(z[f"open_{tf}"]).astype("float64")
    h = np.asarray(z[f"high_{tf}"]).astype("float64")
    lo = np.asarray(z[f"low_{tf}"]).astype("float64")
    c = np.asarray(z[f"close_{tf}"]).astype("float64")
    v = np.asarray(z[f"volume_{tf}"]).astype("float64")
    bars = {}
    for p in np.unique(pts[(pts > 0) & np.isfinite(pts)]):
        idx = np.where(pts == p)[0]
        if len(idx) == 0:
            continue
        bars[int(p)] = {
            "open": float(o[idx[0]]),
            "high": float(np.max(h[idx])),
            "low": float(np.min(lo[idx])),
            "close": float(c[idx[-1]]),
            "v_last": float(v[idx[-1]]),
            "v_sum": float(np.sum(v[idx])),
            "n": len(idx),
        }
    return bars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--only", default="", help="comma TF subset, e.g. W,M")
    ap.add_argument("--ind-dir", default=os.path.join(ROOT, "backtest_v8", "indicators"))
    ap.add_argument("--gateway-dir", default=os.path.join(ROOT, "klines_cache_gateway"))
    a = ap.parse_args()
    only = {t.strip() for t in a.only.split(",") if t.strip()} if a.only else set()
    code = 0
    for sym in a.symbols:
        npz_path = os.path.join(a.ind_dir, sym + ".npz")
        print(f"=== {sym} ===", flush=True)
        if not os.path.exists(npz_path):
            print("  NO_NPZ nothing to decode"); code = 1; continue
        z = np.load(npz_path, allow_pickle=True)
        ts = np.asarray(z["timestamps"]).astype("float64")
        step = float(np.median(np.diff(ts[np.argsort(ts)])))
        print(f"  rows={len(ts)} span={iso(ts[0])[:10]}>{iso(ts[-1])[:10]} grid_step={step:.0f}s")
        if abs(step - 900.0) > 1.0:
            print(f"  ABORT: non-15m grid ({step:.0f}s) — needs explicit handling"); code = 1; continue
        o15 = np.asarray(z["open_15m"]).astype("float64")
        h15 = np.asarray(z["high_15m"]).astype("float64")
        l15 = np.asarray(z["low_15m"]).astype("float64")
        c15 = np.asarray(z["close_15m"]).astype("float64")
        v15 = np.asarray(z["volume_15m"]).astype("float64")
        tf_dec = {}
        if not only or "15m" in only:
            dec15 = {iso(t): (o15[i], h15[i], l15[i], c15[i], v15[i]) for i, t in enumerate(ts)}
            exist15 = load_bars(os.path.join(a.gateway_dir, f"{sym}_15m.json"))
            shared = sorted(set(dec15) & set(exist15))
            if shared:
                d = max(maxreldiff([dec15[k][j] for k in shared], [exist15[k][j] for k in shared]) for j in range(5))
                print(f"  15m: overlap={len(shared)} maxreldiff={d:.2e} {'OK' if d < 1e-6 else 'MISMATCH-ABORT-TF'}")
                ok15 = d < 1e-6
            else:
                print("  15m: no overlap (unverifiable, decode-only)"); ok15 = True
            tf_dec["15m"] = (dec15, ok15, len(exist15))
        for tf in ["1h", "4h", "D"]:
            if only and tf not in only:
                continue
            need = [f"timestamp_{tf}", f"open_{tf}", f"high_{tf}", f"low_{tf}", f"close_{tf}", f"volume_{tf}"]
            if any(k not in z.files for k in need):
                print(f"  {tf}: legacy lacks HTF keys — will resample from 15m at build"); continue
            raw = decode_htf(z, tf)
            exist = load_bars(os.path.join(a.gateway_dir, f"{sym}_{tf}.json"))
            # volume rule: last-vs-sum decided on overlap
            shared_p = [p for p in raw if iso(p) in exist]
            rule = "last"
            if shared_p:
                dl = maxreldiff([raw[p]["v_last"] for p in shared_p], [exist[iso(p)][4] for p in shared_p])
                ds = maxreldiff([raw[p]["v_sum"] for p in shared_p], [exist[iso(p)][4] for p in shared_p])
                rule = "last" if dl <= ds else "sum"
                dv = min(dl, ds)
                do = maxreldiff([raw[p]["open"] for p in shared_p], [exist[iso(p)][0] for p in shared_p])
                dh = maxreldiff([raw[p]["high"] for p in shared_p], [exist[iso(p)][1] for p in shared_p])
                dlw = maxreldiff([raw[p]["low"] for p in shared_p], [exist[iso(p)][2] for p in shared_p])
                dc = maxreldiff([raw[p]["close"] for p in shared_p], [exist[iso(p)][3] for p in shared_p])
                vok = dv < 1e-6 and max(do, dh, dlw, dc) < 1e-6
                print(f"  {tf}: groups={len(raw)} overlap={len(shared_p)} vol_rule={rule}(last={dl:.1e} sum={ds:.1e}) ohlc=({do:.1e},{dh:.1e},{dlw:.1e},{dc:.1e}) {'OK' if vok else 'MISMATCH-ABORT-TF'}")
                ok = vok
            else:
                print(f"  {tf}: groups={len(raw)} no overlap (vol_rule=last, unverifiable)"); ok = True
            dec = {iso(p): (r["open"], r["high"], r["low"], r["close"], r["v_last"] if rule == "last" else r["v_sum"]) for p, r in raw.items()}
            tf_dec[tf] = (dec, ok, len(exist))
        # W/M from final D (authentic D preferred)
        d_path = os.path.join(a.gateway_dir, f"{sym}_D.json")
        d_exist = load_bars(d_path)
        d_final = dict(d_exist)
        if "D" in tf_dec and tf_dec["D"][1]:
            for k, v in tf_dec["D"][0].items():
                d_final.setdefault(k, v)
        if len(d_final) >= 30:
            df = pd.DataFrame([{"timestamp": k, "open": v[0], "high": v[1], "low": v[2], "close": v[3], "volume": v[4]} for k, v in d_final.items()])
            df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("ts").sort_index()
            agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            for tf, rule in [("W", "W-MON"), ("M", "MS")]:
                if only and tf not in only:
                    continue
                kw = {"label": "left", "closed": "left"} if tf == "W" else {}
                rw = df.resample(rule, **kw).agg(agg).dropna()
                dec = {t.strftime("%Y-%m-%dT%H:%M:%S.%fZ"): (r["open"], r["high"], r["low"], r["close"], r["volume"]) for t, r in rw.iterrows()}
                exist = load_bars(os.path.join(a.gateway_dir, f"{sym}_{tf}.json"))
                shared_w = sorted(set(dec) & set(exist))
                if shared_w:
                    d = max(maxreldiff([dec[k][j] for k in shared_w], [exist[k][j] for k in shared_w]) for j in range(5))
                    print(f"  {tf}: resampled={len(dec)} overlap={len(shared_w)} maxreldiff={d:.2e} {'OK' if d < 1e-6 else 'MISMATCH-ABORT-TF'}")
                    ok = d < 1e-6
                else:
                    print(f"  {tf}: resampled={len(dec)} no overlap (boundary rule unverified)"); ok = True
                tf_dec[tf] = (dec, ok, len(exist))
        # merge
        for tf, (dec, ok, n_exist) in tf_dec.items():
            if not ok:
                print(f"  {tf}: SKIPPED (verify failed)"); code = 1; continue
            path = os.path.join(a.gateway_dir, f"{sym}_{tf}.json")
            exist = load_bars(path)
            new_keys = sorted(set(dec) - set(exist))
            print(f"  {tf}: existing={n_exist} decoded={len(dec)} new={len(new_keys)}")
            if a.write and new_keys:
                if os.path.exists(path):
                    bk = os.path.join(ROOT, "backups", f"gw_{sym}_{tf}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}.json")
                    shutil.copy2(path, bk)
                merged = dict(exist)
                for k in new_keys:
                    merged[k] = dec[k]
                rows = [{"timestamp": k, "open": v[0], "high": v[1], "low": v[1], "low": v[2], "close": v[3], "volume": v[4]} for k, v in sorted(merged.items())]
                tmp = path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(rows, f)
                os.replace(tmp, path)
                print(f"  {tf}: WROTE {len(rows)} bars (+{len(new_keys)})")
    return code


if __name__ == "__main__":
    sys.exit(main())
