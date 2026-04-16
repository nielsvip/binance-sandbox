#!/usr/bin/env python3
"""SCALP_V2 (HTF Breakout Scalper) sweep on 48 NPZ symbols, last 30 days.

Replays the V2 strategy with each exit variant + a small parameter grid:
  - 8 EXIT VARIANTS  (V1_WT_CONFIRM, V2_LH_LL_3M, V3, V4_HA_FLIP, V5, V6, V7_TIGHT_TRAILING, V8_HTF_RECLAIM)
  - REQUIRE_ALL    {True, False}
  - MAX_HOLD_MIN   {15, 30, 60, 120}
  - DC_HTF_LIST    {["15m","1h"], ["15m","1h","4h"]}

Entry rule (fixed, mirrors htf_breakout_scalper.check_scalp_v2_entry):
  LONG : price > dc_high_15m_prev AND price > dc_high_1h_prev   (or 'any' if not require_all)
  SHORT: price < dc_low_15m_prev  AND price < dc_low_1h_prev

Universal stop (mirrors module): LONG exits if price <= low_3m_prev; SHORT if price >= high_3m_prev.

Outputs: data/inf_v2_sweep.csv  +  ranked summary table.
"""
import csv, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import numpy as np

NPZ_DIR = Path("backtest_v5/indicators_3m")
LOOKBACK_DAYS = 30
REENTRY_COOLDOWN_S = 300

VARIANTS = ["V1_WT_CONFIRM","V2_LH_LL_3M","V4_HA_FLIP","V6_COMBINED_WT_HA","V7_TIGHT_TRAILING","V8_HTF_RECLAIM"]
MAX_HOLDS = [15, 60]
REQUIRE_ALL_OPTS = [True, False]
DC_HTF_OPTS = [("15m","1h")]

def load_npz(fp):
    d = np.load(fp, allow_pickle=True)
    ts = d["timestamps"].astype(np.int64)
    if ts[-1] > 1e12: ts = ts // 1000
    return d, ts

def _arr(d, key, default=0.0):
    if key in d:
        a = d[key]
        return a.astype(np.float64) if a.dtype != object else np.array([default]*len(a))
    return None

def _str_arr(d, key):
    if key in d: return d[key].astype(str)
    return None

# ---------- Entry ----------
def entry_signal(close, dc_high_15m_prev, dc_high_1h_prev, dc_high_4h_prev,
                 dc_low_15m_prev, dc_low_1h_prev, dc_low_4h_prev, tfs, require_all):
    """Returns 2 boolean arrays (long_entry, short_entry) per bar."""
    n = len(close)
    long_ok = np.zeros(n, dtype=bool); short_ok = np.zeros(n, dtype=bool)
    long_levels = []; short_levels = []
    for tf in tfs:
        if tf == "15m": long_levels.append(dc_high_15m_prev); short_levels.append(dc_low_15m_prev)
        elif tf == "1h": long_levels.append(dc_high_1h_prev); short_levels.append(dc_low_1h_prev)
        elif tf == "4h":
            if dc_high_4h_prev is not None: long_levels.append(dc_high_4h_prev); short_levels.append(dc_low_4h_prev)
    if not long_levels: return long_ok, short_ok
    long_pass = np.stack([(lvl > 0) & (close > lvl) for lvl in long_levels], axis=1)
    short_pass = np.stack([(lvl > 0) & (close < lvl) for lvl in short_levels], axis=1)
    if require_all:
        long_ok = long_pass.all(axis=1); short_ok = short_pass.all(axis=1)
    else:
        long_ok = long_pass.any(axis=1); short_ok = short_pass.any(axis=1)
    return long_ok, short_ok

# ---------- Exit checkers (per-bar arrays of "should exit") ----------
def exit_v1_wt_confirm(d, n):
    wt1 = d.get("wt1_3m"); wt2 = d.get("wt2_3m")
    if wt1 is None or wt2 is None: return None, None
    wt1 = wt1.astype(np.float64); wt2 = wt2.astype(np.float64)
    wt1p = np.roll(wt1, 1); wt2p = np.roll(wt2, 1)
    long_exit = (wt1p > wt2p) & (wt1 < wt2)
    short_exit = (wt1p < wt2p) & (wt1 > wt2)
    return long_exit, short_exit

