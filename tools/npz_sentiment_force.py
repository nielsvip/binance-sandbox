#!/usr/bin/env python3
"""npz_sentiment_force — low-memory forced market_sentiment_score rebuild.

Formula: cross-symbol bull/bear ratio from wt_composite_bias per timestamp
(identical to patch_npz_missing_fields.inject_sentiment_pass). Memory-safe:
pass 1 streams (ts,bias) pairs into sqlite (no giant dicts/arrays in RAM);
pass 2 replays per-file with zipfile-level surgery (stream entries, append one
key). Atomic per file (tmp + os.replace). Peak RAM ~150MB.

  python3 tools/npz_sentiment_force.py ~/binance-sandbox/backtest_v8/indicators
"""
import io
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

import numpy as np


def main():
    only_flat = "--only-flat" in sys.argv
    argv = [a for a in sys.argv[1:] if a != "--only-flat"]
    npz_dir = Path(argv[0])
    for stray in list(npz_dir.glob("*.sent.npz")) + [npz_dir / ".sent_tmp.db"]:
        try:
            stray.unlink()
        except OSError:
            pass
    files = sorted(p for p in npz_dir.glob("*.npz") if ".tmp" not in p.name and ".sent." not in p.name)
    dbf = str(npz_dir / ".sent_tmp.db")
    con = sqlite3.connect(dbf)
    con.execute("CREATE TABLE v (t INTEGER, b INTEGER)")
    print(f"[sent] pass 1: stream {len(files)} files...", flush=True)
    valid = 0
    for i, p in enumerate(files, 1):
        try:
            with np.load(str(p), allow_pickle=True) as z:
                if "timestamps" not in z.files or "wt_composite_bias" not in z.files:
                    continue
                ts = z["timestamps"].astype(np.int64)
                bias = z["wt_composite_bias"].astype(np.int8)
                if len(ts) != len(bias):
                    continue
                con.executemany("INSERT INTO v VALUES (?, ?)", ((int(t), int(b)) for t, b in zip(ts.tolist(), bias.tolist())))
                valid += 1
        except Exception as e:
            print(f"[sent] pass1 fail {p.name}: {type(e).__name__} {e}", flush=True)
        if i % 200 == 0:
            con.commit()
            print(f"[sent] pass1 {i}/{len(files)}...", flush=True)
    con.commit()
    print(f"[sent] aggregate...", flush=True)
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("CREATE TABLE s (t INTEGER PRIMARY KEY, s REAL)")
    lo, hi = con.execute("SELECT MIN(t), MAX(t) FROM v").fetchone()
    span = max(hi - lo + 1, 1)
    K = 16
    for k in range(K):
        a = lo + span * k // K
        b = lo + span * (k + 1) // K - 1
        con.executemany(
            "INSERT INTO s VALUES (?, ?)",
            ((t, float(50.0 + (bull - bear) / max(tot, 1) * 50.0)) for t, bull, bear, tot in con.execute("SELECT t, SUM(CASE WHEN b=1 THEN 1 ELSE 0 END), SUM(CASE WHEN b=-1 THEN 1 ELSE 0 END), COUNT(*) FROM v WHERE t BETWEEN ? AND ? GROUP BY t", (a, b))),
        )
        con.commit()
    con.execute("DROP TABLE v")
    con.execute("VACUUM")
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM s").fetchone()[0]
    print(f"[sent] valid syms: {valid} | unique timestamps: {n}", flush=True)
    if not n:
        con.close()
        return
    print("[sent] pass 2: zip-surgery write...", flush=True)
    updated = skipped = 0
    for i, p in enumerate(files, 1):
        try:
            with np.load(str(p), allow_pickle=True) as z:
                if "timestamps" not in z.files or "wt_composite_bias" not in z.files:
                    continue
                if only_flat and "market_sentiment_score" in z.files:
                    cur = np.asarray(z["market_sentiment_score"], dtype=float)
                    if cur.size and not bool(np.all(cur == 50.0)):
                        skipped += 1
                        continue
                ts = z["timestamps"].astype(np.int64)
                if len(ts) != len(z["wt_composite_bias"]):
                    continue
            lo, hi = int(ts.min()), int(ts.max())
            score = {t: s for t, s in con.execute("SELECT t, s FROM s WHERE t BETWEEN ? AND ?", (lo, hi))}
            mss = np.array([score.get(int(t), 50.0) for t in ts.tolist()], dtype=np.float32)
            del score
            buf = io.BytesIO()
            np.save(buf, mss)
            data = buf.getvalue()
            tmp = str(p) + ".sent.npz"
            with zipfile.ZipFile(p, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.filename != "market_sentiment_score.npy":
                        zout.writestr(item, zin.read(item.filename))
                zout.writestr("market_sentiment_score.npy", data)
            os.replace(tmp, str(p))
            updated += 1
            del mss, data
        except Exception as e:
            print(f"[sent] pass2 fail {p.name}: {type(e).__name__} {e}", flush=True)
            try:
                os.unlink(str(p) + ".sent.npz")
            except OSError:
                pass
        if i % 100 == 0:
            print(f"[sent] {i}/{len(files)}...", flush=True)
    con.close()
    try:
        os.unlink(dbf)
    except OSError:
        pass
    print(f"updated: {updated} skipped_varied: {skipped}", flush=True)


if __name__ == "__main__":
    main()
