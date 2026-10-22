#!/usr/bin/env python3
"""One-off S1 same-host merge (USER 2026-10-22): ~/binance/klines_cache (fresh, healed from Mac)
-> ~/binance-sandbox/klines_cache (stale since ~13:30Z, read by the S1 plan-B lane + npz_bar_refresh).
Append-only: bars whose timestamp key is newer than the sandbox tail are appended; history never
rewritten, never truncated. Prints per-file appended counts. Idempotent (re-run appends nothing)."""
import glob
import json
import os

SRC = os.path.expanduser("~/binance/klines_cache")
DST = os.path.expanduser("~/binance-sandbox/klines_cache")


def bars_of(doc):
    if isinstance(doc, list):
        return doc
    for k in ("bars", "data"):
        if isinstance(doc, dict) and isinstance(doc.get(k), list):
            return doc, k
    return doc, None


def key_of(b):
    if isinstance(b, dict):
        return str(b.get("timestamp") or b.get("time") or b.get("t") or b.get("date"))
    return str(b[0])


def load(path):
    with open(path) as fh:
        doc = json.load(fh)
    if isinstance(doc, list):
        return doc, None
    for k in ("bars", "data"):
        if isinstance(doc.get(k), list):
            return doc, k
    return doc, None


def main():
    n_files, n_bars, n_skip = 0, 0, 0
    for sp in glob.glob(os.path.join(SRC, "tradier", "*.json")):
        dp = os.path.join(DST, "tradier", os.path.basename(sp))
        if not os.path.exists(dp):
            n_skip += 1
            continue
        sdoc, sk = load(sp)
        sbars = sdoc if sk is None else sdoc[sk]
        ddoc, dk = load(dp)
        dbars = ddoc if dk is None else ddoc[dk]
        if not sbars or not dbars:
            continue
        have = {key_of(b) for b in dbars}
        new = [b for b in sbars if key_of(b) not in have and key_of(b) > key_of(dbars[-1])]
        if not new:
            continue
        if dk is None:
            ddoc.extend(new)
        else:
            ddoc[dk].extend(new)
        tmp = dp + ".merge_tmp"
        with open(tmp, "w") as fh:
            json.dump(ddoc, fh)
        os.replace(tmp, dp)
        n_files += 1
        n_bars += len(new)
    print(f"MERGED files={n_files} bars={n_bars} skipped_missing_dst={n_skip}", flush=True)


if __name__ == "__main__":
    main()
