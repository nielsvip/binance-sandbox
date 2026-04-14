#!/usr/bin/env python3
"""Massive entry × exit sweep on ranked stocks. Tests millions of combinations.
Entry: every indicator threshold at every TF
Exit: fixed TP, WT cross at various TFs with/without confirmation
STRICT_NO_LOSS throughout."""
import json, numpy as np, sys, time, math
from datetime import datetime, timezone
from pathlib import Path
from itertools import product

sys.path.insert(0, str(Path(__file__).parent))
import backtest_engine
backtest_engine.KLINES_DIR = Path('/Users/niels/Documents/binance/klines_cache/tradier')
from backtest_engine import precompute_real_indicators

tradier_dir = Path('/Users/niels/Documents/binance/data/tradier')
FEE = 0.0005

def get_ranking(kind, ts_target):
    best = None; best_dist = 999999
    for f in tradier_dir.glob(f'{kind}_*.json'):
        try:
            ts = int(f.stem.split('_')[-1])
            if abs(ts - ts_target) < best_dist and abs(ts - ts_target) < 172800:
                best_dist = abs(ts - ts_target); best = f
        except: pass
    if not best: return []
    data = json.loads(best.read_text())
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return [d['symbol'] for d in data[:8]]
    elif isinstance(data, list): return data[:8]
    return []

# Test dates (market open times in UTC)
TEST_DATES = [
    datetime(2026,3,2,14,30, tzinfo=timezone.utc),
    datetime(2026,3,9,14,30, tzinfo=timezone.utc),
    datetime(2026,3,15,14,30, tzinfo=timezone.utc),
]

# ENTRY CONDITIONS to test
ENTRY_CONDITIONS = [
    # (name, tf, indicator_key, direction, threshold)
    ("DC_pos_1h<0.15", "1h", "dc_position_1h", "below", 0.15),
    ("DC_pos_1h<0.25", "1h", "dc_position_1h", "below", 0.25),
    ("DC_pos_1h<0.35", "1h", "dc_position_1h", "below", 0.35),
    ("WT1_1h<-50", "1h", "wt1_1h", "below", -50),
    ("WT1_1h<-30", "1h", "wt1_1h", "below", -30),
    ("WT1_1h<0", "1h", "wt1_1h", "below", 0),
    ("WT_cross_bull_1h", "1h", "wt_cross_bull_1h", "above", 0.5),
    ("MFI_1h<30", "1h", "mfi_1h", "below", 30),
    ("MFI_1h<40", "1h", "mfi_1h", "below", 40),
    ("RSI_1h<30", "1h", "rsi_1h", "below", 30),
    ("RSI_1h<40", "1h", "rsi_1h", "below", 40),
    ("K_1h<20", "1h", "stoch_k_1h", "below", 20),
    ("K_1h<35", "1h", "stoch_k_1h", "below", 35),
    ("K_cross_1h", "1h", "stoch_crossover_1h", "above", 0.5),
    ("ATR_calm_1h", "1h", "atr_1h", "below_pct10", None),  # Bottom 10% ATR
    ("BB_pctb_1h<0.1", "1h", "bb_pct_b_1h", "below", 0.1),
    ("BB_pctb_1h<0.2", "1h", "bb_pct_b_1h", "below", 0.2),
    # 15m entries
    ("DC_pos_15m<0.15", "15m", "dc_position_15m", "below", 0.15),
    ("DC_pos_15m<0.25", "15m", "dc_position_15m", "below", 0.25),
    ("WT_cross_bull_15m", "15m", "wt_cross_bull_15m", "above", 0.5),
    ("MFI_15m<30", "15m", "mfi_15m", "below", 30),
    ("K_15m<20", "15m", "stoch_k_15m", "below", 20),
    # 5m entries
    ("WT_cross_bull_5m", "5m", "wt_cross_bull_5m", "above", 0.5),
    ("K_5m<20", "5m", "stoch_k_5m", "below", 20),
    # ANY dip (no indicator, just lowest price in scan window)
    ("LOWEST_DIP_5d", "1h", None, "lowest", 30),
]

