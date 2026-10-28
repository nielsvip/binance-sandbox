#!/usr/bin/env python3
"""Replace one 15m NPZ row in place with a recomputed row from the current klines.

Used when a bar's initial (mark / partial) calculation is superseded by the REAL 15m kline with volume.
Runs on the host that owns the NPZ and its kline cache (S1 crypto, S5 stocks). Backs up the NPZ first,
refuses if the bar is absent (use the gap fill for absent bars), and verifies strict ordering afterwards.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--venue", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--bars", required=True, help="comma list of bar starts (epoch s)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    base = Path(a.base)
    sys.path.insert(0, str(base))
    sys.path.insert(0, str(base / "tools"))
    import npz_tail_service as T
    from npz_live_generator import _load_npz as _load, _save_npz_atomic as _save, _encode_row_label as _enc

    npz = Path(T.npz_for(base, a.symbol))
    data = _load(npz)
    ts = data["timestamps"].astype("int64")
    pos = {int(x): i for i, x in enumerate(ts)}
    bars = [int(x) for x in a.bars.split(",") if x]
    report = {"symbol": a.symbol, "npz": str(npz), "rows": len(ts), "bars": []}
    refresh = {}
    for b in bars:
        if b not in pos:
            report["bars"].append({"bar": b, "result": "absent_use_gap_fill"})
            continue
        row = T._compute_row_process((a.symbol, b, str(base), a.venue))
        if not row:
            report["bars"].append({"bar": b, "result": "compute_none"})
            continue
        refresh[b] = dict(row, timestamps=b)
        for tf in ("15m", "5m", "3m", "1m"):
            refresh[b][f"timestamp_{tf}"] = b
        report["bars"].append({"bar": b, "result": "will_replace", "close": row.get("close_15m"), "volume": row.get("volume_15m")})
    report["apply"] = bool(a.apply and refresh)
    print(json.dumps(report), flush=True)
    if not a.apply or not refresh:
        return 0
    from npz_tail_store import _store_lock, tail_rows
    with _store_lock(npz):
        pending = {int(r["timestamps"]) for r in tail_rows(npz)}
        clash = sorted(b for b in refresh if b in pending)
        if clash:
            print(json.dumps({"refused": "bar still pending in sidecar; compaction first", "bars": clash}), flush=True)
            return 2
        return _apply(npz, data, ts, pos, refresh, base, a)


def _apply(npz, data, ts, pos, refresh, base, a):
    import numpy as np
    from npz_live_generator import _save_npz_atomic as _save, _load_npz as _load, _encode_row_label as _enc
    bak = base / "backups" / ("before_bar_refresh_" + a.symbol + "_" + time.strftime("%Y%m%d%H%M%S"))
    bak.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(npz), str(bak / npz.name))
    out = {}
    for k in data.keys():
        arr = data[k]
        if getattr(arr, "ndim", 0) != 1 or len(arr) != len(ts):
            out[k] = arr
            continue
        vals = list(arr)
        for b, row in refresh.items():
            i = pos[b]
            if k in row:
                v = row[k]
            else:
                v = arr[i]
            if v is None:
                v = float("nan") if arr.dtype.kind == "f" else arr[i]
            if arr.dtype != object and isinstance(v, str):
                enc = _enc(k, v)
                v = enc if enc is not None else arr[i]
            vals[i] = v
        out[k] = np.array(vals, dtype=arr.dtype if arr.dtype != object else object)
    _save(npz, out)
    d2 = _load(npz)
    t2 = d2["timestamps"].astype("int64")
    print(json.dumps({"rows_after": len(t2), "sorted": bool(np.all(np.diff(t2) > 0)),
                      "backup": str(bak)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
