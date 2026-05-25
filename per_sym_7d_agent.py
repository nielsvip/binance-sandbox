#!/usr/bin/env python3
"""per_sym_7d_agent — rolling 7-day reconfig using the new per_sym_engine_crypto.

Replaces the old flz_hourly_reconfig.py path that used v8_quick_engine. Now uses the
new vectorized engine with all 9 entry paths + 4 booster gates + augment/reentry/hedge
toggles. Per user 2026-05-05: "rewrite and apply 7D test ... weight on the last day".

Logic:
  1. Read per_sym_active_config.json — that's the baseline (4-yr full sweep winner per sym).
  2. For each symbol:
       a. Run baseline params on last 7 days
       b. Run a small candidate set of variants (looser/tighter neighborhoods of baseline)
       c. Pick winner by time-weighted pool Sharpe (today=1.0, yesterday=0.5, ...)
  3. Promote: 7-day winner beats baseline by Δwsharpe ≥ 0.1 AND wsharpe ≥ 0.7.
  4. Write hourly active config: data/hourly_reconfig/{account}/active_config_7d.json.

Per CLAUDE.md: never places live orders. The downstream live executor is responsible
for reading the active config and routing through execute_now().
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
# Defensive: some NPZ fields (bar_pattern_codes, bar_vol_regime_codes) are object
# arrays. Default np.load() refuses with allow_pickle=False since numpy 1.16.5.
# Engine modules call plain np.load(path) — patch the default here so those fields
# load. We only read precomputed indicator data we authored. (Same patch as
# per_sym_20d_agent_stocks.py — extracted 2026-05-19 when ang BTCUSDC NPZ tripped
# this in the 7D smoke run on S1.)
_orig_np_load = np.load
def _safe_np_load(*args, **kwargs):
    kwargs.setdefault('allow_pickle', True)
    return _orig_np_load(*args, **kwargs)
np.load = _safe_np_load  # affects this process AND its workers via fork+import

import metrics_guard as mg
import per_sym_engine_crypto as _pse_crypto
from per_sym_engine_crypto import SymParams, simulate_dual, NPZ_DIR, _npz_cache

# Defensive wrapper around engine's load_3m_base: tolerate 0-d / non-1-d object
# fields in the NPZ (some recent regens stored bar_pattern_codes as 0-d). Mirror
# the wrapper in per_sym_20d_agent_stocks.py. The engine only consumes float arrays
# for its decision pipeline.
_orig_load_3m_base = _pse_crypto.load_3m_base


def _safe_load_3m_base(sym, years_back=4.0):
    try:
        return _orig_load_3m_base(sym, years_back=years_back)
    except Exception:
        pass
    cache_key = f'{sym}__y{years_back:.2f}'
    cache = _pse_crypto._npz_cache
    if cache_key in cache:
        return cache[cache_key]
    p = NPZ_DIR / f'{sym}.npz'
    if not p.exists():
        return None
    try:
        z = np.load(str(p), allow_pickle=True)
    except Exception:
        return None
    needed = ['open_3m', 'high_3m', 'low_3m', 'close_3m', 'timestamps']
    if not all(k in z.files for k in needed):
        z.close()
        return None
    full: Dict[str, np.ndarray] = {}
    for k in z.files:
        try:
            arr = z[k]
            if arr.ndim < 1:
                continue
            full[k] = arr[:]
        except Exception:
            continue
    z.close()
    if cache:
        cache.clear()
    ts_full = full['timestamps'].astype(np.int64)
    full['timestamps'] = ts_full
    cutoff = ts_full[-1] - int(years_back * 365.25 * 86400)
    si = int(np.searchsorted(ts_full, cutoff))
    sliced: Dict[str, np.ndarray] = {}
    for k, arr in full.items():
        if isinstance(arr, np.ndarray) and arr.ndim == 1 and len(arr) == len(ts_full):
            sliced[k] = arr[si:]
        else:
            sliced[k] = arr
    sliced['open'] = sliced['open_3m'].astype(np.float64)
    sliced['high'] = sliced['high_3m'].astype(np.float64)
    sliced['low'] = sliced['low_3m'].astype(np.float64)
    sliced['close'] = sliced['close_3m'].astype(np.float64)
    sliced['volume'] = sliced.get('volume_3m', np.ones(len(sliced['close']))).astype(np.float64)
    sliced['ts'] = sliced['timestamps']
    cache[cache_key] = sliced
    return sliced


_pse_crypto.load_3m_base = _safe_load_3m_base

# Optional vec-axis engine (per_sym_vec_engine_crypto). When --use-vec is passed, the
# agent evaluates MILLIONS of variants instead of the ~9-variant neighbourhood, then
# falls back to the scalar engine on the top-K for paranoid verification (no claims
# made on raw vec numbers — they're the SHORTLIST, scalar is the VERDICT).
try:
    from per_sym_vec_engine_crypto import sweep_variants as _vec_sweep
    from per_sym_vec_engine_crypto import top_k as _vec_top_k
    from per_sym_variant_generator import generate_variants as _gen_variants
    VEC_AVAILABLE = True
except Exception as _e:
    VEC_AVAILABLE = False
    _vec_sweep = None; _vec_top_k = None; _gen_variants = None

OUT_BASE = ROOT / 'data' / 'hourly_reconfig'
ACTIVE_CFG = OUT_BASE / 'per_sym_active_config.json'
SWEEP_CSV_DIR = ROOT / 'data' / 'sweep_results'

WINDOW_DAYS = 7.0
PROMOTE_DELTA_WSHARPE = 0.10
PROMOTE_WSHARPE_FLOOR = 0.7
MIN_TRADES_FOR_OPINION = 6
TPD_MIN = 5.0
TPD_MAX = 15.0


def time_weighted_pool_sharpe(returns_with_ts: List[Tuple[float, int]],
                              ref_ts: int) -> Tuple[float, float, int]:
    if not returns_with_ts:
        return 0.0, 0.0, 0
    rs = np.array([r for r, _ in returns_with_ts], dtype=np.float64)
    ts = np.array([t for _, t in returns_with_ts], dtype=np.float64)
    days_ago = np.maximum(0.0, (ref_ts - ts) / 86400.0)
    w = np.power(2.0, -days_ago)
    w_sum = float(w.sum())
    if w_sum <= 0 or len(rs) < 2:
        return 0.0, 0.0, len(rs)
    wmean = float((rs * w).sum() / w_sum)
    wvar = float((w * (rs - wmean) ** 2).sum() / w_sum)
    wstd = math.sqrt(max(wvar, 0.0))
    wsharpe = (wmean / wstd) if wstd > 1e-12 else 0.0
    return wsharpe, w_sum, len(rs)


def load_baseline_for_sym(sym: str) -> Optional[SymParams]:
    p = SymParams()
    path = ROOT / "symbol_configs" / f"{sym}_best.json"
    overrides = {}
    if path.exists():
        try: overrides = json.loads(path.read_text())
        except Exception: pass
    elif ACTIVE_CFG.exists():
        try:
            d = json.loads(ACTIVE_CFG.read_text())
            entry = d.get(sym) or d.get(f'{sym}_LONG')
            overrides = entry.get('overrides') or {}
        except Exception: pass
    unknown_knobs = []
    for k, v in overrides.items():
        if k.startswith('_'): continue
        if hasattr(p, k):
            try: setattr(p, k, v)
            except Exception: pass
        else: unknown_knobs.append(k)
    if unknown_knobs:
        print(f"[per_sym_7d_agent] {sym}: {len(unknown_knobs)} unknown knobs in baseline: {','.join(unknown_knobs[:6])}", flush=True)
    return p


def neighborhood_variants(base: SymParams) -> List[Tuple[str, SymParams]]:
    """Small candidate set: baseline + a few looser/tighter neighbors. Designed to finish in <30s/sym.

    USE_V8_AGGREGATORS is forced OFF because v8_quick_engine was renamed to
    OPUS_VOMIT.py (USER 2026-05-10 mandate). Engine falls back to its native
    _build_signals path. Same fix as per_sym_20d_agent_stocks.py.
    """
    base = base.copy()
    base.USE_V8_AGGREGATORS = False
    variants: List[Tuple[str, SymParams]] = [('baseline', base.copy())]
    # Loosen
    p = base.copy(); p.MIN_HOLD_BARS_15m = max(1, base.MIN_HOLD_BARS_15m - 2)
    variants.append(('loose_min_hold', p))
    p = base.copy(); p.COOLDOWN_BARS_15m = max(0, base.COOLDOWN_BARS_15m - 1)
    variants.append(('loose_cooldown', p))
    p = base.copy(); p.MIN_TFS_AGREE = 2
    variants.append(('loose_min_tfs', p))
    # Tighten
    p = base.copy(); p.MIN_TFS_AGREE = min(4, base.MIN_TFS_AGREE + 1)
    variants.append(('tight_min_tfs', p))
    p = base.copy(); p.REQUIRE_D_TREND = True
    variants.append(('tight_d_trend', p))
    # Toggle the 3 v8-parity additions
    p = base.copy(); p.REVERSE_ON_EXIT_ENABLED = not base.REVERSE_ON_EXIT_ENABLED
    variants.append(('toggle_reverse', p))
    p = base.copy(); p.REENTRY_MEAN_REV_ENABLED = not base.REENTRY_MEAN_REV_ENABLED
    variants.append(('toggle_meanrev', p))
    p = base.copy(); p.NOLOSS_ENABLED = not base.NOLOSS_ENABLED
    variants.append(('toggle_noloss', p))
    return variants


def evaluate_7d(sym: str, params: SymParams) -> Optional[Dict]:
    """Run engine over 7 days, return weighted Sharpe + metrics."""
    yrs = WINDOW_DAYS / 365.25
    m = simulate_dual(sym, params, years_back=yrs)
    if m is None:
        return None
    trades = m.get('trade_list', [])
    if not trades:
        return {'wsharpe': 0.0, 'trades': 0, 'trades_per_day': 0.0,
                'pool_sharpe': 0.0, 'max_dd_pct': 0.0, 'wr_pct': 0.0,
                'gain_per_week': 0.0,
                'bh_pct_window': float(m.get('bh_pct_window', 0.0)),
                'long_trades': 0, 'short_trades': 0,
                'augment_count': 0, 'reverse_on_exit_count': 0,
                'mean_rev_reentry_count': 0, 'hedge_count': 0,
                'follow_through_count': 0,
                'metrics': m, 'params': params.to_dict()}
    rets_ts = [(t['pnl_pct'], t['exit_ts']) for t in trades]
    ref_ts = max(t['exit_ts'] for t in trades)
    ws, eff, n = time_weighted_pool_sharpe(rets_ts, ref_ts)
    return {
        'wsharpe': ws,
        'trades': n,
        'trades_per_day': n / max(1.0, WINDOW_DAYS),
        'pool_sharpe': float(m.get('pool_sharpe', 0)),
        'max_dd_pct': float(m.get('max_dd_pct', 0)),
        'wr_pct': float(m.get('wr_pct', 0)),
        'gain_per_week': float(m.get('gain_per_week', 0.0)),
        'bh_pct_window': float(m.get('bh_pct_window', 0.0)),
        'long_trades': int(m.get('long_trades', 0)),
        'short_trades': int(m.get('short_trades', 0)),
        'augment_count': int(m.get('augment_count', 0)),
        'reverse_on_exit_count': int(m.get('reverse_on_exit_count', 0)),
        'mean_rev_reentry_count': int(m.get('mean_rev_reentry_count', 0)),
        'hedge_count': int(m.get('hedge_count', 0)),
        'follow_through_count': int(m.get('follow_through_count', 0)),
        'metrics': m,
        'params': params.to_dict(),
    }


_VEC_N_VARIANTS = int(os.environ.get('PER_SYM_VEC_N', '0') or '0')  # 0 = vec disabled


def reconfig_one_sym(sym: str) -> Optional[Dict]:
    base = load_baseline_for_sym(sym) or SymParams()
    # ── OPTIONAL: vec-engine shortlist (variant-axis vectorized) ──────────
    # When PER_SYM_VEC_N env var is set (e.g. =1000000), the vec engine evaluates
    # that many variants per sym on the 7d window, picks top-K by time-weighted
    # Sharpe, and feeds those K back through the scalar simulate_dual for paranoid
    # verification. Vec numbers are NEVER published as final results — they are a
    # high-throughput SHORTLIST tool only. (per CLAUDE.md SHARPE rule 1: every
    # Sharpe written to disk must come from the scalar engine via metrics_guard.)
    vec_extra_variants: List[Tuple[str, SymParams]] = []
    if _VEC_N_VARIANTS > 0 and VEC_AVAILABLE:
        try:
            vec_variants = _gen_variants(n_total=_VEC_N_VARIANTS, mode='crypto')
            vec_sb = _vec_sweep(sym, vec_variants, years_back=WINDOW_DAYS / 365.25,
                                chunk_size=10000, verbose=False,
                                n_years_for_yr_metrics=WINDOW_DAYS / 365.25)
            # Top 10 candidates by time-weighted Sharpe with >=6 trades
            top = _vec_top_k(vec_sb, vec_variants, k=10,
                             sort_by='time_weighted_sharpe', min_trades=MIN_TRADES_FOR_OPINION)
            for rank, (vd, m) in enumerate(top, 1):
                p = base.copy()
                for k, v in vd.items():
                    if hasattr(p, k):
                        try: setattr(p, k, v)
                        except Exception: pass
                vec_extra_variants.append((f'vec_top{rank}_tws{m["time_weighted_sharpe"]:.2f}', p))
        except Exception as e:
            print(f"[7d_agent vec] {sym} ERR: {e}", flush=True)
            vec_extra_variants = []
    variants = neighborhood_variants(base) + vec_extra_variants
    results: List[Tuple[str, Dict]] = []
    for tag, p in variants:
        r = evaluate_7d(sym, p)
        if r is None: continue
        r['variant_tag'] = tag
        results.append((tag, r))
    if not results:
        return None
    # Pick winner by weighted Sharpe; require min trades
    eligible = [(t, r) for t, r in results if r['trades'] >= MIN_TRADES_FOR_OPINION]
    if not eligible:
        eligible = results
    winner_tag, winner_r = max(eligible, key=lambda x: x[1]['wsharpe'])
    base_tag = 'baseline'
    base_r = next((r for t, r in results if t == base_tag), winner_r)
    promote = (
        winner_r['wsharpe'] >= PROMOTE_WSHARPE_FLOOR and
        winner_r['wsharpe'] - base_r['wsharpe'] >= PROMOTE_DELTA_WSHARPE and
        winner_r['trades_per_day'] >= TPD_MIN and
        winner_r['trades_per_day'] <= TPD_MAX
    )
    return {
        'sym': sym,
        'winner_tag': winner_tag,
        'winner_wsharpe': winner_r['wsharpe'],
        'baseline_wsharpe': base_r['wsharpe'],
        'delta_wsharpe': winner_r['wsharpe'] - base_r['wsharpe'],
        'trades': winner_r['trades'],
        'trades_per_day': winner_r['trades_per_day'],
        'pool_sharpe': winner_r['pool_sharpe'],
        'wr_pct': winner_r['wr_pct'],
        'max_dd_pct': winner_r['max_dd_pct'],
        'gain_per_week': winner_r.get('gain_per_week', 0.0),
        'bh_pct_window': winner_r.get('bh_pct_window', 0.0),
        'long_trades': winner_r['long_trades'],
        'short_trades': winner_r['short_trades'],
        'augment_count': winner_r['augment_count'],
        'reverse_on_exit_count': winner_r['reverse_on_exit_count'],
        'mean_rev_reentry_count': winner_r['mean_rev_reentry_count'],
        'hedge_count': winner_r['hedge_count'],
        'follow_through_count': winner_r['follow_through_count'],
        'promote': promote,
        'params': winner_r['params'],
        'all_variants': [{'tag': t, 'wsharpe': r['wsharpe'], 'trades': r['trades'],
                          'wr_pct': r.get('wr_pct', 0.0), 'max_dd_pct': r.get('max_dd_pct', 0.0),
                          'gain_per_week': r.get('gain_per_week', 0.0),
                          'bh_pct_window': r.get('bh_pct_window', 0.0)} for t, r in results],
    }


def _worker(sym):
    try:
        return reconfig_one_sym(sym)
    except Exception:
        return {'sym': sym, 'error': traceback.format_exc()[-400:]}


def write_active_7d(account: str, results: List[Dict]) -> int:
    out_dir = OUT_BASE / account
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'active_config_7d.json'
    payload = {}
    promoted = 0
    for r in results:
        if r is None or 'error' in r:
            continue
        sym = r['sym']
        payload[sym] = {
            'winning_tag': r['winner_tag'],
            'wsharpe': r['winner_wsharpe'],
            'baseline_wsharpe': r['baseline_wsharpe'],
            'delta_wsharpe': r['delta_wsharpe'],
            'trades_7d': r['trades'],
            'trades_per_day': r['trades_per_day'],
            'pool_sharpe': r['pool_sharpe'],
            'wr_pct': r['wr_pct'],
            'max_dd_pct': r['max_dd_pct'],
            'gain_per_week': r.get('gain_per_week', 0.0),
            'bh_pct_window': r.get('bh_pct_window', 0.0),
            'long_trades': r['long_trades'],
            'short_trades': r['short_trades'],
            'augment_count': r['augment_count'],
            'reverse_on_exit_count': r['reverse_on_exit_count'],
            'mean_rev_reentry_count': r['mean_rev_reentry_count'],
            'hedge_count': r['hedge_count'],
            'follow_through_count': r['follow_through_count'],
            'promote': r['promote'],
            'overrides': r['params'],
            'updated_at': int(time.time()),
        }
        if r['promote']:
            promoted += 1
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    return promoted


def cycle(account: str, syms: List[str], workers: int) -> Dict:
    print(f"[per_sym_7d_agent] cycle {account} | {len(syms)} syms | workers={workers}", flush=True)
    t0 = time.time()
    results: List[Dict] = []
    if workers <= 1:
        for i, s in enumerate(syms, 1):
            r = _worker(s)
            if r is not None:
                results.append(r)
            if i % max(1, len(syms) // 10) == 0 or i == len(syms):
                elapsed = time.time() - t0
                print(f"  [{i}/{len(syms)}] elapsed={elapsed:.0f}s avg={elapsed/i:.1f}s/sym", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_worker, s): s for s in syms}
            done = 0
            for fut in as_completed(futs):
                done += 1
                try:
                    r = fut.result()
                except Exception as e:
                    print(f"  worker exc: {e}", flush=True); continue
                if r is not None:
                    results.append(r)
                if done % max(1, len(syms) // 10) == 0 or done == len(syms):
                    elapsed = time.time() - t0
                    print(f"  [{done}/{len(syms)}] elapsed={elapsed:.0f}s avg={elapsed/done:.1f}s/sym", flush=True)
    elapsed = time.time() - t0
    promoted = write_active_7d(account, results)
    print(f"[per_sym_7d_agent] done in {elapsed:.0f}s | promoted={promoted}/{len(results)}", flush=True)
    # Top-10 by wsharpe with WR/DD/gain_per_week/B&H per USER 2026-05-19 reporting standard.
    valid = [r for r in results if r and 'error' not in r]
    valid.sort(key=lambda x: x.get('winner_wsharpe', -999), reverse=True)
    if valid:
        print(f"  Top {min(10, len(valid))} by wsharpe:", flush=True)
        for r in valid[:10]:
            print(f"    {r['sym']:<14s} tag={r['winner_tag']:<18s} "
                  f"wsharpe={r['winner_wsharpe']:+.3f} "
                  f"Δ={r['delta_wsharpe']:+.3f} "
                  f"tr={r['trades']:>4d} tpd={r['trades_per_day']:.2f} "
                  f"pool={r['pool_sharpe']:+.3f} "
                  f"WR={r['wr_pct']:5.1f}% "
                  f"DD={r['max_dd_pct']:5.2f}% "
                  f"g/wk={r.get('gain_per_week', 0.0):+.2f}% "
                  f"B&H={r.get('bh_pct_window', 0.0):+.2f}% "
                  f"promote={r['promote']}", flush=True)
    return {'completed': len(results), 'promoted': promoted, 'elapsed_s': elapsed}


def load_account_syms(account: str) -> List[str]:
    files = {
        'flz': ['symbols_flz.json'],
        'fin': ['symbols_fin.json'],
        'men': ['symbols_men.json'],
        'ang': ['symbols_ang_long.json', 'symbols_ang_short.json'],
        'inf': ['symbols_inf_long.json', 'symbols_inf_short.json'],
    }.get(account, [])
    syms: List[str] = []
    seen = set()
    for f in files:
        p = ROOT / f
        if not p.exists(): continue
        raw = re.sub(r',(\s*[\]}])', r'\1', p.read_text())
        for s in json.loads(raw):
            if isinstance(s, str) and s not in seen and (NPZ_DIR / f'{s}.npz').exists():
                seen.add(s); syms.append(s)
    return syms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--account', required=True, choices=['ang', 'fin', 'men', 'flz', 'inf'])
    ap.add_argument('--syms', default='', help='comma-sep override (default: account universe)')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--once', action='store_true', default=True)
    ap.add_argument('--daemon', action='store_true', help='cycle every --interval-s')
    ap.add_argument('--interval-s', type=int, default=3600)
    ap.add_argument('--vec-n', type=int, default=0,
                    help='If >0 (e.g. 100000), run per_sym_vec_engine_crypto with that '
                         'many variants per sym to SHORTLIST. Top-K then re-run on scalar '
                         'engine. 0 disables. Per CLAUDE.md: vec numbers NEVER promoted.')
    args = ap.parse_args()
    # propagate to module-level for reconfig_one_sym worker (used by ProcessPoolExecutor too).
    if args.vec_n:
        os.environ['PER_SYM_VEC_N'] = str(args.vec_n)
        global _VEC_N_VARIANTS
        _VEC_N_VARIANTS = args.vec_n

    if args.syms:
        syms = [s.strip() for s in args.syms.split(',') if s.strip()]
    else:
        syms = load_account_syms(args.account)
    if not syms:
        print(f"no syms for account={args.account}"); return 1

    if args.daemon:
        while True:
            t0 = time.time()
            try:
                cycle(args.account, syms, args.workers)
            except Exception:
                traceback.print_exc()
            elapsed = time.time() - t0
            sleep_s = max(60, args.interval_s - int(elapsed))
            print(f"[per_sym_7d_agent] cycle done in {elapsed:.0f}s; sleeping {sleep_s}s", flush=True)
            time.sleep(sleep_s)
    else:
        cycle(args.account, syms, args.workers)
    return 0


if __name__ == '__main__':
    sys.exit(main())
