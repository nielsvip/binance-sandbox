#!/usr/bin/env python3
"""klines_tail_apply - RECEIVER of an incremental klines_canonical tail bundle (USER LAW 2026-10-20, "all new calculations calculate up to the
latest kline bar, max 15m old in the npz").

Why: the whole-store `rsync -az` push (829 files, 1.35 GB of 15m alone, serial per host) took 5-7 min per host and the Mac daemon cycle had
grown from 121 s to 905 s, so the closed 15m bar reached S1/S2 10-20 min after the close. A bundle is the last N rows of every changed file
(~100 KB); applying it is a copy + splice per file (no JSON parse of a 27 MB file).

Contract (stdin = bundle JSON {"v":1,"src":..,"files":{"BTCUSDC_15m.json":[rows..]}}):
  - GROW-ONLY. Rows strictly newer than the stored last row are appended; rows already stored must be identical (a correction goes through the
    full-parse path, still grow-only: write_rows refuses fewer rows / a shorter span). Never shrinks, never rewrites a file that is ahead.
  - Contiguity: if the bundle's first new row is not the stored last + one step AND the bundle does not overlap the stored tail, the file is
    reported `gap` and left alone (the periodic full rsync heals it). Natural exchange gaps inside the overlap are fine.
  - Takes the same write lock as tools/klines_canonical.py (KLINES_CANONICAL_WLOCK), so a local fetch daemon and this tool never interleave.
  - Files that do not exist locally are skipped (`missing_store`): a bundle never creates a symbol's history.
Prints one JSON summary line; exit 0 unless the bundle itself is unreadable.
"""
import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import klines_canonical as K  # noqa: E402

STEP_S = {"15m": 900, "1h": 3600, "4h": 14400, "D": 86400}


def apply_bundle(bundle, store):
    store = Path(store)
    res = {"appended_files": 0, "appended_rows": 0, "noop": 0, "corrected": 0, "gap": [], "missing_store": 0, "errors": [], "latest": {}}
    K.WLOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(K.WLOCK, "a+") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        for name, rows in sorted((bundle.get("files") or {}).items()):
            path = store / name
            try:
                if not path.exists():
                    res["missing_store"] += 1
                    continue
                if not rows:
                    continue
                tr = K.tail_rows(path, n=16)
                if not tr:
                    res["errors"].append(f"{name}: unreadable tail")
                    continue
                by = {K.ts_ms(r["timestamp"]): r for r in tr}
                last = max(by)
                tf = name[:-5].rsplit("_", 1)[1]
                step = STEP_S.get(tf, 900) * 1000
                new, corr, below = [], False, False
                for r in rows:
                    t = K.ts_ms(r["timestamp"])
                    if t > last:
                        new.append((t, r))
                    elif t in by:
                        if any(float(by[t].get(c, 0) or 0) != float(r[c]) for c in K.KEYS):
                            corr = True
                    else:
                        below = True
                if corr:
                    allr = K.load_rows(path)
                    idx = {K.ts_ms(x["timestamp"]): i for i, x in enumerate(allr)}
                    for r in rows:
                        t = K.ts_ms(r["timestamp"])
                        if t in idx and any(float(allr[idx[t]].get(c, 0) or 0) != float(r[c]) for c in K.KEYS):
                            allr[idx[t]] = r
                    have = {K.ts_ms(x["timestamp"]) for x in allr}
                    allr += [r for t, r in new if t not in have]
                    allr.sort(key=lambda x: K.ts_ms(x["timestamp"]))
                    ok, why = K.write_rows(path, allr)
                    if ok:
                        res["corrected"] += 1
                        res["appended_rows"] += len(new)
                        res["latest"][name] = max(K.ts_ms(x["timestamp"]) for x in allr)
                    else:
                        res["errors"].append(f"{name}: {why}")
                    continue
                if not new:
                    res["noop"] += 1
                    res["latest"][name] = last
                    continue
                new.sort(key=lambda x: x[0])
                overlaps = any(K.ts_ms(r["timestamp"]) <= last for r in rows)
                if new[0][0] - last != step and not overlaps:
                    res["gap"].append(name)
                    continue
                K.append_rows_atomic(path, [r for _, r in new])
                res["appended_files"] += 1
                res["appended_rows"] += len(new)
                res["latest"][name] = new[-1][0]
            except Exception as e:
                res["errors"].append(f"{name}: {type(e).__name__} {e}"[:160])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=str(K.STORE))
    ap.add_argument("--state", default=str(K.ROOT / "data" / "klines_tail_apply_state.json"))
    a = ap.parse_args()
    t0 = time.time()
    try:
        bundle = json.loads(sys.stdin.read())
    except ValueError as e:
        print(json.dumps({"ok": False, "error": f"bad bundle {e}"}))
        return 2
    res = apply_bundle(bundle, a.store)
    latest = res.pop("latest")
    res.update(ok=True, src=bundle.get("src"), took_s=round(time.time() - t0, 2), at=time.time(), latest_15m_max=max([v for k, v in latest.items() if k.endswith("_15m.json")] or [0]))
    try:
        Path(a.state).parent.mkdir(parents=True, exist_ok=True)
        Path(a.state).write_text(json.dumps(res))
    except OSError:
        pass
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
