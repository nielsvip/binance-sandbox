#!/usr/bin/env python3
"""SCALP_V2 expanded sweep — adds WT_DC_SCORER, LH_LL_MULTI, REDZONE, BREAKOUT_FAIL exits.
Includes commission modeling: USDC pairs = 0% maker, USDT pairs = 0.02% maker + 0.04% taker.

48 NPZ symbols, last 30 days, same HTF DC breakout entry as baseline.
"""
import csv, time
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np

NPZ_DIR = Path("backtest_v5/indicators_3m")
LOOKBACK_DAYS = 30
REENTRY_COOLDOWN_S = 300

# Commission model: USDC = 0 maker, USDT = 0.02% maker + 0.04% taker
# Assume entry=maker, exit=taker for scalps (limit entry, market exit)
def commission_bps(symbol):
    if "USDC" in symbol: return 0.0, 4.0  # maker=0, taker=4bps
    return 2.0, 4.0  # maker=2bps, taker=4bps

# ═══ EXIT VARIANTS ═══
# Baseline (from V1 sweep)
BASELINE_VARIANTS = ["V1_WT_CONFIRM", "V8_HTF_RECLAIM"]
# New variants
NEW_VARIANTS = [
    "WT_DC_SCORER_3of5", "WT_DC_SCORER_4of5", "WT_DC_SCORER_5of5",
    "LH_LL_15m", "LH_LL_1h", "LH_LL_15m_AND_3m",
    "REDZONE_K70", "REDZONE_K80", "REDZONE_K90",
    "BREAKOUT_FAIL_15m", "BREAKOUT_FAIL_1h",
]
ALL_VARIANTS = BASELINE_VARIANTS + NEW_VARIANTS
MAX_HOLDS = [15, 60]
REQUIRE_OPTS = [True]  # baseline showed req=True is quality winner

def load_npz(fp):
    d = np.load(fp, allow_pickle=True)
    ts = d["timestamps"].astype(np.int64)
    if ts[-1] > 1e12: ts = ts // 1000
    return d, ts

def _f(d, key, default=0.0):
    if key not in d: return None
    a = d[key]
    try: return a.astype(np.float64)
    except: return None

def _s(d, key):
    if key not in d: return None
    return d[key].astype(str)

# ═══ ENTRY (fixed: HTF DC breakout) ═══
def _for(d, key1, key2):
    v = _f(d, key1)
    return v if v is not None else _f(d, key2)

def entry_arrays(d, close, require_all):
    dh15p = _for(d, "dc_high_15m_prev", "dc_high_15m")
    dl15p = _for(d, "dc_low_15m_prev", "dc_low_15m")
    dh1hp = _for(d, "dc_high_1h_prev", "dc_high_1h")
    dl1hp = _for(d, "dc_low_1h_prev", "dc_low_1h")
    if dh15p is None or dh1hp is None: return None, None
    l15 = (dh15p > 0) & (close > dh15p); l1h = (dh1hp > 0) & (close > dh1hp)
    s15 = (dl15p > 0) & (close < dl15p); s1h = (dl1hp > 0) & (close < dl1hp)
    if require_all:
        return l15 & l1h, s15 & s1h
    return l15 | l1h, s15 | s1h

