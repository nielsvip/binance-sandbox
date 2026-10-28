"""Daily kline loader: date-only rows ("YYYY-MM-DD", "YYYY-MM-DD.000000Z") are that day's bar; dropped when the day has normal ISO rows.
Run: LOADER_DIR=<dir holding the patched npz_live_generator.py> python3 tests/test_day_only_loader.py <kline_dir> [more files...]"""
import glob
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.environ.get("LOADER_DIR", str(Path(__file__).resolve().parents[1])))
import npz_live_generator as g  # noqa: E402


def reference_dedup(rows):
    iso_days = {r["timestamp"][:10] for r in rows if len(r["timestamp"]) > 17}
    out = []
    for r in rows:
        t = r["timestamp"]
        if len(t) <= 17 and re.match(r"^\d{4}-\d\d-\d\d(\.0+Z)?$", t):
            if t[:10] in iso_days:
                continue
            r = dict(r, timestamp=t[:10] + "T00:00:00.000000Z")
        out.append(r)
    return out


def frame(rows):
    df = pd.DataFrame(rows)
    df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
    df = df.set_index("timestamp_dt", drop=False).sort_index()
    return df[~df.index.duplicated(keep="last")]


def check(path):
    rows = json.load(open(path))
    got = g._load_klines_df(Path(path))
    want = frame(reference_dedup(rows))
    cols = ["open", "high", "low", "close", "volume"]
    same = len(got) == len(want) and (got.index == want.index).all() and all(
        pd.to_numeric(got[c], errors="coerce").fillna(-1).equals(pd.to_numeric(want[c], errors="coerce").fillna(-1)) for c in cols if c in want)
    old = frame(rows)
    return same, len(rows), len(got), len(old)


if __name__ == "__main__":
    files = sys.argv[1:]
    if len(files) == 1 and os.path.isdir(files[0]):
        files = sorted(glob.glob(os.path.join(files[0], "*_D.json")))
    changed = bad = 0
    for f in files:
        same, n_raw, n_new, n_old = check(f)
        if n_new != n_old:
            changed += 1
        if not same:
            bad += 1
            print("MISMATCH", f)
        if len(files) <= 4:
            print(Path(f).name, "raw rows", n_raw, "-> loader rows", n_new, "(old loader", n_old, ") equals de-duplicated reference:", same)
    print(f"files {len(files)} series changed by the loader {changed} mismatches {bad}")
    sys.exit(1 if bad else 0)
