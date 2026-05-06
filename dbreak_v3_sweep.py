#!/usr/bin/env python3
"""dbreak_v3_sweep — Find scalp_v3 config that handles DC/BB Daily-break events with sharpe>4.

User 2026-05-06 (1000LUNCUSDT -18% incident): SHORT position got crushed during a daily DC/BB
break-up. Need a CUSTOM v3 config that wins specifically on D-break events.

Approach:
  1. For each symbol with NPZ: find all bars where:
       - close > dc_high_D_prev  (LONG breakout — kills SHORTs)
       - close < dc_low_D_prev   (SHORT breakdown — kills LONGs)
       - close > bb_upper_D_prev (BB upper break — kills SHORTs)
       - close < bb_lower_D_prev (BB lower break — kills LONGs)
  2. For each event window (entry at break bar, exit when scalp_v3 exits or N bars later):
       - simulate scalp_v3 entry decision (LONG on lower-break, SHORT on upper-break)
       - track pnl from entry to exit
  3. Sweep scalp_v3 params (entry K thresholds, exit thresholds, hold times, side modes).
  4. Compute pool_sharpe per config restricted to D-break events only.
  5. Output configs with pool_sharpe>4 for promotion as conditional override.

Output: data/sweep_results/dbreak_v3_TS.csv  (one row per param config × side)
        data/scalp_dbreak_overrides/best_<side>.json  (highest sharpe per side)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from itertools import product
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np

NPZ_DIR = ROOT / 'backtest_v8' / 'indicators'
SWEEP_DIR = ROOT / 'data' / 'sweep_results'
OVR_DIR = ROOT / 'data' / 'scalp_dbreak_overrides'

# Crypto commission per side (0.04% = 0.02 fee + 0.02 slippage), RT in pct
COMMISSION_RT_PCT = 0.08

# Min bars after entry to evaluate exit. Exit is whichever fires first:
#  (a) v3 exit signal (k_15m extreme + bar pattern)
#  (b) max_hold_bars timeout
EXIT_MAX_HOLD_BARS = 100   # 100 × 3m = 5 hours max hold per simulated trade


def find_dbreak_events(npz: dict, n: int) -> Dict[str, np.ndarray]:
    """Return boolean masks (length n) per event type at NPZ 3m granularity."""
    close = npz['close_3m'].astype(np.float64)
    high = npz['high_3m'].astype(np.float64)
    low = npz['low_3m'].astype(np.float64)
    dc_hi_D = npz.get('dc_high_D')
    dc_lo_D = npz.get('dc_low_D')
    bb_up_D = npz.get('bb_upper_D')
    bb_lo_D = npz.get('bb_lower_D')
    out = {}
    if dc_hi_D is not None and len(dc_hi_D) == n:
        prev = np.roll(dc_hi_D, 1); prev[0] = dc_hi_D[0]
        # close > prev dc_high_D = LONG breakout above prior daily high (KILLS SHORTs)
        out['dc_break_up'] = (close > prev) & (prev > 0)
    if dc_lo_D is not None and len(dc_lo_D) == n:
        prev = np.roll(dc_lo_D, 1); prev[0] = dc_lo_D[0]
        # close < prev dc_low_D = SHORT breakdown below prior daily low (KILLS LONGs)
        out['dc_break_down'] = (close < prev) & (prev > 0)
    if bb_up_D is not None and len(bb_up_D) == n:
        prev = np.roll(bb_up_D, 1); prev[0] = bb_up_D[0]
        out['bb_break_up'] = (close > prev) & (prev > 0)
    if bb_lo_D is not None and len(bb_lo_D) == n:
        prev = np.roll(bb_lo_D, 1); prev[0] = bb_lo_D[0]
        out['bb_break_down'] = (close < prev) & (prev > 0)
    return out


def _safe(npz, key, n, default=0.0):
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def simulate_v3_on_events(sym: str, side: str, params: Dict, event_indices: np.ndarray,
                          npz: dict, n: int) -> List[Dict]:
    """For each event index, simulate v3 entry+exit; return trade records."""
    close = npz['close_3m'].astype(np.float64)
    high = npz['high_3m'].astype(np.float64)
    low = npz['low_3m'].astype(np.float64)
    k_1m = _safe(npz, 'stoch_k_1m', n, 50)
    k_3m = _safe(npz, 'stoch_k_3m', n, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    k_4h = _safe(npz, 'stoch_k_4h', n, 50)
    ts = npz['timestamps'].astype(np.int64) if 'timestamps' in npz else np.arange(n)

    # v3 entry gates (vectorized)
    K_1M_MAX = float(params.get('SCALP_V3_ENTRY_K_1M_MAX', 20))
    K_3M_MAX = float(params.get('SCALP_V3_ENTRY_K_3M_MAX', 40))
    K_15M_MAX = float(params.get('SCALP_V3_ENTRY_K_15M_MAX', 65))
    K_1H_MAX = float(params.get('SCALP_V3_ENTRY_K_1H_MAX', 80))
    K_4H_MAX = float(params.get('SCALP_V3_ENTRY_K_4H_MAX', 85))
    SHORT_K_3M_MAX = float(params.get('SCALP_V3_SHORT_ENTRY_K_3M_MAX', 15))
    SHORT_K_15M_MAX = float(params.get('SCALP_V3_SHORT_ENTRY_K_15M_MAX', 30))
    SHORT_K_1H_MAX = float(params.get('SCALP_V3_SHORT_ENTRY_K_1H_MAX', 40))
    EXIT_K_15M_MIN = float(params.get('SCALP_V3_EXIT_15M_K_MIN', 95))
    MAX_HOLD_MIN = float(params.get('SCALP_V3_MAX_HOLD_MIN', 5.0))
    max_hold_bars = max(1, int(MAX_HOLD_MIN / 3.0))

    if side == 'LONG':
        entry_ok = ((k_1m < K_1M_MAX) & (k_3m < K_3M_MAX) & (k_15m < K_15M_MAX)
                    & (k_1h < K_1H_MAX) & (k_4h < K_4H_MAX))
        exit_ok = (k_15m > EXIT_K_15M_MIN)
    else:
        entry_ok = ((k_1m > (100 - K_1M_MAX)) & (k_3m > (100 - SHORT_K_3M_MAX))
                    & (k_15m > (100 - SHORT_K_15M_MAX)) & (k_1h > (100 - SHORT_K_1H_MAX)))
        exit_ok = (k_15m < (100 - EXIT_K_15M_MIN))

    trades = []
    for ev_i in event_indices:
        ev_i = int(ev_i)
        if ev_i + 1 >= n: continue
        if not entry_ok[ev_i]: continue  # v3 wouldn't enter at this event bar
        ep = float(close[ev_i])
        if ep <= 0: continue
        # Find exit
        x_end = min(ev_i + max_hold_bars, n - 1)
        x_window = exit_ok[ev_i + 1:x_end + 1]
        x_idxs = np.flatnonzero(x_window)
        if len(x_idxs):
            xi = ev_i + 1 + int(x_idxs[0])
        else:
            xi = x_end
        xp = float(close[xi])
        if xp <= 0: continue
        if side == 'LONG':
            pnl_gross = (xp - ep) / ep * 100.0
        else:
            pnl_gross = (ep - xp) / ep * 100.0
        pnl_net = pnl_gross - COMMISSION_RT_PCT
        trades.append({
            'sym': sym, 'side': side, 'event_i': ev_i, 'exit_i': xi,
            'entry_ts': int(ts[ev_i]), 'exit_ts': int(ts[xi]),
            'entry_price': ep, 'exit_price': xp,
            'pnl_pct': pnl_net, 'bars_held': xi - ev_i,
        })
    return trades


def event_pool_for_param(param_set: Dict, syms: List[str], event_type: str) -> Dict:
    """Pool trades across syms for one param set on given event type. Return metrics."""
    # Determine side based on event_type:
    #   dc_break_up / bb_break_up = LONG-favorable breakout (so SHORT positions die; we test LONG entry)
    #   dc_break_down / bb_break_down = SHORT-favorable breakdown (so LONG positions die; test SHORT entry)
    # But user wants to find what WORKS during these events — so test the side that profits.
    if event_type.endswith('_up'):
        test_side = 'LONG'    # buy the breakout
    else:
        test_side = 'SHORT'   # short the breakdown
    all_trades = []
    for sym in syms:
        p = NPZ_DIR / f'{sym}.npz'
        if not p.exists(): continue
        try:
            z = np.load(str(p))
            full = {k: z[k][:] for k in z.files}
            z.close()
        except Exception:
            continue
        n = len(full.get('close_3m', []))
        if n < 1000: continue
        events = find_dbreak_events(full, n)
        ev_mask = events.get(event_type)
        if ev_mask is None or not ev_mask.any(): continue
        ev_idxs = np.flatnonzero(ev_mask)
        # Subsample if too many — cap at 500 events per sym for speed
        if len(ev_idxs) > 500:
            ev_idxs = np.random.choice(ev_idxs, 500, replace=False)
        trades = simulate_v3_on_events(sym, test_side, param_set, ev_idxs, full, n)
        all_trades.extend(trades)
    if not all_trades:
        return {'event_type': event_type, 'side': test_side, 'trades': 0, 'pool_sharpe': 0.0,
                'wr_pct': 0.0, 'avg_pnl': 0.0, 'total_pct': 0.0, 'max_dd_pct': 0.0,
                'n_syms': 0, 'params': param_set}
    rets = np.array([t['pnl_pct'] for t in all_trades], dtype=np.float64)
    n_t = len(rets)
    sd = float(rets.std())
    pool = float(rets.mean() / sd) if sd > 1e-12 else 0.0
    wr = float((rets > 0).mean() * 100.0)
    eq = np.cumsum(rets); peak = np.maximum.accumulate(eq); dd = float((peak - eq).max())
    return {
        'event_type': event_type, 'side': test_side, 'trades': n_t,
        'pool_sharpe': pool, 'wr_pct': wr, 'avg_pnl': float(rets.mean()),
        'total_pct': float(rets.sum()), 'max_dd_pct': dd,
        'n_syms': len(set(t['sym'] for t in all_trades)),
        'params': param_set,
    }


def build_param_grid() -> List[Dict]:
    """Sweep grid over key v3 entry/exit params. Marginal expansion (~200 variants)."""
    base = {
        'SCALP_V3_ENTRY_K_1M_MAX': 20, 'SCALP_V3_ENTRY_K_3M_MAX': 40,
        'SCALP_V3_ENTRY_K_15M_MAX': 65, 'SCALP_V3_ENTRY_K_1H_MAX': 80,
        'SCALP_V3_ENTRY_K_4H_MAX': 85,
        'SCALP_V3_SHORT_ENTRY_K_3M_MAX': 15, 'SCALP_V3_SHORT_ENTRY_K_15M_MAX': 30,
        'SCALP_V3_SHORT_ENTRY_K_1H_MAX': 40,
        'SCALP_V3_EXIT_15M_K_MIN': 95, 'SCALP_V3_MAX_HOLD_MIN': 5.0,
    }
    grid = [dict(base)]
    def add(**kwargs):
        d = dict(base); d.update(kwargs); grid.append(d)
    # Sweep K thresholds
    for v in (10, 15, 20, 25, 30, 40, 50, 70):
        add(SCALP_V3_ENTRY_K_1M_MAX=v)
    for v in (15, 25, 35, 45, 55, 65):
        add(SCALP_V3_ENTRY_K_3M_MAX=v)
    for v in (40, 50, 60, 70, 80):
        add(SCALP_V3_ENTRY_K_15M_MAX=v)
    for v in (60, 70, 80, 90):
        add(SCALP_V3_ENTRY_K_1H_MAX=v)
    for v in (5, 10, 15, 20, 30, 40):
        add(SCALP_V3_SHORT_ENTRY_K_3M_MAX=v)
    for v in (10, 20, 30, 40, 50):
        add(SCALP_V3_SHORT_ENTRY_K_15M_MAX=v)
    for v in (75, 85, 90, 95, 98):
        add(SCALP_V3_EXIT_15M_K_MIN=v)
    for v in (3.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0):
        add(SCALP_V3_MAX_HOLD_MIN=v)
    # Some 2-param combos
    for k1, ex in product([15, 25, 35], [80, 90, 95]):
        add(SCALP_V3_ENTRY_K_1M_MAX=k1, SCALP_V3_EXIT_15M_K_MIN=ex)
    for sk, mh in product([10, 20, 35], [5.0, 15.0, 60.0]):
        add(SCALP_V3_SHORT_ENTRY_K_3M_MAX=sk, SCALP_V3_MAX_HOLD_MIN=mh)
    return grid


def _worker(args):
    param_set, syms, event_type = args
    try:
        return event_pool_for_param(param_set, syms, event_type)
    except Exception:
        return {'event_type': event_type, 'error': traceback.format_exc()[-300:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms', default='', help='comma-sep (default: all NPZ syms)')
    ap.add_argument('--event-types', default='dc_break_up,dc_break_down,bb_break_up,bb_break_down')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--target-sharpe', type=float, default=4.0)
    args = ap.parse_args()

    # Symbol universe
    if args.syms:
        syms = [s.strip() for s in args.syms.split(',') if s.strip()]
    else:
        syms = sorted(p.stem for p in NPZ_DIR.glob('*.npz'))
    print(f"[dbreak_v3_sweep] {len(syms)} syms, events={args.event_types}, workers={args.workers}", flush=True)

    grid = build_param_grid()
    event_types = [e.strip() for e in args.event_types.split(',') if e.strip()]
    work = [(p, syms, et) for et in event_types for p in grid]
    print(f"[dbreak_v3_sweep] {len(work)} jobs ({len(grid)} configs × {len(event_types)} event types)", flush=True)

    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    OVR_DIR.mkdir(parents=True, exist_ok=True)
    ts_run = int(time.time())
    csv_path = SWEEP_DIR / f'dbreak_v3_{ts_run}.csv'
    csv_f = csv_path.open('w', newline='')
    csv_w = None

    results = []
    t0 = time.time()
    if args.workers <= 1:
        for i, w in enumerate(work, 1):
            r = _worker(w)
            results.append(r)
            if csv_w is None and 'pool_sharpe' in r:
                csv_w = csv.DictWriter(csv_f, fieldnames=[k for k in r.keys() if k != 'params'] + ['params_json'])
                csv_w.writeheader()
            if csv_w and 'pool_sharpe' in r:
                row = {k: v for k, v in r.items() if k != 'params'}
                row['params_json'] = json.dumps(r.get('params', {}))
                csv_w.writerow(row); csv_f.flush()
            if i % 20 == 0:
                print(f"  [{i}/{len(work)}] elapsed={time.time()-t0:.0f}s", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_worker, w): w for w in work}
            done = 0
            for fut in as_completed(futs):
                done += 1
                try: r = fut.result()
                except Exception as e: print(f"  worker err: {e}", flush=True); continue
                results.append(r)
                if csv_w is None and 'pool_sharpe' in r:
                    csv_w = csv.DictWriter(csv_f, fieldnames=[k for k in r.keys() if k != 'params'] + ['params_json'])
                    csv_w.writeheader()
                if csv_w and 'pool_sharpe' in r:
                    row = {k: v for k, v in r.items() if k != 'params'}
                    row['params_json'] = json.dumps(r.get('params', {}))
                    csv_w.writerow(row); csv_f.flush()
                if done % 20 == 0:
                    print(f"  [{done}/{len(work)}] elapsed={time.time()-t0:.0f}s", flush=True)
    csv_f.close()
    elapsed = time.time() - t0

    # Top by pool_sharpe per event_type
    print()
    print(f"[dbreak_v3_sweep] cycle done in {elapsed:.0f}s")
    print(f"[dbreak_v3_sweep] CSV: {csv_path}")
    by_event: Dict[str, List[Dict]] = {}
    for r in results:
        if 'pool_sharpe' not in r: continue
        by_event.setdefault(r['event_type'], []).append(r)
    promoted = {}
    for et, rs in by_event.items():
        rs.sort(key=lambda x: -x['pool_sharpe'])
        print(f"\n=== {et} top 5 ===")
        for r in rs[:5]:
            print(f"  pool={r['pool_sharpe']:+.4f}  wr={r['wr_pct']:5.1f}%  tr={r['trades']:>5d}  avg={r['avg_pnl']:+.4f}%  dd={r['max_dd_pct']:5.2f}%  syms={r['n_syms']}")
        # Promote winner if sharpe > target
        if rs and rs[0]['pool_sharpe'] >= args.target_sharpe:
            best = rs[0]
            ovr_path = OVR_DIR / f'best_{et}.json'
            ovr_path.write_text(json.dumps({
                '_meta': f"dbreak_v3 winner for {et}: pool={best['pool_sharpe']:+.4f} wr={best['wr_pct']:.1f}% tr={best['trades']} dd={best['max_dd_pct']:.2f}% syms={best['n_syms']}",
                'event_type': et, 'side': best['side'],
                'pool_sharpe': best['pool_sharpe'], 'wr_pct': best['wr_pct'],
                'trades': best['trades'], 'max_dd_pct': best['max_dd_pct'],
                'overrides': best['params'],
            }, indent=2))
            promoted[et] = ovr_path
            print(f"  ✅ PROMOTED {et} (pool>={args.target_sharpe}) → {ovr_path}")
        elif rs:
            print(f"  ❌ best pool={rs[0]['pool_sharpe']:+.4f} < target={args.target_sharpe}")
    print()
    if promoted:
        print(f"Promoted {len(promoted)} event-type winners → {OVR_DIR}")
    else:
        print(f"No configs reached target sharpe ≥ {args.target_sharpe}.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
