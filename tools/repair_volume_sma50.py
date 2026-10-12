#!/usr/bin/env python3
"""Repair volume_D_50_sma in stocks NPZs: stored rows are previous-day volume (not a 50-sma).
Regenerates TRUE rolling(50, min_periods=1).mean over native D volume (retired builder semantics,
old/backtest_v8_precompute_tradier.py:561), broadcast to 15m rows. Fail-closed per symbol.
Backs up original to <sym>.npz.bak-sma50-<UTC>. Also reports zero-volume sessions + klines coverage.
Usage: repair_volume_sma50.py [--apply] [SYM ...]  (default: audit only, all stocks NPZs with the key)
"""
import json, os, sys, time, datetime
import numpy as np
try:
    import pandas as pd
except ImportError:
    pd = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IND = os.path.join(ROOT, "backtest_v8", "indicators")
KL = os.path.join(ROOT, "klines_cache_backtest", "tradier")
KL2 = os.path.join(ROOT, "klines_cache_backtest")


def native_sessions(td, v):
    chg = np.ones(len(td), bool)
    chg[1:] = td[1:] != td[:-1]
    firsts = np.flatnonzero(chg)
    return firsts, np.cumsum(chg) - 1, v[firsts]


def true_sma50(v, td):
    firsts, gid, nv = native_sessions(td, v)
    sma = pd.Series(nv).rolling(50, min_periods=1).mean().values
    return sma[gid].astype(np.float32), len(nv)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    apply = "--apply" in sys.argv
    add_missing = "--add-missing" in sys.argv
    if pd is None:
        print("FATAL: pandas required")
        return 2
    syms = args or sorted(f[:-4] for f in os.listdir(IND) if f.endswith(".npz") and "_" not in f and f[:-4].isupper())
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    audit = {"utc": stamp, "apply": apply, "syms": {}}
    for sym in syms:
        p = os.path.join(IND, sym + ".npz")
        if not os.path.exists(p):
            continue
        try:
            L = dict(np.load(p, allow_pickle=True))
        except Exception as e:
            audit["syms"][sym] = {"status": "LOAD_FAIL", "err": type(e).__name__}
            continue
        if "volume_D_50_sma" not in L or "volume_D" not in L or "timestamp_D" not in L:
            if add_missing and "volume_D_50_sma" not in L and "volume_D" in L and "timestamp_D" in L:
                try:
                    v = np.asarray(L["volume_D"], dtype=np.float64).reshape(-1)
                    td = np.asarray(L["timestamp_D"]).reshape(-1)
                    rep, nsess = true_sma50(v, td)
                    import shutil
                    bak = p + f".bak-sma50-{stamp}"
                    shutil.copy2(p, bak)
                    L["volume_D_50_sma"] = rep
                    tmp = p + ".tmp-repair.npz"
                    np.savez_compressed(tmp, **{k: np.asarray(v) for k, v in L.items()})
                    os.replace(tmp, p)
                    audit["syms"][sym] = {"status": "ADDED", "sessions": nsess, "backup": os.path.basename(bak)}
                    continue
                except Exception as e:
                    audit["syms"][sym] = {"status": "REPAIR_FAIL", "err": f"{type(e).__name__}:{str(e)[:100]}"}
                    continue
            audit["syms"][sym] = {"status": "NO_KEY"}
            continue
        try:
            v = np.asarray(L["volume_D"], dtype=np.float64).reshape(-1)
            td = np.asarray(L["timestamp_D"]).reshape(-1)
            old = np.asarray(L["volume_D_50_sma"], dtype=np.float64).reshape(-1)
            ts = np.asarray(L["timestamps"]).astype(np.int64).reshape(-1)
            firsts, gid, nv = native_sessions(td, v)
            rep, _ns = true_sma50(v, td)
            nz = old != 0
            err = np.abs(rep[nz].astype(np.float64) - old[nz]) / np.maximum(np.abs(old[nz]), 1e-9) if nz.any() else np.array([0.0])
            holes = [int(ts[firsts[i]]) for i in range(len(nv)) if nv[i] == 0]
            rec = {"status": "GARBAGE" if float(err.max()) > 1e-5 else "OK", "sessions": len(nv),
                   "worst_rel": float(err.max()), "n_bad": int((err > 1e-5).sum()),
                   "zero_vol_sessions": len(holes), "hole_ts": holes[:8]}
            if rec["status"] == "GARBAGE" and apply:
                bak = p + f".bak-sma50-{stamp}"
                import shutil
                shutil.copy2(p, bak)
                L["volume_D_50_sma"] = rep
                tmp = p + ".tmp-repair.npz"
                np.savez_compressed(tmp, **{k: np.asarray(v) for k, v in L.items()})
                os.replace(tmp, p)
                rec["repaired"] = True
                rec["backup"] = os.path.basename(bak)
            audit["syms"][sym] = rec
        except Exception as e:
            audit["syms"][sym] = {"status": "REPAIR_FAIL", "err": f"{type(e).__name__}:{str(e)[:100]}"}
    n_g = sum(1 for r in audit["syms"].values() if r.get("status") == "GARBAGE")
    n_ok = sum(1 for r in audit["syms"].values() if r.get("status") == "OK")
    print(f"audited={len(audit['syms'])} GARBAGE={n_g} OK={n_ok} apply={apply}")
    ap = os.path.join(ROOT, "data", "reports", f"sma50_repair_{stamp}.json")
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    json.dump(audit, open(ap, "w"), indent=1)
    print("audit:", ap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
