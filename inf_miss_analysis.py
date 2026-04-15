#!/usr/bin/env python3
"""Inventory inf account missed ranked movers.

For each 3m bar in the last 30 days covered by NPZ data, compute per-symbol
15m and 3m returns, apply ez_rankings.py:4317-4318 thresholds to build the
inf_long/inf_short candidate set at that instant, then cross-check against
data/history/inf/{SYMBOL}_{SIDE}.jsonl for an OPEN within +/- 60 min.

Outputs:
  data/inf_miss_analysis.csv  — one row per (symbol, side, bar)
  data/inf_miss_summary.csv   — aggregated per (symbol, side)
"""
import json, os, sys, glob, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
import numpy as np

NPZ_DIR = Path("backtest_v5/indicators_3m")
HIST_DIR = Path("data/history/inf")
DEC_DIR = Path("data/decisions")
OUT_DIR = Path("data")

WINDOW_DAYS = 30
LONG_15M_THR = 1.0
LONG_3M_THR = 0.9
SHORT_15M_THR = -0.9
SHORT_3M_THR = -0.75
OPEN_WINDOW_MIN = 60

def load_symbol(fp: Path):
    d = np.load(fp, allow_pickle=True)
    keys = set(d.keys())
    if "timestamps" not in keys or "close_3m" not in keys:
        return None
    ts = d["timestamps"].astype(np.int64)
    close = d["close_3m"].astype(np.float64)
    if ts.size != close.size or ts.size < 10:
        return None
    return ts, close

def load_hist_opens(symbol: str):
    events = []
    for side in ("LONG", "SHORT"):
        fp = HIST_DIR / f"{symbol}_{side}.jsonl"
        if not fp.exists(): continue
        with open(fp) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception: continue
                t = rec.get("type", "")
                if t not in ("OPEN", "QUICK_OPEN", "AUGMENT"): continue
                ts_str = rec.get("ts")
                if not ts_str: continue
                try:
                    if ts_str.endswith("Z"): ts_str = ts_str[:-1] + "+00:00"
                    dtp = datetime.fromisoformat(ts_str)
                except Exception: continue
                events.append((int(dtp.timestamp()), side, t))
    events.sort()
    return events

def has_open_near(events, target_ts_sec: int, side: str, window_sec: int):
    for e_ts, e_side, e_type in events:
        if e_side != side: continue
        if abs(e_ts - target_ts_sec) <= window_sec:
            return e_ts, e_type
    return None