# EXIT CONDITIONS to test
EXIT_CONDITIONS = [
    ("TP_0.5%", "fixed_tp", 0.005),
    ("TP_1.0%", "fixed_tp", 0.010),
    ("TP_1.5%", "fixed_tp", 0.015),
    ("TP_2.0%", "fixed_tp", 0.020),
    ("TP_3.0%", "fixed_tp", 0.030),
    ("WT_5m", "wt_cross", "5m", None),
    ("WT_15m", "wt_cross", "15m", None),
    ("WT_1h", "wt_cross", "1h", None),
    ("WT_1h+4h", "wt_cross", "1h", "4h"),
    ("WT_15m+1h", "wt_cross", "15m", "1h"),
    ("K_cross_5m", "k_cross", "5m"),
    ("K_cross_15m", "k_cross", "15m"),
    ("K_cross_1h", "k_cross", "1h"),
    ("MFI_flip_1h", "mfi_flip", "1h"),
]

print(f"Entry conditions: {len(ENTRY_CONDITIONS)}")
print(f"Exit conditions: {len(EXIT_CONDITIONS)}")
print(f"Test dates: {len(TEST_DATES)}")
print(f"Combinations per stock: {len(ENTRY_CONDITIONS) * len(EXIT_CONDITIONS)}")

all_results = {}
start_time = time.time()