def exit_lh_ll(d, tf):
    high = d.get(f"high_{tf}"); high_prev = d.get(f"high_{tf}_prev")
    low = d.get(f"low_{tf}");   low_prev = d.get(f"low_{tf}_prev")
    if high is None or low is None: return None, None
    high = high.astype(np.float64); high_prev = high_prev.astype(np.float64) if high_prev is not None else np.roll(high,1)
    low = low.astype(np.float64); low_prev = low_prev.astype(np.float64) if low_prev is not None else np.roll(low,1)
    long_exit = high < high_prev   # lower high
    short_exit = low > low_prev    # higher low
    return long_exit, short_exit

def exit_ha_flip(d):
    ha = d.get("ha_3m")
    if ha is None: return None, None
    ha = ha.astype(str)
    long_exit = (ha == "red")
    short_exit = (ha == "green")
    return long_exit, short_exit

def exit_tight_trailing(d):
    close = d["close_3m"].astype(np.float64)
    low_prev = d.get("low_3m_prev"); high_prev = d.get("high_3m_prev")
    if low_prev is None or high_prev is None: return None, None
    low_prev = low_prev.astype(np.float64); high_prev = high_prev.astype(np.float64)
    long_exit = close < low_prev
    short_exit = close > high_prev
    return long_exit, short_exit

def exit_htf_reclaim(d):
    close = d["close_3m"].astype(np.float64)
    dc_high_15m = d.get("dc_high_15m"); dc_low_15m = d.get("dc_low_15m")
    if dc_high_15m is None: return None, None
    dc_high_15m = dc_high_15m.astype(np.float64); dc_low_15m = dc_low_15m.astype(np.float64)
    long_exit = close < dc_high_15m   # fell back below breakout level
    short_exit = close > dc_low_15m
    return long_exit, short_exit

def exit_combined_wt_ha(d, n):
    le_wt, se_wt = exit_v1_wt_confirm(d, n)
    le_ha, se_ha = exit_ha_flip(d)
    if le_wt is None or le_ha is None: return None, None
    return le_wt & le_ha, se_wt & se_ha

def get_exit_arrays(variant, d, n):
    """Return (long_exit_per_bar, short_exit_per_bar) booleans. None if data missing."""
    if variant == "V1_WT_CONFIRM": return exit_v1_wt_confirm(d, n)
    if variant == "V2_LH_LL_3M":   return exit_lh_ll(d, "3m")
    if variant == "V3_LH_LL_1M":   return exit_lh_ll(d, "3m")  # NPZ has no 1m, fall back to 3m
    if variant == "V4_HA_FLIP":    return exit_ha_flip(d)
    if variant == "V5_BREAK_HIGH_REENTRY": return exit_ha_flip(d)  # exit on HA flip
    if variant == "V6_COMBINED_WT_HA": return exit_combined_wt_ha(d, n)
    if variant == "V7_TIGHT_TRAILING": return exit_tight_trailing(d)
    if variant == "V8_HTF_RECLAIM": return exit_htf_reclaim(d)
    return None, None

# Universal stop: LONG <= low_3m_prev, SHORT >= high_3m_prev
def universal_stop_arrays(d):
    close = d["close_3m"].astype(np.float64)
    low_prev = d.get("low_3m_prev"); high_prev = d.get("high_3m_prev")
    if low_prev is None or high_prev is None: return None, None
    low_prev = low_prev.astype(np.float64); high_prev = high_prev.astype(np.float64)
    return close <= low_prev, close >= high_prev