# ═══ EXIT ARRAYS ═══
def exit_arrays(variant, d, close, n):
    if variant == "V1_WT_CONFIRM":
        wt1 = _f(d, "wt1_3m"); wt2 = _f(d, "wt2_3m")
        if wt1 is None: return None, None
        wt1p = np.roll(wt1, 1); wt2p = np.roll(wt2, 1)
        return (wt1p > wt2p) & (wt1 < wt2), (wt1p < wt2p) & (wt1 > wt2)
    if variant == "V8_HTF_RECLAIM":
        dh15 = _f(d, "dc_high_15m"); dl15 = _f(d, "dc_low_15m")
        if dh15 is None: return None, None
        return close < dh15, close > dl15
    # WT_DC_SCORER: N-of-5 gate (vectorized)
    if variant.startswith("WT_DC_SCORER"):
        min_cond = int(variant.split("_")[-1].replace("of5",""))
        wt_cross_bear_1h = _f(d, "wt_cross_bear_1h")
        wt_cross_bull_1h = _f(d, "wt_cross_bull_1h")
        wt1_4h = _f(d, "wt1_4h"); wt2_4h = _f(d, "wt2_4h")
        wt1_D = _f(d, "wt1_D"); wt2_D = _f(d, "wt2_D")
        k_1h = _f(d, "stoch_k_1h"); k_4h = _f(d, "stoch_k_4h")
        dc_pos_1h = _f(d, "dc_position_1h"); dc_pos_4h = _f(d, "dc_position_4h")
        if any(x is None for x in [wt_cross_bear_1h, wt1_4h, wt2_4h, wt1_D, wt2_D, k_1h, k_4h, dc_pos_1h, dc_pos_4h]):
            return None, None
        # LONG exits
        c1l = (wt_cross_bear_1h == 1); c2l = (wt1_4h < wt2_4h); c3l = (wt1_D < wt2_D)
        c4l = (k_1h >= 75) | (k_4h >= 75); c5l = (dc_pos_1h >= 0.80) | (dc_pos_4h >= 0.80)
        hits_l = c1l.astype(int) + c2l.astype(int) + c3l.astype(int) + c4l.astype(int) + c5l.astype(int)
        # SHORT exits
        c1s = (wt_cross_bull_1h == 1); c2s = (wt1_4h > wt2_4h); c3s = (wt1_D > wt2_D)
        c4s = (k_1h <= 25) | (k_4h <= 25); c5s = (dc_pos_1h <= 0.20) | (dc_pos_4h <= 0.20)
        hits_s = c1s.astype(int) + c2s.astype(int) + c3s.astype(int) + c4s.astype(int) + c5s.astype(int)
        return hits_l >= min_cond, hits_s >= min_cond
    # LH_LL on various TFs
    if variant.startswith("LH_LL_"):
        tf = variant.replace("LH_LL_","")
        if tf == "15m_AND_3m":
            h15 = _f(d,"high_15m"); h15p = _f(d,"high_15m_prev")
            l15 = _f(d,"low_15m"); l15p = _f(d,"low_15m_prev")
            h3 = _f(d,"high_3m"); h3p = _f(d,"high_3m_prev")
            l3 = _f(d,"low_3m"); l3p = _f(d,"low_3m_prev")
            if h15 is None or h3 is None: return None, None
            return (h15 < h15p) & (h3 < h3p), (l15 > l15p) & (l3 > l3p)
        h = _f(d, f"high_{tf}"); hp = _f(d, f"high_{tf}_prev")
        l = _f(d, f"low_{tf}"); lp = _f(d, f"low_{tf}_prev")
        if h is None or l is None: return None, None
        if hp is None: hp = np.roll(h, 1)
        if lp is None: lp = np.roll(l, 1)
        return h < hp, l > lp  # lower high (long exit), higher low (short exit)
    # REDZONE: stoch K crossing back from extreme
    if variant.startswith("REDZONE_K"):
        k_thresh = int(variant.split("K")[1])
        k3 = _f(d, "stoch_k_3m"); k3p = np.roll(k3, 1) if k3 is not None else None
        if k3 is None: return None, None
        long_exit = (k3p >= k_thresh) & (k3 < k_thresh)   # K fell below threshold (overbought reversal)
        short_exit = (k3p <= (100 - k_thresh)) & (k3 > (100 - k_thresh))  # K rose above mirror
        return long_exit, short_exit
    # BREAKOUT_FAIL: price falls back below DC high on given TF
    if variant.startswith("BREAKOUT_FAIL_"):
        tf = variant.replace("BREAKOUT_FAIL_","")
        dch = _f(d, f"dc_high_{tf}"); dcl = _f(d, f"dc_low_{tf}")
        if dch is None: return None, None
        return close < dch, close > dcl
    return None, None

# ═══ UNIVERSAL STOP ═══
def stop_arrays(d, close):
    lp = _f(d, "low_3m_prev"); hp = _f(d, "high_3m_prev")
    if lp is None or hp is None: return None, None
    return close <= lp, close >= hp

# ═══ SIMULATOR ═══
def simulate(d, ts, idx_start, idx_end, close, variant, require_all, max_hold_min, sym):
    le, se = entry_arrays(d, close, require_all)
    if le is None: return []
    ex_l, ex_s = exit_arrays(variant, d, close, len(close))
    if ex_l is None: return []
    us_l, us_s = stop_arrays(d, close)
    if us_l is None: return []
    max_bars = max_hold_min // 3 + 1
    maker_bps, taker_bps = commission_bps(sym)
    trades = []
    in_pos = False; is_long = True; entry_idx = -1; entry_px = 0
    last_close_ts = -10**12
    for i in range(idx_start, idx_end):
        if in_pos:
            held = i - entry_idx
            if is_long:
                stop = us_l[i]; sig = ex_l[i]
            else:
                stop = us_s[i]; sig = ex_s[i]
            timed = held >= max_bars
            if stop or sig or timed:
                if is_long:
                    pnl_gross = (close[i] - entry_px) / entry_px * 100
                else:
                    pnl_gross = (entry_px - close[i]) / entry_px * 100
                comm = (maker_bps + taker_bps) / 100  # bps to %
                pnl_net = pnl_gross - comm
                trades.append({"side":"LONG" if is_long else "SHORT",
                    "entry_ts":int(ts[entry_idx]),"exit_ts":int(ts[i]),
                    "entry":float(entry_px),"exit":float(close[i]),
                    "pnl_gross":float(pnl_gross),"pnl_net":float(pnl_net),
                    "held_bars":held,
                    "exit_kind":"STOP" if stop else "VARIANT" if sig else "TIME",
                    "comm_bps":maker_bps+taker_bps})
                in_pos = False; last_close_ts = ts[i]
        if not in_pos and (ts[i] - last_close_ts) >= REENTRY_COOLDOWN_S:
            if le[i]:
                in_pos = True; is_long = True; entry_idx = i; entry_px = float(close[i])
            elif se[i]:
                in_pos = True; is_long = False; entry_idx = i; entry_px = float(close[i])
    return trades