for test_dt in TEST_DATES:
    ts_start = int(test_dt.timestamp())
    winners = get_ranking('winners_30r', ts_start)
    losers = get_ranking('losers_30r', ts_start)
    date_str = test_dt.strftime('%m/%d')

    for side, syms in [('LONG', winners), ('SHORT', losers)]:
        is_long = side == 'LONG'
        for sym in syms:
            # Precompute ALL TFs
            tfs = {}
            for tf in ['5m', '15m', '1h', '4h']:
                pre = precompute_real_indicators(sym, tf)
                if pre: tfs[tf] = pre
            if '1h' not in tfs: continue

            # Get 1h arrays
            pre_1h = tfs['1h']
            c_1h = pre_1h['indicators'].get('current_price')
            if c_1h is None or not isinstance(c_1h, np.ndarray): continue
            h_1h = pre_1h['indicators'].get('high_1h', c_1h)
            l_1h = pre_1h['indicators'].get('low_1h', c_1h)
            ts_1h = pre_1h['df']['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64)
            n_1h = len(c_1h)
            si_1h = np.searchsorted(ts_1h, ts_start, side='right')
            if si_1h >= n_1h - 10: continue

            # Test each ENTRY condition
            for entry_name, entry_tf, entry_key, entry_dir, entry_th in ENTRY_CONDITIONS:
                if entry_tf not in tfs: continue
                entry_pre = tfs[entry_tf]
                entry_ind = entry_pre['indicators']
                entry_c = entry_ind.get('current_price')
                if entry_c is None: continue
                entry_ts = entry_pre['df']['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64)
                ei_start = np.searchsorted(entry_ts, ts_start, side='right')
                n_entry = len(entry_c)
                scan_end = min(ei_start + 200, n_entry)  # Scan first ~week

                # Find entry bar
                entry_idx = None
                if entry_key is None and entry_dir == "lowest":
                    # Just find lowest/highest price
                    scan_bars = min(int(entry_th), scan_end - ei_start)
                    if is_long:
                        entry_idx = ei_start + int(np.argmin(entry_c[ei_start:ei_start+scan_bars]))
                    else:
                        entry_idx = ei_start + int(np.argmax(entry_c[ei_start:ei_start+scan_bars]))
                else:
                    arr = entry_ind.get(entry_key)
                    if arr is None or not isinstance(arr, np.ndarray): continue
                    if entry_dir == "below_pct10":
                        valid = arr[ei_start:scan_end]
                        valid_clean = valid[~np.isnan(valid.astype(float))] if valid.dtype != object else valid
                        if len(valid_clean) < 10: continue
                        th = np.percentile(valid_clean.astype(float), 10)
                        for bi in range(ei_start, scan_end):
                            v = float(arr[bi]) if isinstance(arr[bi], (int, float, np.floating)) else None
                            if v is not None and v < th:
                                if is_long: entry_idx = bi; break
                    elif entry_dir == "below":
                        for bi in range(ei_start, scan_end):
                            v = float(arr[bi]) if isinstance(arr[bi], (int, float, np.floating)) else None
                            if v is not None and ((is_long and v < entry_th) or (not is_long and v > (100 - entry_th if entry_th < 50 else entry_th))):
                                entry_idx = bi; break
                    elif entry_dir == "above":
                        for bi in range(ei_start, scan_end):
                            v = float(arr[bi]) if isinstance(arr[bi], (int, float, np.floating)) else None
                            if v is not None and v > entry_th:
                                if is_long: entry_idx = bi; break
                                elif not is_long: entry_idx = bi; break

                if entry_idx is None: continue
                ep = float(entry_c[entry_idx])
                if ep <= 0: continue
                entry_ts_val = int(entry_ts[entry_idx])

                # Test each EXIT condition
                for exit_info in EXIT_CONDITIONS:
                    exit_name = exit_info[0]
                    exit_type = exit_info[1]

                    # Map to exit TF
                    if exit_type == "fixed_tp":
                        tp = exit_info[2]
                        # Use 1h bars for TP check
                        ei_1h = np.searchsorted(ts_1h, entry_ts_val, side='right')
                        mg = 0.0; xr = None
                        for bi in range(ei_1h, n_1h):
                            _h = float(h_1h[bi]); _l = float(l_1h[bi]); _c = float(c_1h[bi])
                            hp = (_h - ep) / ep if is_long else (ep - _l) / ep
                            pnl = (_c - ep) / ep if is_long else (ep - _c) / ep
                            mg = max(mg, hp)
                            if hp >= tp: xr = tp - 2*FEE; break
                            elif mg >= 0.005 and pnl <= 0.003: xr = pnl - 2*FEE; break

                    elif exit_type == "wt_cross":
                        exit_tf = exit_info[2]
                        confirm_tf = exit_info[3] if len(exit_info) > 3 else None
                        if exit_tf not in tfs: continue
                        exit_pre = tfs[exit_tf]
                        wk = f'wt_cross_bear_{exit_tf}' if is_long else f'wt_cross_bull_{exit_tf}'
                        wt = exit_pre['indicators'].get(wk)
                        if wt is None: continue
                        exit_c = exit_pre['indicators'].get('current_price')
                        exit_h = exit_pre['indicators'].get(f'high_{exit_tf}', exit_c)
                        exit_l = exit_pre['indicators'].get(f'low_{exit_tf}', exit_c)
                        exit_ts_arr = exit_pre['df']['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64)
                        ei_exit = np.searchsorted(exit_ts_arr, entry_ts_val, side='right')

                        conf_wt = None
                        if confirm_tf and confirm_tf in tfs:
                            ck = f'wt_cross_bear_{confirm_tf}' if is_long else f'wt_cross_bull_{confirm_tf}'
                            ca = tfs[confirm_tf]['indicators'].get(ck)
                            if ca is not None:
                                ct = tfs[confirm_tf]['df']['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64)
                                cm = np.searchsorted(ct, exit_ts_arr, side='right') - 1
                                np.clip(cm, 0, len(ca)-1, out=cm)
                                conf_wt = ca[cm]

                        mg = 0.0; xr = None
                        for bi in range(ei_exit + 1, len(exit_c)):
                            _c = float(exit_c[bi]) if isinstance(exit_c[bi], (int,float,np.floating)) else 0
                            _h = float(exit_h[bi]) if isinstance(exit_h[bi], (int,float,np.floating)) else _c
                            _l = float(exit_l[bi]) if isinstance(exit_l[bi], (int,float,np.floating)) else _c
                            if _c <= 0: continue
                            hp = (_h - ep) / ep if is_long else (ep - _l) / ep
                            pnl = (_c - ep) / ep if is_long else (ep - _c) / ep
                            mg = max(mg, hp)
                            wf = float(wt[bi]) > 0 if bi < len(wt) else False
                            cf = conf_wt is None or (bi < len(conf_wt) and float(conf_wt[bi]) > 0)
                            if pnl > 0.003 and wf and cf: xr = pnl - 2*FEE; break
                            if mg >= 0.005 and pnl <= 0.003: xr = pnl - 2*FEE; break

                    elif exit_type == "k_cross":
                        exit_tf = exit_info[2]
                        if exit_tf not in tfs: continue
                        kk = f'stoch_crossunder_{exit_tf}' if is_long else f'stoch_crossover_{exit_tf}'
                        ka = tfs[exit_tf]['indicators'].get(kk)
                        if ka is None: continue
                        exit_c = tfs[exit_tf]['indicators'].get('current_price')
                        exit_h = tfs[exit_tf]['indicators'].get(f'high_{exit_tf}', exit_c)
                        exit_l = tfs[exit_tf]['indicators'].get(f'low_{exit_tf}', exit_c)
                        exit_ts_arr = tfs[exit_tf]['df']['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64)
                        ei_exit = np.searchsorted(exit_ts_arr, entry_ts_val, side='right')
                        mg = 0.0; xr = None
                        for bi in range(ei_exit + 1, len(exit_c)):
                            _c = float(exit_c[bi]) if isinstance(exit_c[bi], (int,float,np.floating)) else 0
                            _h = float(exit_h[bi]) if isinstance(exit_h[bi], (int,float,np.floating)) else _c
                            _l = float(exit_l[bi]) if isinstance(exit_l[bi], (int,float,np.floating)) else _c
                            if _c <= 0: continue
                            hp = (_h - ep) / ep if is_long else (ep - _l) / ep
                            pnl = (_c - ep) / ep if is_long else (ep - _c) / ep
                            mg = max(mg, hp)
                            if pnl > 0.003 and float(ka[bi]) > 0: xr = pnl - 2*FEE; break
                            if mg >= 0.005 and pnl <= 0.003: xr = pnl - 2*FEE; break

                    elif exit_type == "mfi_flip":
                        exit_tf = exit_info[2]
                        if exit_tf not in tfs: continue
                        mfi_arr = tfs[exit_tf]['indicators'].get(f'mfi_{exit_tf}')
                        if mfi_arr is None: continue
                        exit_c = tfs[exit_tf]['indicators'].get('current_price')
                        exit_ts_arr = tfs[exit_tf]['df']['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64)
                        ei_exit = np.searchsorted(exit_ts_arr, entry_ts_val, side='right')
                        mg = 0.0; xr = None
                        for bi in range(ei_exit + 1, len(exit_c)):
                            _c = float(exit_c[bi]) if isinstance(exit_c[bi], (int,float,np.floating)) else 0
                            if _c <= 0: continue
                            pnl = (_c - ep) / ep if is_long else (ep - _c) / ep
                            hp = pnl  # Simplified
                            mg = max(mg, hp)
                            mfi_val = float(mfi_arr[bi]) if isinstance(mfi_arr[bi], (int,float,np.floating)) else 50
                            mfi_exit = (is_long and mfi_val > 70) or (not is_long and mfi_val < 30)
                            if pnl > 0.003 and mfi_exit: xr = pnl - 2*FEE; break
                            if mg >= 0.005 and pnl <= 0.003: xr = pnl - 2*FEE; break

                    if xr is not None:
                        combo_key = f"{entry_name}→{exit_name}"
                        if combo_key not in all_results:
                            all_results[combo_key] = []
                        all_results[combo_key].append(xr)

elapsed = time.time() - start_time
print(f"\nDone in {elapsed:.0f}s — {len(all_results)} entry×exit combos tested")

# Rank by average return
ranked = [(k, np.mean(v), np.max(v), np.mean(np.array(v) > 0) * 100, len(v)) for k, v in all_results.items() if len(v) >= 3]
ranked.sort(key=lambda x: x[1], reverse=True)

print(f"\n{'='*90}")
print(f"{'Entry → Exit':55s} {'Avg Ret':>8s} {'Max Ret':>8s} {'WR':>6s} {'N':>4s}")
print(f"{'='*90}")
for name, avg, mx, wr, n in ranked[:40]:
    print(f"{name:55s} {avg*100:>+7.3f}% {mx*100:>+7.3f}% {wr:>5.1f}% {n:>4d}")

print(f"\n{'--- WORST 15 ---':55s}")
for name, avg, mx, wr, n in ranked[-15:]:
    print(f"{name:55s} {avg*100:>+7.3f}% {mx*100:>+7.3f}% {wr:>5.1f}% {n:>4d}")