# ---------- Simulator ----------
def simulate(d, ts, idx_start, idx_end, variant, require_all, max_hold_min, dc_tfs):
    """Return list of trade dicts."""
    close = d["close_3m"].astype(np.float64)
    dh15p = _arr(d, "dc_high_15m_prev") if "dc_high_15m_prev" in d else _arr(d, "dc_high_15m")
    dl15p = _arr(d, "dc_low_15m_prev") if "dc_low_15m_prev" in d else _arr(d, "dc_low_15m")
    dh1hp = _arr(d, "dc_high_1h_prev") if "dc_high_1h_prev" in d else _arr(d, "dc_high_1h")
    dl1hp = _arr(d, "dc_low_1h_prev") if "dc_low_1h_prev" in d else _arr(d, "dc_low_1h")
    dh4hp = _arr(d, "dc_high_4h_prev") if "dc_high_4h_prev" in d else _arr(d, "dc_high_4h")
    dl4hp = _arr(d, "dc_low_4h_prev") if "dc_low_4h_prev" in d else _arr(d, "dc_low_4h")
    if dh15p is None or dh1hp is None: return []
    long_entry, short_entry = entry_signal(close, dh15p, dh1hp, dh4hp, dl15p, dl1hp, dl4hp, dc_tfs, require_all)
    le_arr, se_arr = get_exit_arrays(variant, d, len(close))
    if le_arr is None: return []
    us_long, us_short = universal_stop_arrays(d)
    if us_long is None: return []

    trades = []
    in_long = False; in_short = False
    entry_idx = -1; entry_px = 0; max_hold_bars = max_hold_min // 3 + 1
    last_trade_close_ts = -10**12
    for i in range(idx_start, idx_end):
        # close logic first
        if in_long:
            held = i - entry_idx
            stop_hit = us_long[i]
            exit_hit = le_arr[i]
            time_hit = held >= max_hold_bars
            if stop_hit or exit_hit or time_hit:
                pnl = (close[i] - entry_px) / entry_px * 100
                trades.append({"side":"LONG","entry_ts":int(ts[entry_idx]),"exit_ts":int(ts[i]),
                               "entry":entry_px,"exit":float(close[i]),"pnl_pct":pnl,"held_bars":held,
                               "exit_kind":("STOP" if stop_hit else "VARIANT" if exit_hit else "TIME")})
                in_long = False; last_trade_close_ts = ts[i]
        elif in_short:
            held = i - entry_idx
            stop_hit = us_short[i]
            exit_hit = se_arr[i]
            time_hit = held >= max_hold_bars
            if stop_hit or exit_hit or time_hit:
                pnl = (entry_px - close[i]) / entry_px * 100
                trades.append({"side":"SHORT","entry_ts":int(ts[entry_idx]),"exit_ts":int(ts[i]),
                               "entry":entry_px,"exit":float(close[i]),"pnl_pct":pnl,"held_bars":held,
                               "exit_kind":("STOP" if stop_hit else "VARIANT" if exit_hit else "TIME")})
                in_short = False; last_trade_close_ts = ts[i]
        # entry logic
        if not in_long and not in_short and (ts[i] - last_trade_close_ts) >= REENTRY_COOLDOWN_S:
            if long_entry[i]:
                in_long = True; entry_idx = i; entry_px = float(close[i])
            elif short_entry[i]:
                in_short = True; entry_idx = i; entry_px = float(close[i])
    return trades

