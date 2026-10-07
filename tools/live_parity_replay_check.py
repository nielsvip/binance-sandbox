#!/usr/bin/env python3
"""Replay check (Agent D): live_parity_keys.derive_bar_keys on real klines vs the NPZ arrays at the same timestamps.
usage (on a host with klines_cache + backtest_v8/indicators): python live_parity_replay_check.py SYMBOL"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_parity_keys as K  # noqa: E402

sym = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDC"
base = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "binance-sandbox"
z = np.load(base / "backtest_v8" / "indicators" / f"{sym}.npz", allow_pickle=True)
ts = np.asarray(z["timestamps"])
print("npz bars", len(ts), "ts dtype", ts.dtype, "first/last", ts[0], ts[-1])
res = {}
for tf in ("15m", "1h", "4h"):
    d = json.load(open(base / "klines_cache" / f"{sym}_{tf}.json"))
    df = pd.DataFrame(d)
    df["t"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None).astype("datetime64[s]").astype("int64")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    cmp = {f"close_3bar_{tf}": [], f"close_5bar_{tf}": [], f"ema_9_above_21_{tf}": []}
    if tf == "1h":
        cmp["volume_sma_1h"] = []
    tmax = (int(ts[-1]) // (1000 if ts[0] > 1e11 else 1)) - 2 * {"15m": 900, "1h": 3600, "4h": 14400}[tf]
    idx = np.where(df["t"].values < tmax)[0]
    for i in idx[-60:]:  # bars CLOSED in both views and inside the NPZ coverage
        sub = df.iloc[: i + 1]
        out = {}
        K.derive_bar_keys(sub, tf, out)
        step = {"15m": 900, "1h": 3600, "4h": 14400}[tf]
        tclose = int(df["t"].iloc[i]) + step  # klines timestamp = bar OPEN; NPZ row stamped at base-bar open: last base bar of this closed bar
        scale = 1000 if ts[0] > 1e11 else 1
        j0 = int(np.searchsorted(ts, (tclose - 900) * scale))  # base bar (15m) that closes together with the tf bar
        out_off = out
        for k in cmp:
            if k in z.files and k in out:
                cmp[k].append([float(out[k])] + [float(z[k][min(max(j0 + o, 0), len(ts) - 1)]) for o in (-1, 0, 1, 2)])
    for k, v in cmp.items():
        if not v:
            res[k] = "no overlap/key"
            continue
        a = np.array(v, dtype=float)
        best = {}
        for oi, o in enumerate((-1, 0, 1, 2)):
            rel = np.abs(a[:, 0] - a[:, 1 + oi]) / (np.abs(a[:, 1 + oi]) + 1e-9)
            best[f"base_offset_{o}"] = {"max_rel": float(rel.max()), "exact_share": float((rel < 1e-5).mean())}
        res[k] = {"n": len(v), **best}
print(json.dumps(res, indent=1))
