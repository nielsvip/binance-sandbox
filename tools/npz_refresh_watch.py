#!/usr/bin/env python3
"""Stock refresh trigger: when the REAL 15m kline (with volume) lands for a recent RTH bar whose NPZ row
still has no volume, replace that row via npz_bar_refresh. Runs on S5; cron every 5 minutes."""
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/niels/binance-sandbox")
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tools"))
import npz_tail_service as T  # noqa: E402
from tools.npz_cutoffs import is_rth_bar_start  # noqa: E402
from npz_tail_store import tail_rows  # noqa: E402

LOOKBACK = 2 * 3600


def cache_rows(sym):
    p = BASE / "klines_cache" / "tradier" / f"{sym}_15m.json"
    try:
        d = json.loads(p.read_text())
    except Exception:
        return {}
    rows = d if isinstance(d, list) else d.get("klines", [])
    out = {}
    for r in rows:
        try:
            ts = int(np.datetime64(str(r["timestamp"]).replace("Z", "")).astype("datetime64[s]").astype("int64"))
        except Exception:
            continue
        out[ts] = r
    return out


def main():
    now = time.time()
    uni = T.load_universe(str(BASE))
    syms = sorted({s.rsplit("_", 1)[0] for s in T.owned_symbols(uni, "stocks")})
    todo = {}
    for sym in syms:
        npz = Path(T.npz_for(BASE, sym))
        try:
            d = np.load(str(npz), allow_pickle=True)
        except Exception:
            continue
        ts = d["timestamps"].astype("int64")
        vol = d["volume_15m"] if "volume_15m" in d.files else None
        if vol is None:
            continue
        pending = {int(r["timestamps"]) for r in tail_rows(npz)}
        cache = cache_rows(sym)
        bars = []
        for i in np.where(ts >= now - LOOKBACK)[0]:
            b = int(ts[i])
            if b in pending or not is_rth_bar_start(b):
                continue
            v_npz = vol[i]
            if v_npz is not None and np.isfinite(float(v_npz)) and float(v_npz) > 0:
                continue
            c = cache.get(b)
            if not c or str(c.get("_source", "")) == "mark_resample":
                continue
            if not float(c.get("volume") or 0) > 0:
                continue
            bars.append(b)
        if bars:
            todo[sym] = bars
    report = {"checked": len(syms), "to_refresh": {k: len(v) for k, v in todo.items()}}
    print(json.dumps(report), flush=True)
    for sym, bars in todo.items():
        r = subprocess.run([sys.executable, str(BASE / "tools" / "npz_bar_refresh.py"), "--base", str(BASE),
                            "--venue", "tradier", "--symbol", sym, "--bars", ",".join(map(str, bars)), "--apply"],
                           capture_output=True, text=True, timeout=900)
        print(json.dumps({"sym": sym, "rc": r.returncode, "out": r.stdout.strip()[-300:],
                          "err": r.stderr.strip()[-200:]}), flush=True)


if __name__ == "__main__":
    main()
