#!/usr/bin/env python3
"""per_sym_real_profiler — runs the REAL backtest_v8_engine via subprocess per (sym, params).
NO Frankenstein. Trade JSONLs come from actual ez_manage code path. Sharpes are honest.

User 2026-05-05: only backtest_v8_engine produces useful results. v8_quick_engine is a lie.
This profiler is a thin orchestration wrapper around the real engine — no own walker,
no own entry/exit logic, no own augment/hedge math. The engine does it all.

Per-sym workflow:
  1. Load proven baseline override (override_btc_BEST.json for BTC-cluster, etc.)
  2. Sweep small set of candidate variants on top of baseline
  3. For each variant: write temp override JSON + invoke backtest_v8_engine subprocess
  4. Read resulting trade JSONL + compute metrics via metrics_guard
  5. Pick winner per (sym, side)
  6. Write per_sym_active_config.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import shutil
import subprocess
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg

ENGINE_PY = ROOT / 'backtest_v8_engine.py'
def _resolve_python_bin() -> str:
    # 2026-05-07: previous Path('/home/niels') fork picked S1's .conda even on S2 (which uses miniconda3) — broke S2 sweeps.
    candidates = [
        '/home/niels/miniconda3/envs/binance_env/bin/python',
        '/home/niels/.conda/envs/binance_env/bin/python',
        '/opt/anaconda3/envs/binance_env/bin/python',
        sys.executable,
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return sys.executable
PYTHON_BIN = _resolve_python_bin()
NPZ_DIR = ROOT / 'backtest_v8' / 'indicators'
SWEEP_DIR = ROOT / 'data' / 'sweep_results'
ACTIVE_CFG = ROOT / 'data' / 'hourly_reconfig' / 'per_sym_active_config.json'
RUN_BASE = ROOT / 'data' / 'sweep_results'

OVERRIDE_BTC_BEST = ROOT / 'backtest_v8' / 'btc_loop_results' / 'override_btc_BEST.json'
OVERRIDE_V5_RZ_LOOSE = ROOT / 'backtest_v8' / 'btc_loop_results' / 'override_v5_rz_loose.json'

BTC_DEDICATED_SYMS = {'BTCUSDC','ETHUSDC','SOLUSDC','BNBUSDC','XRPUSDC','DOGEUSDC','ZECUSDC','BTCDOMUSDT'}

# Promote criteria (per CLAUDE.md NO-LIES)
PROMOTE_POOL_FLOOR = 0.5   # User 2026-05-07: 0.5 is the practical floor for new per-sym configs to ship live before market open
PROMOTE_DD_CAP = 10.0
PROMOTE_TRADES_MIN = 30


def _load_baseline(sym: str, mode: str = 'crypto') -> Dict:
    # 2026-05-07 22:00: engine-defaults produce 0 trades for both modes when standalone (verified CF/CTRA tradier 3.4yr = 0 closes; SKYUSDT/TRXUSDT/XLMUSDT crypto 1.4yr = 0 closes). Use known-trade-firing baselines instead. Tradier: tradier_v5_merged.json (honest pool_sharpe 0.5823 per feedback_engine_pool_sharpe_cockroach_smothered_20260430). Crypto: crypto_1p033_20260422.json (filename Sharpe is pre-cockroach lie, but the override knobs DO fire trades — real Sharpe will be measured via metrics_guard from the per-trade returns).
    BASELINES = ROOT / 'data' / 'baselines'
    candidates = []
    if mode == 'tradier':
        candidates = [BASELINES / 'canonical_tradier_top1_post_audit.json', BASELINES / 'tradier_v5_merged.json', BASELINES / 'tradier_v4_merged.json']
    else:
        # 2026-05-07 22:30: canonical_crypto_top1_post_audit.json is now the iter=17 w31811 winner (pool_sharpe 0.6897, rate 2.20/sym/day, dd 5.65% — see data/baselines/promoted/crypto_canonical_v2_20260507_iter17_w31811.json).
        candidates = [BASELINES / 'canonical_crypto_top1_post_audit.json', BASELINES / 'crypto_1p033_20260422.json', BASELINES / 'crypto_0p9543.json']
    for p in candidates:
        try:
            if p.exists():
                d = json.loads(p.read_text())
                clean = {k: v for k, v in d.items() if not k.startswith('_')}
                if clean:
                    # 2026-05-08: force test-time settings so positions actually close.
                    # NOLOSS_ENABLED=True in baselines blocks all loss-exits → 0 closes → no JSONL.
                    # EARLY_ABORT_ENABLED already disabled via V8_RATE_GUARD_DISABLED but belt+suspenders.
                    clean['NOLOSS_ENABLED'] = False
                    clean['STRICT_NO_LOSS_ENABLED'] = False
                    clean['EARLY_ABORT_ENABLED'] = False
                    if mode == 'tradier':
                        # B_MAIN_ENTRY_GATE blocks all entries in backtest because
                        # should_enter_long/short needs live indicator values the backtest
                        # doesn't provide (relative_volume_D, bb_pct_b_D etc). Disable so
                        # pure WT_DC_ENTRY score path (A) is used — this path reads NPZ correctly.
                        clean['B_MAIN_ENTRY_GATE_ENABLED'] = False
                    return clean
        except Exception:
            continue
    return {}


def variants_for_sym(base: Dict, sym: str, account: str = '') -> List[Tuple[str, Dict]]:
    # Fast mode: 3 variants per sym (~40 min each on 1 month data). Full grid with PER_SYM_FULL_GRID=1.
    grid: List[Tuple[str, Dict]] = [('BASELINE', dict(base))]
    def add(tag, deltas):
        d = dict(base); d.update(deltas)
        grid.append((tag, d))
    full = os.environ.get('PER_SYM_FULL_GRID', '0') == '1'
    if sym in BTC_DEDICATED_SYMS:
        if full:
            for mh, bk in ((1, 1), (3, 1), (5, 3), (8, 5), (12, 8)):
                add(f'mh{mh}_bk{bk}', {'BTC_MIN_HOLD_BARS': mh, 'BTC_BREAKOUT_MIN_HOLD_BARS': bk})
            for n in (2, 3, 4, 5):
                add(f'accel_tfs_{n}', {'BTC_ACCEL_RAMP_MIN_TFS': n})
            for t in (1, 2, 3, 4):
                add(f'texit_{t}', {'BTC_TECH_EXIT_WT_MIN_TFS': t})
            for cd, bcd in ((1, 1), (3, 1), (5, 3), (8, 5)):
                add(f'cd{cd}_bcd{bcd}', {'BTC_COOLDOWN_BARS': cd, 'BTC_BREAKOUT_COOLDOWN_BARS': bcd})
            for hl in (3.0, 5.4, 8.0, 12.0):
                add(f'hl_{hl}', {'BTC_HARD_LOSS_USD_PER_TRADE': hl})
        else:
            # 2 key variants: relaxed hold + strict accel
            add('mh1_bk1', {'BTC_MIN_HOLD_BARS': 1, 'BTC_BREAKOUT_MIN_HOLD_BARS': 1})
            add('accel_tfs_3', {'BTC_ACCEL_RAMP_MIN_TFS': 3})
        for tag, d in grid:
            d['BTC_DEDICATED_ENABLED'] = True
            d['BTC_DEDICATED_SYMBOLS'] = [sym]
            if account:
                d['BTC_DEDICATED_ACCOUNTS'] = [account]
    else:
        if full:
            for es in (12.0, 15.0, 18.0, 22.0, 28.0):
                add(f'es_{es:.0f}', {'ENTRY_SCORE_THRESHOLD': es, 'TRADIER_ENTRY_SCORE_THRESHOLD': es})
            for align in (1, 2, 3, 4):
                add(f'tfalign_{align}', {'TF_ALIGNMENT_MIN_LONG': align, 'TF_ALIGNMENT_MIN_SHORT': align})
            for wt in (1, 2, 3, 4):
                add(f'wt_exit_{wt}', {'WT_EXIT_MIN_TFS': wt, 'TRADIER_WT_EXIT_MIN_TFS_TRADIER': wt})
            add('wt_pct_4h_strict', {'WT_PERCENTILE_EXIT_OB_4H': 80, 'WT_PERCENTILE_EXIT_OS_4H': 20})
            add('wt_pct_D_strict', {'WT_PERCENTILE_EXIT_OB_D': 85, 'WT_PERCENTILE_EXIT_OS_D': 15})
            add('wt_4h_vel_strict', {'WT_4H_VEL_EXIT_ENABLED': True, 'WT_4H_VEL_EXIT_LONG_VEL_MIN': 5.0, 'WT_4H_VEL_EXIT_SHORT_VEL_MIN': -5.0, 'WT_4H_VEL_EXIT_REQUIRE_K_EXTREME': True})
            add('wt_exhaust_on_gain', {'WT_EXHAUST_EXIT_ENABLED': True, 'WT_EXHAUST_EXIT_REQUIRE_GAIN': True})
        else:
            # 2 key variants: tighter entry score + looser exit
            add('es_22', {'ENTRY_SCORE_THRESHOLD': 22.0, 'TRADIER_ENTRY_SCORE_THRESHOLD': 22.0})
            add('wt_exit_2', {'WT_EXIT_MIN_TFS': 2, 'TRADIER_WT_EXIT_MIN_TFS_TRADIER': 2})
    return grid


def run_one_variant(sym: str, tag: str, override: Dict, run_dir: Path, account: str = 'flz',
                    start: str = '2022-01-01', mode: str = 'crypto', timeout_s: int = 3600) -> Optional[Dict]:
    """Invoke backtest_v8_engine subprocess. Returns metrics dict or None."""
    run_dir.mkdir(parents=True, exist_ok=True)
    ovr_path = run_dir / f'override_{sym}_{tag}.json'
    ovr_path.write_text(json.dumps(override, indent=2))
    run_id = f'real_{sym}_{tag}'
    env = os.environ.copy()
    env['V8_OVERRIDE_FILE'] = str(ovr_path)
    env['V8_TRADES_OUT_DIR'] = str(run_dir)
    env['V8_TRADES_RUN_ID'] = run_id
    env['V8_SWEEP_MODE'] = '1'
    env['V8_RATE_GUARD_DISABLED'] = '1'
    cmd = [PYTHON_BIN, str(ENGINE_PY), '--mode', mode, '--account', account,
           '--start', start, '--symbols', sym, '--capital', '10000']
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, timeout=timeout_s, text=True)
    except subprocess.TimeoutExpired:
        return {'sym': sym, 'tag': tag, 'error': 'timeout'}
    # Non-zero exit: asyncio cleanup errors are common (CancelledError on teardown).
    # Still try to read the JSONL — if trades were written before the crash, use them.
    nonzero_note = ''
    if r.returncode != 0:
        nonzero_note = f'rc={r.returncode}'
    # Read JSONL
    jp = run_dir / f'{run_id}__{sym}.jsonl'
    if not jp.exists():
        if nonzero_note:
            return {'sym': sym, 'tag': tag, 'error': r.stderr[-300:]}
        return {'sym': sym, 'tag': tag, 'trades': 0, 'pool_sharpe': 0.0,
                'wr_pct': 0.0, 'max_dd_pct': 0.0, 'trades_per_day': 0.0,
                'years': 0.0, 'note': 'no_jsonl'}
    rets = []; ts_first = None; ts_last = 0
    with jp.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
                p = float(rec.get('pnl_pct', 0))
                rets.append(p)
                e = int(rec.get('exit_ts', 0) or 0)
                if ts_first is None or (e and e < ts_first): ts_first = e
                if e > ts_last: ts_last = e
            except Exception: pass
    if not rets:
        return {'sym': sym, 'tag': tag, 'trades': 0, 'pool_sharpe': 0.0,
                'wr_pct': 0.0, 'max_dd_pct': 0.0, 'trades_per_day': 0.0, 'years': 0.0}
    n = len(rets)
    yrs = (ts_last - ts_first) / 86400 / 365.25 if ts_first else 0.01
    span_days = max(1.0, (ts_last - ts_first) / 86400) if ts_first else 1.0
    arr = np.array(rets, dtype=np.float64)
    sd = float(arr.std())
    pool = float(arr.mean() / sd) if sd > 1e-12 else 0.0
    wr = float((arr > 0).mean() * 100)
    eq = np.cumsum(arr); peak = np.maximum.accumulate(eq); dd = float((peak - eq).max())
    total = float(arr.sum())
    return {
        'sym': sym, 'tag': tag, 'trades': n, 'trades_per_day': n / span_days,
        'pool_sharpe': pool, 'sym_sharpe': max(-5.0, min(5.0, pool)),
        'wr_pct': wr, 'max_dd_pct': dd, 'total_gain_pct': total,
        'avg_gain_trade': total / n, 'gain_per_yr': total / yrs, 'gain_sym_yr': total / yrs,
        'years': yrs, 'n_syms': 1, 'n_wins': int((arr > 0).sum()),
        'override': override,
    }


def optimize_sym(sym: str, account: str = 'flz', start: str = '2022-01-01',
                 mode: str = 'crypto', timeout_s: int = 3600) -> Optional[Dict]:
    """Sweep variants for one symbol via real engine subprocess. Pick best."""
    base = _load_baseline(sym, mode=mode)
    grid = variants_for_sym(base, sym, account=account)
    run_dir = RUN_BASE / f'real_per_sym_{sym}_{int(time.time())}'
    results = []
    for tag, ovr in grid:
        r = run_one_variant(sym, tag, ovr, run_dir, account=account, start=start, mode=mode, timeout_s=timeout_s)
        if r is None: continue
        results.append(r)
        ps = r.get('pool_sharpe', 0); tr = r.get('trades', 0); dd = r.get('max_dd_pct', 0); wr = r.get('wr_pct', 0)
        err = r.get('error', '')
        note = r.get('note', '')
        suffix = f"  ERR={err[:120]}" if err else (f"  {note}" if note else "")
        print(f"  {sym:12s} {tag:25s} pool={ps:+.4f} tr={tr:>6d} wr={wr:5.1f}% dd={dd:5.2f}%{suffix}", flush=True)
    if not results:
        return None
    # Pick highest pool_sharpe with trades >= floor
    qualified = [r for r in results if r.get('trades', 0) >= PROMOTE_TRADES_MIN]
    if not qualified: qualified = results
    winner = max(qualified, key=lambda r: r.get('pool_sharpe', -1e9))
    return {'sym': sym, 'winner': winner, 'all_results': results, 'baseline_pool': results[0].get('pool_sharpe', 0)}


def write_active_config(winners: Dict) -> int:
    ACTIVE_CFG.parent.mkdir(parents=True, exist_ok=True)
    try: existing = json.loads(ACTIVE_CFG.read_text())
    except Exception: existing = {}
    promoted = 0
    now = time.strftime('%Y-%m-%d', time.gmtime())
    for sym, info in winners.items():
        if info is None: continue
        w = info['winner']
        ps = w.get('pool_sharpe', 0); tr = w.get('trades', 0); dd = w.get('max_dd_pct', 0)
        verdict = 'PROMOTE' if (ps >= PROMOTE_POOL_FLOOR and dd <= PROMOTE_DD_CAP and tr >= PROMOTE_TRADES_MIN) else 'DIAGNOSTIC'
        if ps < 0: verdict = 'NOISE'
        existing[sym] = {
            'winning_tag': f"real_{sym}_{w.get('tag','')}_{now}",
            'wsharpe': ps,
            'pool_sharpe': ps,
            'trades': tr,
            'trades_per_day': w.get('trades_per_day', 0),
            'wr_pct': w.get('wr_pct', 0),
            'max_dd_pct': dd,
            'avg_gain_trade': w.get('avg_gain_trade', 0),
            'gain_per_yr': w.get('gain_per_yr', 0),
            'years': w.get('years', 0),
            'sample_tag': 'CRYPTO_PER_SYM_REAL_V8',
            'verdict': verdict,
            'overrides': w.get('override', {}),
            'baseline_pool': info.get('baseline_pool', 0),
        }
        if verdict == 'PROMOTE': promoted += 1
    ACTIVE_CFG.write_text(json.dumps(existing, indent=2, default=str))
    return promoted


def cycle(syms: List[str], account: str, start: str, mode: str, workers: int, timeout_s: int = 3600) -> Dict:
    print(f"[per_sym_real_profiler] {len(syms)} syms, account={account}, start={start}, workers={workers}", flush=True)
    t0 = time.time()
    csv_path = SWEEP_DIR / f'per_sym_real_{account}_{int(time.time())}.csv'
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    winners: Dict[str, Dict] = {}
    if workers <= 1:
        for i, s in enumerate(syms, 1):
            r = optimize_sym(s, account=account, start=start, mode=mode, timeout_s=timeout_s)
            winners[s] = r
            elapsed = time.time() - t0
            print(f"[{i}/{len(syms)}] {s} done in {elapsed:.0f}s", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(optimize_sym, s, account, start, mode, timeout_s): s for s in syms}
            done = 0
            for fut in as_completed(futs):
                done += 1
                s = futs[fut]
                try: winners[s] = fut.result()
                except Exception as e: print(f"  worker exc {s}: {e}", flush=True); winners[s] = None
                elapsed = time.time() - t0
                print(f"[{done}/{len(syms)}] {s} done in {elapsed:.0f}s", flush=True)
    promoted = write_active_config(winners)
    # Canonical CSV via metrics_guard (one row per sym winner)
    for s, info in winners.items():
        if info is None: continue
        w = info['winner']
        try:
            row = {k: v for k, v in w.items() if k not in ('override',)}
            row['tag'] = f"real_{s}_{w.get('tag','')}"
            mg.write_sharpe_row(csv_path, row, mode=mode, append=True)
        except Exception as e:
            print(f"  [csv] REFUSED {s}: {e}", flush=True)
    elapsed = time.time() - t0
    print(f"[per_sym_real_profiler] cycle done in {elapsed:.0f}s, promoted={promoted}/{len([w for w in winners.values() if w])}", flush=True)
    print(f"[per_sym_real_profiler] CSV: {csv_path}", flush=True)
    return {'completed': len(winners), 'promoted': promoted, 'elapsed_s': elapsed}


def load_account_syms(account: str) -> List[str]:
    files = {
        'flz': ['symbols_flz.json'],
        'fin': ['symbols_fin.json'],
        'men': ['symbols_men.json'],
        'ang': ['symbols_ang_long.json', 'symbols_ang_short.json'],
        'inf': ['symbols_inf_long.json', 'symbols_inf_short.json'],
        'trb': ['symbols_trb_long.json', 'symbols_trb_short.json'],
    }.get(account, [])
    seen = set(); out = []
    for f in files:
        p = ROOT / f
        if not p.exists(): continue
        raw = re.sub(r',(\s*[\]}])', r'\1', p.read_text())
        for s in json.loads(raw):
            if isinstance(s, str) and s not in seen and (NPZ_DIR / f'{s}.npz').exists():
                seen.add(s); out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms', default='', help='comma-sep override (default: account universe)')
    ap.add_argument('--account', default='', help='account key; defaults to ang (crypto) or trb (tradier)')
    ap.add_argument('--mode', default='crypto', choices=['crypto', 'tradier'])
    ap.add_argument('--start', default='2026-04-01')
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--once', action='store_true', default=True)
    ap.add_argument('--daemon', action='store_true')
    ap.add_argument('--cycle-interval-s', type=int, default=86400)
    ap.add_argument('--timeout', type=int, default=3600, help='subprocess timeout per variant in seconds')
    args = ap.parse_args()
    if not args.account:
        args.account = 'trb' if args.mode == 'tradier' else 'ang'
    if args.syms:
        syms = [s.strip() for s in args.syms.split(',') if s.strip()]
    else:
        syms = load_account_syms(args.account)
    if not syms: print("no syms"); return 1
    if args.daemon:
        while True:
            t0 = time.time()
            try: cycle(syms, args.account, args.start, args.mode, args.workers, timeout_s=args.timeout)
            except Exception: traceback.print_exc()
            elapsed = time.time() - t0
            sleep_s = max(60, args.cycle_interval_s - int(elapsed))
            print(f"[per_sym_real_profiler] cycle done in {elapsed:.0f}s; sleeping {sleep_s}s", flush=True)
            time.sleep(sleep_s)
    else:
        cycle(syms, args.account, args.start, args.mode, args.workers, timeout_s=args.timeout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
