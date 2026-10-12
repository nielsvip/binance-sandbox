#!/usr/bin/env python3
"""Dedupe NPZs whose rows were stored N times identically (verified: 0 conflicting closes).
Keeps the FIRST occurrence per timestamp, restores the true file (full depth preserved).
Fail-closed per symbol: refuses when any row key conflicts within a dup group or result is non-monotonic.
Backs up original to <sym>.npz.bak-dedup-<UTC>. Usage: dedupe_npz_rows.py [--apply] SYM [...]
"""
import json, os, sys, datetime
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IND = os.path.join(ROOT, "backtest_v8", "indicators")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    apply = "--apply" in sys.argv
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    audit = {"utc": stamp, "apply": apply, "syms": {}}
    for sym in args:
        p = os.path.join(IND, sym + ".npz")
        try:
            L = dict(np.load(p, allow_pickle=True))
            ts = np.asarray(L["timestamps"]).astype(np.int64).reshape(-1)
            n = len(ts)
            uniq, first, counts = np.unique(ts, return_index=True, return_counts=True)
            ndup = int((counts > 1).sum())
            if ndup == 0:
                audit["syms"][sym] = {"status": "CLEAN", "rows": n}
                continue
            keep = np.zeros(n, bool)
            keep[np.sort(first)] = True
            conf = []
            for k, v in L.items():
                a = np.asarray(v)
                if a.ndim >= 1 and len(a) == n and k != "timestamps":
                    if a.dtype.kind in "biufc":
                        f = a.astype(np.float64)
                        for t in uniq[counts > 1]:
                            idx = np.flatnonzero(ts == t)
                            if len(idx) > 1 and float(f[idx].max() - f[idx].min()) > 1e-6 * max(1.0, float(abs(f[idx].mean()))):
                                conf.append(k)
                                break
                    else:
                        for t in uniq[counts > 1]:
                            idx = np.flatnonzero(ts == t)
                            if len(idx) > 1 and not all(x == a[idx[0]] for x in a[idx]):
                                conf.append(k)
                                break
            rec = {"status": "CONFLICT" if conf else "DEDUPABLE", "rows": n, "unique": len(uniq), "dup_groups": ndup, "conflicts": conf[:6]}
            if conf:
                audit["syms"][sym] = rec
                continue
            if apply:
                import shutil
                bak = p + f".bak-dedup-{stamp}"
                shutil.copy2(p, bak)
                out = {k: (np.asarray(v)[keep] if (np.ndim(v) >= 1 and len(np.asarray(v)) == n) else v) for k, v in L.items()}
                if not bool((np.diff(np.asarray(out["timestamps"]).astype(np.int64)) > 0).all()):
                    audit["syms"][sym] = {"status": "REFUSE_NONMONOTONIC", "rows": n}
                    continue
                tmp = p + ".tmp-dedup.npz"
                np.savez_compressed(tmp, **{k: np.asarray(v) for k, v in out.items()})
                os.replace(tmp, p)
                rec["deduped"] = True
                rec["backup"] = os.path.basename(bak)
            audit["syms"][sym] = rec
        except Exception as e:
            audit["syms"][sym] = {"status": "FAIL", "err": f"{type(e).__name__}:{str(e)[:100]}"}
    print(json.dumps(audit, indent=0)[:2000])
    ap = os.path.join(ROOT, "data", "reports", f"npz_dedup_{stamp}.json")
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    json.dump(audit, open(ap, "w"), indent=1)
    print("audit:", ap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
