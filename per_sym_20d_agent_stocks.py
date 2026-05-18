#!/usr/bin/env python3
"""per_sym_20d_agent_stocks — rolling 20-day reconfig for TRADIER stocks accounts.

Stocks counterpart of per_sym_7d_agent.py (crypto). Per user spec 2026-05-17:

  * Window: 20 trading days (vs crypto's 7 days)
  * Recency weight: each day is 50% MORE weight than the previous day
      → decay factor = 1/1.5 = 0.667
      today = 1.0, yesterday = 0.667, day-2 = 0.444, day-19 = ~0.0009
  * Engine: per_sym_engine_stocks.simulate_dual_stocks (5m base, MODE='tradier')
  * Source: data/hourly_reconfig/per_sym_active_config.json (same as crypto),
            filtered to per-account tradier universe
  * Promotion gate: same logic — Δwsharpe ≥ 0.1 AND wsharpe ≥ 0.7
  * Output:
      - data/hourly_reconfig/{trb,trc}/active_config_20d.json (winners only payload)
      - data/hourly_reconfig/{trb,trc}/runs/run_<ts>.json (audit trail)
      - data/hourly_reconfig/{trb,trc}/_candidates/cand_<sym>_<ts>.json (per-variant)
  * NO live code edits. NO live orders. Standalone read-NPZ + write-JSON.

Per CLAUDE.md:
  - DIAGNOSTIC tag when n_syms < MIN_SYMS_STOCKS (100) in the published summary.
  - Pool Sharpe via metrics_guard.pool_sharpe (no annualization, no sqrt-N).
  - Open trades MtM'd to final bar by simulate_dual_stocks already (rule R2).
  - Trade-return list IS the source of truth.
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
# The engine modules call plain np.load(path) — we patch the default here so
# those fields load. We only read precomputed indicator data we authored.
_orig_np_load = np.load
def _safe_np_load(*args, **kwargs):
    kwargs.setdefault('allow_pickle', True)
    return _orig_np_load(*args, **kwargs)
np.load = _safe_np_load  # affects this process AND its workers via fork+import

import metrics_guard as mg
import per_sym_engine_stocks as _pse
from per_sym_engine_stocks import (
    SymParamsStocks,
    simulate_dual_stocks,
    BARS_PER_DAY_STOCKS,
)
from per_sym_engine_crypto import NPZ_DIR

# Defensive wrapper around engine's load_5m_base: tolerate 0-d / non-1-d object
# fields in the NPZ (e.g. bar_pattern_codes scalars on some syms). We delegate
# to the original loader; if it raises, fall back to a hand-loaded dict that
# skips problem fields. The engine only consumes float arrays.
_orig_load_5m_base = _pse.load_5m_base


def _safe_load_5m_base(sym, years_back=4.0):
    try:
        return _orig_load_5m_base(sym, years_back=years_back)
    except Exception:
        pass
    cache_key = f'{sym}__y{years_back:.2f}'
    cache = _pse._npz_cache_stocks
    if cache_key in cache:
        return cache[cache_key]
    p = NPZ_DIR / f'{sym}.npz'
    if not p.exists():
        return None
    try:
        z = np.load(str(p), allow_pickle=True)
    except Exception:
        return None
    needed = ['open_5m', 'high_5m', 'low_5m', 'close_5m', 'timestamps']
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
    sliced['open'] = sliced['open_5m'].astype(np.float64)
    sliced['high'] = sliced['high_5m'].astype(np.float64)
    sliced['low'] = sliced['low_5m'].astype(np.float64)
    sliced['close'] = sliced['close_5m'].astype(np.float64)
    sliced['volume'] = sliced.get('volume_5m', np.ones(len(sliced['close']))).astype(np.float64)
    sliced['ts'] = sliced['timestamps']
    cache[cache_key] = sliced
    return sliced


_pse.load_5m_base = _safe_load_5m_base

OUT_BASE = ROOT / 'data' / 'hourly_reconfig'
ACTIVE_CFG = OUT_BASE / 'per_sym_active_config.json'
STRUCT_V4_UNIVERSE = ROOT / 'data' / 'struct_v4_universe.json'

WINDOW_DAYS = 20.0
DAY_DECAY_FACTOR = 1.0 / 1.5
PROMOTE_DELTA_WSHARPE = 0.10
PROMOTE_WSHARPE_FLOOR = 0.7
MIN_TRADES_FOR_OPINION = 6
TPD_MIN = 0.5
TPD_MAX = 12.0

ACCOUNTS = ('trb', 'trc')


def stocks_day_weight(days_back: int) -> float:
    """Each day is 50% MORE weight than the previous → decay = 1/1.5 per day-back."""
    return DAY_DECAY_FACTOR ** max(0, int(days_back))


def time_weighted_pool_sharpe(returns_with_ts: List[Tuple[float, int]],
                              ref_ts: int) -> Tuple[float, float, int]:
    """Weighted pool Sharpe = (w·mean)/(w·std) where weights decay 1/1.5 per day-back.
    Uses metrics_guard.pool_sharpe semantics (per-trade returns, no annualization,
    no sqrt) for the unweighted reference; weights are applied post-hoc to mean+var.
    """
    if not returns_with_ts:
        return 0.0, 0.0, 0
    rs = np.array([r for r, _ in returns_with_ts], dtype=np.float64)
    ts = np.array([t for _, t in returns_with_ts], dtype=np.float64)
    days_ago = np.maximum(0.0, (ref_ts - ts) / 86400.0)
    w = np.power(DAY_DECAY_FACTOR, days_ago)
    w_sum = float(w.sum())
    if w_sum <= 0 or len(rs) < 2:
        return 0.0, 0.0, len(rs)
    wmean = float((rs * w).sum() / w_sum)
    wvar = float((w * (rs - wmean) ** 2).sum() / w_sum)
    wstd = math.sqrt(max(wvar, 0.0))
    wsharpe = (wmean / wstd) if wstd > 1e-12 else 0.0
    return wsharpe, w_sum, len(rs)


def load_baseline_for_sym(sym: str) -> Optional[SymParamsStocks]:
    """Read per_sym_active_config.json. Try LONG entry first, then SHORT, then bare.
    Returns SymParamsStocks populated from `overrides` dict; falls back to defaults
    when key absent or overrides empty."""
    if not ACTIVE_CFG.exists():
        return None
    try:
        d = json.loads(ACTIVE_CFG.read_text())
    except Exception:
        return None
    entry = (d.get(f'{sym}_LONG') or d.get(f'{sym}_SHORT') or d.get(sym))
    if not entry:
        return None
    overrides = entry.get('overrides') or {}
    p = SymParamsStocks()
    for k, v in overrides.items():
        if k.startswith('_'):
            continue
        if not hasattr(p, k):
            continue
        try:
            setattr(p, k, v)
        except Exception:
            pass
    return p


def neighborhood_variants(base: SymParamsStocks) -> List[Tuple[str, SymParamsStocks]]:
    """Small candidate set: baseline + loose/tight neighbors + toggle flips.
    Designed to finish in <30s/sym on 20d window.

    USE_V8_AGGREGATORS is forced OFF because v8_quick_engine was renamed to
    OPUS_VOMIT.py (USER 2026-05-10 mandate); engine falls back to _build_signals
    which is the correct path for stocks now.
    """
    base = base.copy()
    base.USE_V8_AGGREGATORS = False
    variants: List[Tuple[str, SymParamsStocks]] = [('baseline', base.copy())]

    # Loosen
    p = base.copy(); p.MIN_HOLD_BARS_15m = max(1, base.MIN_HOLD_BARS_15m - 2)
    variants.append(('loose_min_hold', p))
    p = base.copy(); p.COOLDOWN_BARS_15m = max(0, base.COOLDOWN_BARS_15m - 1)
    variants.append(('loose_cooldown', p))
    p = base.copy(); p.MIN_TFS_AGREE = max(2, base.MIN_TFS_AGREE - 1)
    variants.append(('loose_min_tfs', p))

    # Tighten — stocks bias higher TF alignment per CLAUDE.md (HTF ≥ 2)
    p = base.copy(); p.MIN_TFS_AGREE = min(4, base.MIN_TFS_AGREE + 1)
    variants.append(('tight_min_tfs', p))
    p = base.copy(); p.REQUIRE_D_TREND = True
    variants.append(('tight_d_trend', p))

    # Toggle the parity additions
    p = base.copy(); p.REVERSE_ON_EXIT_ENABLED = not base.REVERSE_ON_EXIT_ENABLED
    variants.append(('toggle_reverse', p))
    p = base.copy(); p.REENTRY_MEAN_REV_ENABLED = not base.REENTRY_MEAN_REV_ENABLED
    variants.append(('toggle_meanrev', p))
    p = base.copy(); p.NOLOSS_ENABLED = not base.NOLOSS_ENABLED
    variants.append(('toggle_noloss', p))

    return variants


def evaluate_20d(sym: str, params: SymParamsStocks) -> Optional[Dict]:
    """Run stocks engine over WINDOW_DAYS calendar days, return weighted Sharpe + metrics.
    20 trading days × 78 5m bars/day = 1,560 bars (sub-floor — DIAGNOSTIC tier).
    """
    yrs = WINDOW_DAYS / 365.25
    m = simulate_dual_stocks(sym, params, years_back=yrs)
    if m is None:
        return None
    trades = m.get('trade_list', [])
    if not trades:
        return {'wsharpe': 0.0, 'trades': 0, 'trades_per_day': 0.0,
                'pool_sharpe': 0.0, 'max_dd_pct': 0.0, 'wr_pct': 0.0,
                'long_trades': 0, 'short_trades': 0,
                'augment_count': 0, 'reverse_on_exit_count': 0,
                'mean_rev_reentry_count': 0, 'hedge_count': 0,
                'follow_through_count': 0,
                'open_at_end_count': int(m.get('open_at_end_count', 0)),
                'metrics': {k: v for k, v in m.items() if k != 'trade_list'},
                'params': params.to_dict()}
    # All trades — including open-at-end MtM'd by engine (rule R2)
    rets_ts = [(t['pnl_pct'], t['exit_ts']) for t in trades]
    ref_ts = max(t['exit_ts'] for t in trades)
    ws, eff, n = time_weighted_pool_sharpe(rets_ts, ref_ts)
    # canonical pool_sharpe via metrics_guard for source-of-truth comparison
    canonical_pool = mg.pool_sharpe([r for r, _ in rets_ts])
    return {
        'wsharpe': ws,
        'trades': n,
        'trades_per_day': n / max(1.0, WINDOW_DAYS),
        'pool_sharpe': float(canonical_pool),
        'engine_pool_sharpe': float(m.get('pool_sharpe', 0)),
        'max_dd_pct': float(m.get('max_dd_pct', 0)),
        'wr_pct': float(m.get('wr_pct', 0)),
        'long_trades': int(m.get('long_trades', 0)),
        'short_trades': int(m.get('short_trades', 0)),
        'augment_count': int(m.get('augment_count', 0)),
        'reverse_on_exit_count': int(m.get('reverse_on_exit_count', 0)),
        'mean_rev_reentry_count': int(m.get('mean_rev_reentry_count', 0)),
        'hedge_count': int(m.get('hedge_wt3m_count', 0)),
        'follow_through_count': int(m.get('follow_through_count', 0)),
        'peak_protect_count': int(m.get('peak_protect_count', 0)),
        'hard_loss_count': int(m.get('hard_loss_count', 0)),
        'open_at_end_count': int(m.get('open_at_end_count', 0)),
        'mtm_pnl_open_pct': float(m.get('mtm_pnl_open_pct', 0.0)),
        'metrics': {k: v for k, v in m.items() if k != 'trade_list'},
        'params': params.to_dict(),
    }


def reconfig_one_sym(sym: str, account: str, ts: int,
                     write_candidates: bool) -> Optional[Dict]:
    base = load_baseline_for_sym(sym) or SymParamsStocks()
    variants = neighborhood_variants(base)
    results: List[Tuple[str, Dict]] = []
    for tag, p in variants:
        r = evaluate_20d(sym, p)
        if r is None:
            continue
        r['variant_tag'] = tag
        results.append((tag, r))
    if not results:
        return None

    eligible = [(t, r) for t, r in results if r['trades'] >= MIN_TRADES_FOR_OPINION]
    if not eligible:
        eligible = results
    winner_tag, winner_r = max(eligible, key=lambda x: x[1]['wsharpe'])
    base_r = next((r for t, r in results if t == 'baseline'), winner_r)
    promote = (
        winner_r['wsharpe'] >= PROMOTE_WSHARPE_FLOOR and
        winner_r['wsharpe'] - base_r['wsharpe'] >= PROMOTE_DELTA_WSHARPE and
        winner_r['trades_per_day'] >= TPD_MIN and
        winner_r['trades_per_day'] <= TPD_MAX
    )

    if write_candidates:
        cand_dir = OUT_BASE / account / '_candidates'
        cand_dir.mkdir(parents=True, exist_ok=True)
        cand_path = cand_dir / f'cand_{sym}_{ts}.json'
        try:
            payload = {
                'sym': sym, 'account': account, 'ts': ts,
                'window_days': WINDOW_DAYS,
                'day_decay_factor': DAY_DECAY_FACTOR,
                'winner_tag': winner_tag,
                'baseline_wsharpe': base_r['wsharpe'],
                'variants': [
                    {
                        'tag': t,
                        'wsharpe': r['wsharpe'],
                        'pool_sharpe': r['pool_sharpe'],
                        'trades': r['trades'],
                        'trades_per_day': r['trades_per_day'],
                        'wr_pct': r['wr_pct'],
                        'max_dd_pct': r['max_dd_pct'],
                        'long_trades': r['long_trades'],
                        'short_trades': r['short_trades'],
                        'augment_count': r['augment_count'],
                        'reverse_on_exit_count': r['reverse_on_exit_count'],
                        'mean_rev_reentry_count': r['mean_rev_reentry_count'],
                        'hedge_count': r['hedge_count'],
                        'follow_through_count': r['follow_through_count'],
                        'open_at_end_count': r['open_at_end_count'],
                        'params': r['params'],
                    }
                    for t, r in results
                ],
            }
            cand_path.write_text(json.dumps(payload, indent=2, default=str))
        except Exception:
            pass

    return {
        'sym': sym,
        'account': account,
        'winner_tag': winner_tag,
        'winner_wsharpe': winner_r['wsharpe'],
        'baseline_wsharpe': base_r['wsharpe'],
        'delta_wsharpe': winner_r['wsharpe'] - base_r['wsharpe'],
        'trades': winner_r['trades'],
        'trades_per_day': winner_r['trades_per_day'],
        'pool_sharpe': winner_r['pool_sharpe'],
        'wr_pct': winner_r['wr_pct'],
        'max_dd_pct': winner_r['max_dd_pct'],
        'long_trades': winner_r['long_trades'],
        'short_trades': winner_r['short_trades'],
        'augment_count': winner_r['augment_count'],
        'reverse_on_exit_count': winner_r['reverse_on_exit_count'],
        'mean_rev_reentry_count': winner_r['mean_rev_reentry_count'],
        'hedge_count': winner_r['hedge_count'],
        'follow_through_count': winner_r['follow_through_count'],
        'open_at_end_count': winner_r['open_at_end_count'],
        'promote': promote,
        'params': winner_r['params'],
        'all_variants': [{'tag': t, 'wsharpe': r['wsharpe'], 'trades': r['trades']}
                         for t, r in results],
    }


_WORKER_ARGS: Dict[str, object] = {}


def _worker_init(account: str, ts: int, write_candidates: bool):
    _WORKER_ARGS['account'] = account
    _WORKER_ARGS['ts'] = ts
    _WORKER_ARGS['write_candidates'] = write_candidates


def _worker(sym: str):
    try:
        return reconfig_one_sym(
            sym,
            str(_WORKER_ARGS.get('account', '')),
            int(_WORKER_ARGS.get('ts', int(time.time()))),
            bool(_WORKER_ARGS.get('write_candidates', True)),
        )
    except Exception:
        return {'sym': sym, 'error': traceback.format_exc()[-400:]}


def write_active_20d(account: str, results: List[Dict], dry_run: bool) -> int:
    out_dir = OUT_BASE / account
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'active_config_20d.json'
    payload: Dict[str, Dict] = {}
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
            'trades_20d': r['trades'],
            'trades_per_day': r['trades_per_day'],
            'pool_sharpe': r['pool_sharpe'],
            'wr_pct': r['wr_pct'],
            'max_dd_pct': r['max_dd_pct'],
            'long_trades': r['long_trades'],
            'short_trades': r['short_trades'],
            'augment_count': r['augment_count'],
            'reverse_on_exit_count': r['reverse_on_exit_count'],
            'mean_rev_reentry_count': r['mean_rev_reentry_count'],
            'hedge_count': r['hedge_count'],
            'follow_through_count': r['follow_through_count'],
            'open_at_end_count': r['open_at_end_count'],
            'promote': r['promote'],
            'overrides': r['params'],
            'updated_at': int(time.time()),
        }
        if r['promote']:
            promoted += 1
    if not dry_run:
        out_path.write_text(json.dumps(payload, indent=2, default=str))
    return promoted


def write_run_audit(account: str, ts: int, syms: List[str], results: List[Dict],
                    elapsed_s: float, dry_run: bool) -> Optional[Path]:
    if dry_run:
        return None
    runs_dir = OUT_BASE / account / 'runs'
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_path = runs_dir / f'run_{ts}.json'
    n_syms_done = sum(1 for r in results if r and 'error' not in r)
    diagnostic_tag = (n_syms_done < mg.MIN_SYMS_STOCKS)
    audit = {
        'account': account,
        'ts': ts,
        'window_days': WINDOW_DAYS,
        'day_decay_factor': DAY_DECAY_FACTOR,
        'recency_weights_first10': [stocks_day_weight(i) for i in range(10)],
        'promote_delta_wsharpe': PROMOTE_DELTA_WSHARPE,
        'promote_wsharpe_floor': PROMOTE_WSHARPE_FLOOR,
        'tpd_window': [TPD_MIN, TPD_MAX],
        'syms_requested': len(syms),
        'syms_completed': n_syms_done,
        'elapsed_s': elapsed_s,
        'diagnostic': diagnostic_tag,
        'diagnostic_note': (f'[DIAGNOSTIC ONLY · n_syms={n_syms_done} '
                            f'< MIN_SYMS_STOCKS={mg.MIN_SYMS_STOCKS} · '
                            f'years={WINDOW_DAYS/365.25:.3f}]'
                            if diagnostic_tag else ''),
        'engine': 'per_sym_engine_stocks.simulate_dual_stocks',
        'source_baseline': str(ACTIVE_CFG),
        'mode': 'tradier',
        'ltf': '5m',
        'bars_per_day_stocks': BARS_PER_DAY_STOCKS,
        'results': results,
    }
    run_path.write_text(json.dumps(audit, indent=2, default=str))
    return run_path


def cycle(account: str, syms: List[str], workers: int,
          dry_run: bool, write_candidates: bool) -> Dict:
    print(f"[per_sym_20d_stocks] cycle {account} | {len(syms)} syms | "
          f"workers={workers} | dry_run={dry_run}", flush=True)
    if len(syms) < mg.MIN_SYMS_STOCKS:
        print(f"  [DIAGNOSTIC] n_syms={len(syms)} < MIN_SYMS_STOCKS="
              f"{mg.MIN_SYMS_STOCKS} — output tagged as diagnostic only",
              flush=True)
    ts = int(time.time())
    t0 = time.time()
    results: List[Dict] = []
    if workers <= 1:
        _worker_init(account, ts, write_candidates)
        for i, s in enumerate(syms, 1):
            r = _worker(s)
            if r is not None:
                results.append(r)
            if i % max(1, len(syms) // 10) == 0 or i == len(syms):
                elapsed = time.time() - t0
                print(f"  [{i}/{len(syms)}] elapsed={elapsed:.0f}s "
                      f"avg={elapsed/i:.1f}s/sym", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers,
                                 initializer=_worker_init,
                                 initargs=(account, ts, write_candidates)) as ex:
            futs = {ex.submit(_worker, s): s for s in syms}
            done = 0
            for fut in as_completed(futs):
                done += 1
                try:
                    r = fut.result()
                except Exception as e:
                    print(f"  worker exc: {e}", flush=True)
                    continue
                if r is not None:
                    results.append(r)
                if done % max(1, len(syms) // 10) == 0 or done == len(syms):
                    elapsed = time.time() - t0
                    print(f"  [{done}/{len(syms)}] elapsed={elapsed:.0f}s "
                          f"avg={elapsed/done:.1f}s/sym", flush=True)
    elapsed = time.time() - t0
    promoted = write_active_20d(account, results, dry_run=dry_run)
    audit_path = write_run_audit(account, ts, syms, results, elapsed, dry_run=dry_run)
    diag = '[DIAGNOSTIC]' if len(syms) < mg.MIN_SYMS_STOCKS else ''
    print(f"[per_sym_20d_stocks] {diag} done in {elapsed:.0f}s | "
          f"promoted={promoted}/{len(results)} "
          f"| audit={audit_path}", flush=True)

    # Print best 10 by wsharpe for quick eyeball
    valid = [r for r in results if r and 'error' not in r]
    valid.sort(key=lambda x: x.get('winner_wsharpe', -999), reverse=True)
    if valid:
        print(f"  Top {min(10, len(valid))} by wsharpe:", flush=True)
        for r in valid[:10]:
            print(f"    {r['sym']:<8s} tag={r['winner_tag']:<18s} "
                  f"wsharpe={r['winner_wsharpe']:+.3f} "
                  f"Δ={r['delta_wsharpe']:+.3f} "
                  f"tr={r['trades']:>4d} tpd={r['trades_per_day']:.2f} "
                  f"pool={r['pool_sharpe']:+.3f} "
                  f"dd={r['max_dd_pct']:5.2f}% promote={r['promote']}", flush=True)
    return {'completed': len(results), 'promoted': promoted, 'elapsed_s': elapsed,
            'diagnostic': len(syms) < mg.MIN_SYMS_STOCKS}


def load_struct_v4_universe() -> List[str]:
    if not STRUCT_V4_UNIVERSE.exists():
        return []
    try:
        d = json.loads(STRUCT_V4_UNIVERSE.read_text())
    except Exception:
        return []
    return [s for s in d.get('symbols', []) if isinstance(s, str)]


def load_account_syms(account: str, include_universe: bool) -> List[str]:
    """Load union of {symbols_<acct>_long.json, symbols_<acct>_short.json}
    optionally union with struct_v4_universe.json. Deduplicate and keep only syms
    whose NPZ exists."""
    files = {
        'trb': ['symbols_trb_long.json', 'symbols_trb_short.json'],
        'trc': ['symbols_trc_long.json', 'symbols_trc_short.json'],
    }.get(account, [])
    syms: List[str] = []
    seen = set()
    for f in files:
        p = ROOT / f
        if not p.exists():
            continue
        try:
            raw = re.sub(r',(\s*[\]}])', r'\1', p.read_text())
            data = json.loads(raw)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for s in data:
            if not isinstance(s, str):
                continue
            if s in seen:
                continue
            if not (NPZ_DIR / f'{s}.npz').exists():
                continue
            seen.add(s)
            syms.append(s)
    if include_universe:
        for s in load_struct_v4_universe():
            if s in seen:
                continue
            if not (NPZ_DIR / f'{s}.npz').exists():
                continue
            seen.add(s)
            syms.append(s)
    return syms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--account', required=True, choices=list(ACCOUNTS))
    ap.add_argument('--syms', default='',
                    help='comma-sep override (default: account universe ∪ struct_v4)')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--max-syms', type=int, default=0,
                    help='cap symbol count (0 = no cap). Useful for dry-runs.')
    ap.add_argument('--no-universe', action='store_true',
                    help='skip struct_v4_universe.json union — only account symbols')
    ap.add_argument('--no-candidates', action='store_true',
                    help='skip writing per-variant candidate files')
    ap.add_argument('--dry-run', action='store_true',
                    help='do not write active_config_20d.json or run audit')
    ap.add_argument('--daemon', action='store_true',
                    help='cycle every --interval-s')
    ap.add_argument('--interval-s', type=int, default=3600)
    args = ap.parse_args()

    if args.syms:
        candidate = [s.strip() for s in args.syms.split(',') if s.strip()]
        syms = [s for s in candidate if (NPZ_DIR / f'{s}.npz').exists()]
        missing = [s for s in candidate if s not in syms]
        if missing:
            print(f"  skipping {len(missing)} syms without NPZ: {missing[:10]}",
                  flush=True)
    else:
        syms = load_account_syms(args.account, include_universe=not args.no_universe)
    if args.max_syms and args.max_syms > 0:
        syms = syms[:args.max_syms]
    if not syms:
        print(f"no syms for account={args.account}")
        return 1

    # dry-run implies no per-variant candidate writes either
    write_cands = (not args.no_candidates) and (not args.dry_run)

    if args.daemon:
        while True:
            t0 = time.time()
            try:
                cycle(args.account, syms, args.workers,
                      dry_run=args.dry_run,
                      write_candidates=write_cands)
            except Exception:
                traceback.print_exc()
            elapsed = time.time() - t0
            sleep_s = max(60, args.interval_s - int(elapsed))
            print(f"[per_sym_20d_stocks] cycle done in {elapsed:.0f}s; "
                  f"sleeping {sleep_s}s", flush=True)
            time.sleep(sleep_s)
    else:
        cycle(args.account, syms, args.workers,
              dry_run=args.dry_run,
              write_candidates=write_cands)
    return 0


if __name__ == '__main__':
    sys.exit(main())