def main():
    t0 = time.time()
    files = sorted(NPZ_DIR.glob("*.npz"))
    if not files: print("no NPZ"); return
    # determine window
    syms = []
    for fp in files:
        d, ts = load_npz(fp)
        if ts.size < 100: continue
        syms.append((fp.stem, d, ts))
    if not syms: print("no usable npz"); return
    end_ts = max(s[2][-1] for s in syms)
    start_ts = end_ts - LOOKBACK_DAYS * 86400
    print(f"window: {datetime.fromtimestamp(start_ts, tz=timezone.utc)} -> {datetime.fromtimestamp(end_ts, tz=timezone.utc)}  symbols={len(syms)}")

    results = []  # list of dict per (variant, require_all, max_hold, tfs_label)
    n_combos = len(VARIANTS)*len(REQUIRE_ALL_OPTS)*len(MAX_HOLDS)*len(DC_HTF_OPTS)
    print(f"sweeping {n_combos} configs * {len(syms)} symbols")
    combo_idx = 0
    for variant in VARIANTS:
        for req in REQUIRE_ALL_OPTS:
            for hold in MAX_HOLDS:
                for tfs in DC_HTF_OPTS:
                    combo_idx += 1
                    cfg_label = f"{variant}|req={req}|hold={hold}m|tfs={'+'.join(tfs)}"
                    print(f"  [{combo_idx}/{n_combos}] {cfg_label}", flush=True)
                    all_trades = []
                    for sym, d, ts in syms:
                        i_start = int(np.searchsorted(ts, start_ts))
                        i_end = len(ts)
                        trades = simulate(d, ts, i_start, i_end, variant, req, hold, tfs)
                        for t in trades: t["symbol"] = sym
                        all_trades.extend(trades)
                    if not all_trades: continue
                    pnls = np.array([t["pnl_pct"] for t in all_trades])
                    n = len(pnls); total = float(pnls.sum()); mean = float(pnls.mean())
                    sharpe = float(mean / pnls.std() * np.sqrt(252*24*20) if pnls.std() > 0 else 0)  # rough annualization
                    wr = float((pnls > 0).mean() * 100)
                    results.append({
                        "variant": variant, "require_all": req, "max_hold_min": hold, "dc_tfs": "+".join(tfs),
                        "n_trades": n, "total_pnl_pct": round(total,3), "mean_pnl_pct": round(mean,4),
                        "win_rate_pct": round(wr,1), "sharpe_rough": round(sharpe,2),
                        "median_held_bars": int(np.median([t["held_bars"] for t in all_trades])),
                        "stop_pct": round(sum(1 for t in all_trades if t["exit_kind"]=="STOP")/n*100,1),
                        "variant_pct": round(sum(1 for t in all_trades if t["exit_kind"]=="VARIANT")/n*100,1),
                        "time_pct": round(sum(1 for t in all_trades if t["exit_kind"]=="TIME")/n*100,1),
                    })

    Path("data").mkdir(exist_ok=True)
    with open("data/inf_v2_sweep.csv","w",newline="") as f:
        cols = ["variant","require_all","max_hold_min","dc_tfs","n_trades","total_pnl_pct","mean_pnl_pct","win_rate_pct","sharpe_rough","median_held_bars","stop_pct","variant_pct","time_pct"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in results: w.writerow(r)

    # rank by total_pnl_pct, but require minimum trade count
    print(f"\n=== TOP 20 CONFIGS BY TOTAL PnL (min N=20) ===")
    valid = [r for r in results if r["n_trades"] >= 20]
    valid.sort(key=lambda r: -r["total_pnl_pct"])
    print(f"{'variant':<22} {'req':<5} {'hold':>5} {'tfs':<10} {'N':>5} {'total':>8} {'mean':>7} {'WR%':>5} {'sharpe':>7} {'stop%':>5} {'var%':>5} {'time%':>5}")
    for r in valid[:20]:
        print(f"{r['variant']:<22} {str(r['require_all']):<5} {r['max_hold_min']:>3}m  {r['dc_tfs']:<10} {r['n_trades']:>5} {r['total_pnl_pct']:>+7.2f}% {r['mean_pnl_pct']:>+6.3f}% {r['win_rate_pct']:>4.1f} {r['sharpe_rough']:>6.2f} {r['stop_pct']:>4.0f} {r['variant_pct']:>4.0f} {r['time_pct']:>4.0f}")

    print(f"\n=== TOP 10 CONFIGS BY MEAN PnL/trade (min N=20) ===")
    valid_m = sorted(valid, key=lambda r: -r["mean_pnl_pct"])
    for r in valid_m[:10]:
        print(f"{r['variant']:<22} {str(r['require_all']):<5} {r['max_hold_min']:>3}m  {r['dc_tfs']:<10} N={r['n_trades']:>4}  total={r['total_pnl_pct']:>+6.2f}%  mean={r['mean_pnl_pct']:>+6.3f}%  WR={r['win_rate_pct']:>4.1f}%")

    print(f"\nrows: {len(results)}  csv: data/inf_v2_sweep.csv  elapsed: {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
