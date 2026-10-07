#!/usr/bin/env python3
"""Rebuild stock NPZs to TODAY from the longest surviving history (2026-09-29).

FIXED 2026-10-01 (Agent S, NPZ sawtooth): the original merged_15m() turned EVERY row of a 5m-base source NPZ into a pseudo-15m bar at its
5m timestamp (ghost bars at :05/:10 carrying a stale, 4h-shifted 5m series for 2026-09-11..18) and let klines overwrite only the exact
:00/:15/:30/:45 stamps => interleaved two-series NPZs (UEC, SMCI: -99% churn). Now: history bars come ONLY from boundary rows (ts % 900 == 0),
ONLY strictly BEFORE the first bar of the authentic klines cache (the cache wins its whole range), and the precompute is run with
FORCE_TRADIER_15M_BASE=1 (15m-base standard; the user forbids 5m data in the backtest system).

klines_cache_backtest/tradier is a symlink to the rolling live cache (~42 sessions of 15m), so a plain
backtest_v8_precompute refuses every stock (needs >=300d) and older degraded builds (649 keys, no W/M) crept in.
The only surviving long 15m history is inside the older complete NPZs (open/high/low/close/volume_15m + timestamps,
verified to align with kline timestamps at zero offset). This tool:
  1. extracts the 15m OHLCV from --src-npz-dir/{SYM}.npz,
  2. union-merges it UNDER the current klines (klines win on overlap = fresher bars),
  3. writes {SYM}_15m.json into --stage-dir (a klines source precompute already unions, e.g. klines_cache_macbook/tradier),
  4. runs backtest_v8_precompute --mode tradier --out-dir --out (never the canonical indicators dir),
  5. validates the result (keys, span >=300d, last bar >= klines last bar) and prints one JSON line per symbol.
Read-only on the canonical NPZs; installing validated outputs is a separate, explicit step.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def iso(t):
    return dt.datetime.fromtimestamp(int(t), tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def merged_15m(symbol, src_dir, klines_dir):
    d = np.load(src_dir / f"{symbol}.npz")
    ts = d["timestamps"].astype(np.int64)
    ts = ts // 1000 if ts[-1] > 1e11 else ts
    bars = {}
    for t, o, h, l, c, v in zip(ts, d["open_15m"], d["high_15m"], d["low_15m"], d["close_15m"], d["volume_15m"]):
        if np.isfinite(c) and c > 0:
            bars[int(t)] = {"timestamp": iso(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": float(v)}
    kpath = klines_dir / f"{symbol}_15m.json"
    k_last = None
    k_rows = json.load(open(kpath)) if kpath.exists() else []
    k_first = min((int(dt.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp()) for r in k_rows), default=None)
    # history = boundary rows only, strictly before the authentic cache range
    bars = {t: v for t, v in bars.items() if t % 900 == 0 and (k_first is None or t < k_first)}
    n_hist = len(bars)
    if kpath.exists():
        for r in k_rows:
            t = int(dt.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp())
            bars[t] = {"timestamp": r["timestamp"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]}
            k_last = t if k_last is None or t > k_last else k_last
    rows = [bars[t] for t in sorted(bars)]
    return rows, n_hist, k_last


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", required=True, help="comma list or @file")
    p.add_argument("--src-npz-dir", required=True, type=Path)
    p.add_argument("--klines-dir", default=str(ROOT / "klines_cache" / "tradier"), type=Path)
    p.add_argument("--stage-dir", default=str(ROOT / "klines_cache_macbook" / "tradier"), type=Path)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    syms = [s.strip() for s in (open(a.symbols[1:]).read().split() if a.symbols.startswith("@") else a.symbols.split(",")) if s.strip()]
    a.stage_dir.mkdir(parents=True, exist_ok=True)
    a.out.mkdir(parents=True, exist_ok=True)
    for s in syms:
        res = {"symbol": s}
        try:
            rows, n_hist, k_last = merged_15m(s, a.src_npz_dir, a.klines_dir)
            span = (dt.datetime.fromisoformat(rows[-1]["timestamp"].replace("Z", "+00:00")) - dt.datetime.fromisoformat(rows[0]["timestamp"].replace("Z", "+00:00"))).days
            res.update(merged_bars=len(rows), hist_bars=n_hist, span_days=span, first=rows[0]["timestamp"][:10], last=rows[-1]["timestamp"][:16])
            stage = a.stage_dir / f"{s}_15m.json"
            tmp = stage.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rows))
            os.replace(tmp, stage)
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "stock_npz_precompute_relaxed.py"), "--mode", "tradier", "--symbols", s, "--out-dir", str(a.out)], cwd=str(ROOT), capture_output=True, text=True, timeout=1800, env=dict(os.environ, FORCE_TRADIER_15M_BASE="1"))
            out = a.out / f"{s}.npz"
            if not out.exists():
                res["error"] = "precompute wrote nothing: " + " | ".join(x for x in (r.stdout + r.stderr).splitlines() if "SKIP" in x or "Error" in x)[-200:]
            else:
                d = np.load(out)
                t = d["timestamps"].astype(np.int64)
                t = t // 1000 if t[-1] > 1e11 else t
                res.update(keys=len(d.files), bars=int(len(t)), npz_first=iso(t[0])[:10], npz_last=iso(t[-1])[:16], has_W="wt1_W" in d.files, npz_span_days=int((t[-1] - t[0]) / 86400))
                res["ok"] = bool(res["keys"] >= 900 and res["npz_span_days"] >= 300 and (k_last is None or t[-1] >= k_last - 86400))
        except Exception as e:
            res["error"] = str(e)[:200]
        print(json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