def main():
    t0 = time.time()
    files = sorted(NPZ_DIR.glob("*.npz"))
    if not files:
        print("no NPZ found"); sys.exit(1)
    # Determine max ts to set window
    max_ts = 0
    payloads = {}
    for fp in files:
        sym = fp.stem.replace("_3m", "")
        try: r = load_symbol(fp)
        except Exception as e:
            print(f"skip {sym}: {e}"); continue
        if r is None: continue
        ts, close = r
        # normalize ts to seconds
        if ts[-1] > 1e12: ts = (ts // 1000).astype(np.int64)
        payloads[sym] = (ts, close)
        if ts[-1] > max_ts: max_ts = int(ts[-1])
    if not payloads:
        print("no usable payloads"); sys.exit(1)
    end_sec = max_ts
    start_sec = end_sec - WINDOW_DAYS * 86400
    print(f"window: {datetime.fromtimestamp(start_sec, tz=timezone.utc)} -> {datetime.fromtimestamp(end_sec, tz=timezone.utc)}  symbols={len(payloads)}")

    # Pre-compute returns arrays per symbol
    rows = []
    per_sym = {}
    for sym, (ts, close) in payloads.items():
        mask = (ts >= start_sec) & (ts <= end_sec)
        idx = np.where(mask)[0]
        if idx.size < 10: continue
        # 3m return %
        prev = np.empty_like(close); prev[0] = close[0]; prev[1:] = close[:-1]
        ret3 = np.where(prev > 0, (close - prev) / prev * 100.0, 0.0)
        # 15m return: 5 bars back
        shift = 5
        prev15 = np.empty_like(close); prev15[:shift] = close[:shift]; prev15[shift:] = close[:-shift]
        ret15 = np.where(prev15 > 0, (close - prev15) / prev15 * 100.0, 0.0)
        per_sym[sym] = (ts, ret3, ret15, idx, close)

    # Load opens/events per symbol once
    events_cache = {sym: load_hist_opens(sym) for sym in per_sym.keys()}

    # Walk timestamps — iterate per symbol because ez_rankings is per-symbol threshold, no inter-symbol ranking needed for "was this symbol qualifying"
    for sym, (ts, ret3, ret15, idx, close) in per_sym.items():
        ev = events_cache[sym]
        # Identify first qualifying bar of each 'spike episode' (30 min gap separates episodes)
        for side, (cond3_thr, cond15_thr, cond) in [
            ("LONG", (LONG_3M_THR, LONG_15M_THR, lambda r3, r15: (r3 > LONG_3M_THR) | (r15 > LONG_15M_THR))),
            ("SHORT", (SHORT_3M_THR, SHORT_15M_THR, lambda r3, r15: (r3 < SHORT_3M_THR) | (r15 < SHORT_15M_THR))),
        ]:
            q = cond(ret3[idx], ret15[idx])
            hit_idx = idx[q]
            if hit_idx.size == 0: continue
            episodes = []
            cur = [hit_idx[0]]
            for i in hit_idx[1:]:
                if (ts[i] - ts[cur[-1]]) > 30 * 60:
                    episodes.append(cur); cur = [i]
                else:
                    cur.append(i)
            episodes.append(cur)
            for ep in episodes:
                first_i = ep[0]
                peak_i = ep[int(np.argmax(np.abs(ret15[ep]) + np.abs(ret3[ep])))]
                first_ts = int(ts[first_i])
                opened = has_open_near(ev, first_ts, side, OPEN_WINDOW_MIN * 60)
                rows.append({
                    "symbol": sym,
                    "side": side,
                    "episode_start": datetime.fromtimestamp(first_ts, tz=timezone.utc).isoformat(),
                    "episode_bars": len(ep),
                    "ret15_peak": float(ret15[peak_i]),
                    "ret3_peak": float(ret3[peak_i]),
                    "opened": int(bool(opened)),
                    "open_dt_s": (opened[0] - first_ts) if opened else None,
                    "open_type": opened[1] if opened else None,
                })

    # Write detail CSV
    OUT_DIR.mkdir(exist_ok=True)
    detail = OUT_DIR / "inf_miss_analysis.csv"
    summ = OUT_DIR / "inf_miss_summary.csv"
    import csv
    cols = ["symbol","side","episode_start","episode_bars","ret15_peak","ret3_peak","opened","open_dt_s","open_type"]
    with open(detail, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow(r)

    # Summary per (symbol, side)
    from collections import defaultdict
    agg = defaultdict(lambda: {"episodes": 0, "captured": 0, "max_move_pct": 0.0, "max_move_time": ""})
    for r in rows:
        k = (r["symbol"], r["side"])
        a = agg[k]
        a["episodes"] += 1
        a["captured"] += r["opened"]
        move = max(abs(r["ret15_peak"]), abs(r["ret3_peak"]))
        if move > a["max_move_pct"]:
            a["max_move_pct"] = move; a["max_move_time"] = r["episode_start"]

    with open(summ, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["symbol","side","episodes","captured","missed","capture_rate","max_move_pct","max_move_time"])
        tot_ep = tot_cap = 0
        out_rows = []
        for (sym, side), a in agg.items():
            missed = a["episodes"] - a["captured"]
            rate = a["captured"] / a["episodes"] if a["episodes"] else 0
            tot_ep += a["episodes"]; tot_cap += a["captured"]
            out_rows.append((sym, side, a["episodes"], a["captured"], missed, rate, a["max_move_pct"], a["max_move_time"]))
        out_rows.sort(key=lambda r: (-r[2], r[5]))
        for row in out_rows: w.writerow(row)

    print(f"elapsed: {time.time()-t0:.1f}s")
    print(f"rows: {len(rows)}  detail: {detail}  summary: {summ}")
    print(f"TOTAL episodes: {tot_ep}  captured: {tot_cap}  capture_rate: {tot_cap/tot_ep if tot_ep else 0:.2%}")

    # print worst offenders (high-episode, low-capture)
    print("\nTOP 15 MISS-HEAVY (symbols with most missed episodes, sorted by missed desc):")
    ranked = sorted(out_rows, key=lambda r: -r[4])[:15]
    print(f"{'symbol':<14} {'side':<6} {'eps':>4} {'cap':>4} {'miss':>4} {'rate':>6} {'max%':>8}")
    for r in ranked:
        print(f"{r[0]:<14} {r[1]:<6} {r[2]:>4} {r[3]:>4} {r[4]:>4} {r[5]*100:>5.1f}% {r[6]:>7.2f}")

if __name__ == "__main__":
    main()