def main():
    t0 = time.time()
    files = sorted(NPZ_DIR.glob("*.npz"))
    syms = []
    for fp in files:
        d, ts = load_npz(fp)
        if ts.size < 100: continue
        sym = fp.stem
        close = d["close_3m"].astype(np.float64)
        syms.append((sym, d, ts, close))
    if not syms: print("no usable npz"); return
    end_ts = max(s[2][-1] for s in syms)
    start_ts = end_ts - LOOKBACK_DAYS * 86400
    print(f"window: {datetime.fromtimestamp(start_ts, tz=timezone.utc)} -> {datetime.fromtimestamp(end_ts, tz=timezone.utc)}  symbols={len(syms)}", flush=True)
    n_combos = len(ALL_VARIANTS) * len(MAX_HOLDS) * len(REQUIRE_OPTS)
    print(f"sweeping {n_combos} configs x {len(syms)} symbols", flush=True)
    results = []
    combo = 0
    for variant in ALL_VARIANTS:
        for req in REQUIRE_OPTS:
            for hold in MAX_HOLDS:
                combo += 1
                print(f"  [{combo}/{n_combos}] {variant}|req={req}|hold={hold}m", flush=True)
                all_trades = []
                for sym, d, ts, close in syms:
                    i0 = int(np.searchsorted(ts, start_ts)); i1 = len(ts)
                    trades = simulate(d, ts, i0, i1, close, variant, req, hold, sym)
                    for t in trades: t["symbol"] = sym
                    all_trades.extend(trades)
                if not all_trades: continue
                gross = np.array([t["pnl_gross"] for t in all_trades])
                net = np.array([t["pnl_net"] for t in all_trades])
                n = len(net)
                sharpe_net = float(net.mean() / net.std() * np.sqrt(252*24*20)) if net.std() > 0 else 0
                wr = float((net > 0).mean() * 100)
                avg_comm = np.mean([t["comm_bps"] for t in all_trades])
                results.append({
                    "variant": variant, "require_all": req, "max_hold_min": hold,
                    "n_trades": n,
                    "total_gross": round(float(gross.sum()),2),
                    "total_net": round(float(net.sum()),2),
                    "mean_gross": round(float(gross.mean()),4),
                    "mean_net": round(float(net.mean()),4),
                    "wr_pct": round(wr,1),
                    "sharpe_net": round(sharpe_net,2),
                    "med_held": int(np.median([t["held_bars"] for t in all_trades])),
                    "stop_pct": round(sum(1 for t in all_trades if t["exit_kind"]=="STOP")/n*100,1),
                    "var_pct": round(sum(1 for t in all_trades if t["exit_kind"]=="VARIANT")/n*100,1),
                    "avg_comm_bps": round(avg_comm,1),
                })

    Path("data").mkdir(exist_ok=True)
    with open("data/inf_v2_sweep_expanded.csv","w",newline="") as f:
        cols = ["variant","require_all","max_hold_min","n_trades","total_gross","total_net","mean_gross","mean_net","wr_pct","sharpe_net","med_held","stop_pct","var_pct","avg_comm_bps"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in results: w.writerow(r)

    print(f"\n=== TOP 20 CONFIGS BY NET TOTAL PnL (min N=20) ===")
    valid = sorted([r for r in results if r["n_trades"] >= 20], key=lambda r: -r["total_net"])
    print(f"{'variant':<26} {'hold':>5} {'N':>6} {'gross':>9} {'net':>9} {'mean_net':>9} {'WR%':>5} {'sharpe':>7} {'stop%':>5} {'var%':>5} {'comm':>5}")
    for r in valid[:20]:
        print(f"{r['variant']:<26} {r['max_hold_min']:>3}m  {r['n_trades']:>6} {r['total_gross']:>+8.1f}% {r['total_net']:>+8.1f}% {r['mean_net']:>+8.4f}% {r['wr_pct']:>4.1f} {r['sharpe_net']:>6.1f} {r['stop_pct']:>4.0f} {r['var_pct']:>4.0f} {r['avg_comm_bps']:>4.0f}")

    print(f"\n=== TOP 10 BY MEAN NET PnL/trade (min N=20) ===")
    valid_m = sorted(valid, key=lambda r: -r["mean_net"])
    for r in valid_m[:10]:
        print(f"  {r['variant']:<26} hold={r['max_hold_min']:>3}m  N={r['n_trades']:>5}  net={r['total_net']:>+7.1f}%  mean_net={r['mean_net']:>+7.4f}%  WR={r['wr_pct']:>4.1f}%  sharpe={r['sharpe_net']:>5.1f}")

    print(f"\nrows: {len(results)}  csv: data/inf_v2_sweep_expanded.csv  elapsed: {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
