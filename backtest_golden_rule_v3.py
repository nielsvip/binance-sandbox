"""GOLDEN_RULE v3 backtest harness — universe-gated, pattern-gated, LTF/HTF bucketed.

Default mode is tradier (the user's current focus — stocks via trb).
Universe loaded from symbols_trb_long.json + symbols_trb_short.json (115 stocks total).

Single-config or grid-sweep:

    # Single config:
    python3 backtest_golden_rule_v3.py --mode tradier --start 2024-06-01 \\
        --ltf-req 1 --htf-req 2 --per-tf-min 7 --global-min 2 --tp 2.0 --sl 0.7

    # Grid sweep (writes one canonical row per cell):
    python3 backtest_golden_rule_v3.py --mode tradier --start 2024-06-01 --grid

All Sharpe outputs route through metrics_guard per CLAUDE.md NO-LIES MANDATE.
"""
from __future__ import annotations
import argparse, sys, os, time, json, glob, itertools
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import golden_rule_v3 as gr3v
from metrics_guard import standard_metric_set, validate_and_format_sharpe, tier_name, write_sharpe_row, format_standard_set


def _load_npz(path: str):
    z = np.load(path, allow_pickle=True)
    return {k: z[k] for k in z.files}


def _ind_at_bar(npz: dict, idx: int) -> dict:
    out = {}
    for k, arr in npz.items():
        if not isinstance(arr, np.ndarray) or arr.ndim != 1 or idx >= arr.shape[0]:
            continue
        v = arr[idx]
        if isinstance(v, (bytes, np.bytes_)):
            try: v = v.decode()
            except Exception: v = str(v)
        out[k] = v
    return out


def _resolve_universe_npz(npz_dir: Path, mode: str, syms_to_use: List[str]) -> List[Tuple[str, str]]:
    paired = []
    for sym in syms_to_use:
        path = npz_dir / f"{sym}.npz"
        if path.exists():
            paired.append((sym.upper(), str(path)))
    return paired


def _start_idx(npz: dict, start_iso: str, base_tf: str) -> int:
    import datetime as dt
    target = dt.datetime.fromisoformat(start_iso).timestamp()
    for cand in ('timestamp', f'timestamp_{base_tf}', 'timestamps', 'ts', f'ts_{base_tf}'):
        if cand in npz:
            ts = npz[cand]
            if isinstance(ts, np.ndarray) and ts.ndim == 1:
                t0 = float(ts[0]) if ts.dtype.kind != 'M' else ts[0].astype('datetime64[s]').astype('int64')
                if t0 > 1e12: target *= 1000
                if ts.dtype.kind == 'M':
                    target_dt = np.datetime64(int(target), 's')
                    return max(0, min(int(np.searchsorted(ts, target_dt)), ts.shape[0] - 1))
                return max(0, min(int(np.searchsorted(ts, target)), ts.shape[0] - 1))
    return 0


class _Cfg:
    """Mock config for evaluator overrides."""
    pass


def run_backtest(mode: str, start_iso: str, npz_dir: Path,
                 ltf_req: int, htf_req: int, per_tf_min: int, global_min: int,
                 tp: float, sl: float, max_bars: int = 200,
                 sample_every: int = 1, require_pattern: bool = True,
                 verbose: bool = True) -> dict:
    cfg = _Cfg()
    cfg.GR3_LTF_REQ = ltf_req
    cfg.GR3_HTF_REQ = htf_req
    cfg.GR3_PER_TF_MIN_INDS = per_tf_min
    cfg.GR3_GLOBAL_MIN = global_min
    cfg.GR3_TP_PCT = tp
    cfg.GR3_SL_PCT = sl
    cfg.GR3_MAX_BARS = max_bars
    cfg.GR3_REQUIRE_PATTERN = require_pattern

    longs, shorts = gr3v.load_trb_universe()
    syms_to_use = sorted(longs | shorts)
    base_tf = '5m' if mode == 'tradier' else '3m'
    paired = _resolve_universe_npz(npz_dir, mode, syms_to_use)
    if verbose:
        print(f"[GR3_BT] mode={mode} universe={len(syms_to_use)} npz_found={len(paired)} "
              f"ltf_req={ltf_req} htf_req={htf_req} per_tf_min={per_tf_min} global_min={global_min} "
              f"tp={tp}% sl={sl}% start={start_iso}", flush=True)

    returns_by_sym: Dict[str, List[float]] = {}
    fires_total = 0
    earliest_ts = None
    latest_ts = None
    t0 = time.time()

    for sidx, (sym, path) in enumerate(paired):
        try:
            npz = _load_npz(path)
        except Exception as e:
            if verbose: print(f"[GR3_BT] {sym}: load err {e}", flush=True)
            continue
        close_field = f'close_{base_tf}' if f'close_{base_tf}' in npz else 'close'
        if close_field not in npz:
            continue
        close = npz[close_field]
        if not isinstance(close, np.ndarray) or close.shape[0] < 100:
            continue
        n_bars = close.shape[0]
        start_idx = _start_idx(npz, start_iso, base_tf)
        ts_field = f'timestamp_{base_tf}' if f'timestamp_{base_tf}' in npz else 'timestamp' if 'timestamp' in npz else None
        if ts_field is not None:
            ts_arr = npz[ts_field]
            try:
                if ts_arr.dtype.kind == 'M':
                    t_s = ts_arr[start_idx].astype('datetime64[s]').astype('int64')
                    t_e = ts_arr[-1].astype('datetime64[s]').astype('int64')
                else:
                    t_s = float(ts_arr[start_idx]); t_e = float(ts_arr[-1])
                    if t_s > 1e12: t_s /= 1000; t_e /= 1000
                    t_s = int(t_s); t_e = int(t_e)
                if earliest_ts is None or t_s < earliest_ts: earliest_ts = t_s
                if latest_ts is None or t_e > latest_ts: latest_ts = t_e
            except Exception: pass

        sym_rets = []
        for is_long in (True, False):
            # Universe gate at outer level — skip directly
            if is_long and sym not in longs: continue
            if (not is_long) and sym not in shorts: continue
            in_pos = False
            entry_px = 0.0
            entry_idx = 0
            entry_mult = 1.0
            # 2026-05-10 — per-(sym, side) state for FRESH-breakout / RETEST tracking
            pattern_state = {
                'prev_above': False,
                'breakout_ts': -10**9,
                'cur_bar': 0,
                'base_tf': base_tf,
                'retest_window_bars': 80,  # ~6.5h on 5m
            }
            for i in range(start_idx, n_bars, sample_every):
                px = float(close[i])
                if px <= 0 or px != px: continue
                ind = _ind_at_bar(npz, i)
                pattern_state['cur_bar'] = i
                if not in_pos:
                    sig = gr3v.evaluate_golden_rule_v3(sym, ind, px, is_long, mode=mode, config=cfg, state=pattern_state)
                    if sig.fire:
                        in_pos = True
                        entry_px = px
                        entry_idx = i
                        entry_mult = max(0.1, float(sig.mult))
                        fires_total += 1
                else:
                    bars_held = i - entry_idx
                    should_exit, _ = gr3v.evaluate_exit_v3(entry_px, px, is_long, bars_held, ind, mode=mode, config=cfg)
                    if should_exit:
                        gain_pct = (px - entry_px) / entry_px * 100.0
                        if not is_long: gain_pct = -gain_pct
                        sym_rets.append(gain_pct * entry_mult)
                        in_pos = False
                # Update prev_above AFTER evaluation for next-bar fresh-breakout detection
                dc_h_1h = float(ind.get('dc_high_1h', 0) or 0)
                dc_l_1h = float(ind.get('dc_low_1h', 0) or 0)
                bb_u_1h = float(ind.get('bb_upper_1h', 0) or 0)
                bb_l_1h = float(ind.get('bb_lower_1h', 0) or 0)
                if is_long:
                    pattern_state['prev_above'] = (dc_h_1h > 0 and px > dc_h_1h) or (bb_u_1h > 0 and px > bb_u_1h)
                else:
                    pattern_state['prev_above'] = (dc_l_1h > 0 and px < dc_l_1h) or (bb_l_1h > 0 and px < bb_l_1h)
            if in_pos and entry_px > 0:
                final_px = float(close[-1])
                if final_px > 0:
                    g = (final_px - entry_px) / entry_px * 100.0
                    if not is_long: g = -g
                    sym_rets.append(g * entry_mult)
        if sym_rets:
            returns_by_sym[sym] = sym_rets
        if verbose and (sidx + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"[GR3_BT] processed {sidx+1}/{len(paired)} syms — fires={fires_total} trades={sum(len(v) for v in returns_by_sym.values())} elapsed={elapsed:.0f}s", flush=True)

    if earliest_ts and latest_ts and latest_ts > earliest_ts:
        years = (latest_ts - earliest_ts) / (365.25 * 86400)
    else:
        years = 0.5
    metrics = standard_metric_set(returns_by_sym, years)
    metrics['fires_total'] = fires_total
    metrics['mode'] = mode
    metrics['start'] = start_iso
    metrics['elapsed_s'] = time.time() - t0
    metrics['ltf_req'] = ltf_req
    metrics['htf_req'] = htf_req
    metrics['per_tf_min'] = per_tf_min
    metrics['global_min'] = global_min
    metrics['tp'] = tp
    metrics['sl'] = sl
    return metrics


def _write_canonical(out: Path, metrics: dict, tag: str):
    pool_s = float(metrics.get('pool_sharpe', 0))
    sym_s = float(metrics.get('sym_sharpe', 0))
    avg_g = float(metrics.get('avg_gain_trade', 0))
    gain_yr = float(metrics.get('gain_per_yr', 0))
    gain_sym_yr = float(metrics.get('gain_sym_yr', 0))
    tot = float(metrics.get('total_gain_pct', 0))
    n_syms = int(metrics.get('n_syms', 0))
    years = float(metrics.get('years', 0))
    trades = int(metrics.get('trades', 0))
    row = {
        'iter': f'gr3_{metrics["mode"]}_{tag}_{int(time.time())}',
        'pool_sharpe': pool_s, 'sym_sharpe': sym_s, 'acc_gain_pct': tot,
        'gain_sym_yr': gain_sym_yr, 'avg_gain_trade': avg_g, 'gain_per_yr': gain_yr,
        'max_dd_pct': 0.0, 'trades': trades, 'gain_vs_bh': 0.0,
        'elapsed_s': metrics.get('elapsed_s', 0), 'overrides_count': 0,
        'reliable': trades >= 1000 and n_syms >= 30, 'useless': trades < 100,
        'overrides_json': json.dumps({k: metrics[k] for k in ('ltf_req','htf_req','per_tf_min','global_min','tp','sl')}),
        'n_syms': n_syms, 'years': years, 'mode': metrics['mode'], 'start': metrics['start'],
        '_meta': f'GR3 {tag} fires={metrics.get("fires_total",0)} cfg={metrics["ltf_req"]}/{metrics["htf_req"]}/{metrics["per_tf_min"]}/{metrics["global_min"]} tp={metrics["tp"]} sl={metrics["sl"]}'
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_sharpe_row(out, row, mode='stocks' if metrics['mode'] == 'tradier' else 'crypto')
        return True
    except Exception as e:
        print(f"[GR3_BT] write_sharpe_row REFUSED for {tag}: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['crypto', 'tradier'], default='tradier')
    ap.add_argument('--start', default='2024-06-01')
    ap.add_argument('--npz-dir', default='backtest_v8/indicators')
    ap.add_argument('--sample-every', type=int, default=20)
    ap.add_argument('--out', default='data/sweep_results/canonical_gr3.csv')
    ap.add_argument('--ltf-req', type=int, default=1)
    ap.add_argument('--htf-req', type=int, default=2)
    ap.add_argument('--per-tf-min', type=int, default=7)
    ap.add_argument('--global-min', type=int, default=2)
    ap.add_argument('--tp', type=float, default=2.0)
    ap.add_argument('--sl', type=float, default=0.7)
    ap.add_argument('--no-pattern', action='store_true', help='disable GOLDEN_RULE pattern gate')
    ap.add_argument('--grid', action='store_true', help='run grid sweep over key dimensions')
    ap.add_argument('--tag', default='single')
    args = ap.parse_args()

    npz_dir = Path(args.npz_dir)
    out = Path(args.out)

    if args.grid:
        # Coarse grid: ltf_req × htf_req × per_tf_min × (tp, sl)
        grid = list(itertools.product(
            [0, 1, 2],          # ltf_req
            [1, 2, 3],          # htf_req
            [5, 7, 9],          # per_tf_min
            [(1.5, 0.5), (2.0, 0.7), (3.0, 1.0)],  # (tp, sl)
        ))
        print(f"[GR3_BT_GRID] running {len(grid)} configs sequentially")
        results = []
        for cfg_i, (ltf, htf, ptm, (tp, sl)) in enumerate(grid):
            if ltf == 0 and htf == 0:  # skip degenerate cell
                continue
            tag = f'L{ltf}H{htf}P{ptm}TP{tp}SL{sl}'
            print(f"\n=== [{cfg_i+1}/{len(grid)}] {tag} ===", flush=True)
            metrics = run_backtest(args.mode, args.start, npz_dir, ltf, htf, ptm,
                                   args.global_min, tp, sl, sample_every=args.sample_every,
                                   require_pattern=not args.no_pattern, verbose=False)
            ps = metrics.get('pool_sharpe', 0); tr = metrics.get('trades', 0)
            ns = metrics.get('n_syms', 0); ay = metrics.get('avg_gain_trade', 0)
            print(f"  → pool_sharpe={ps:+.4f} trades={tr} n_syms={ns} avg_gain={ay:+.3f}% tier={tier_name(ps)}", flush=True)
            _write_canonical(out, metrics, tag)
            results.append((tag, ps, tr, ns, ay, metrics))
        print("\n=== GRID SWEEP RESULTS — sorted by pool_sharpe descending ===")
        for tag, ps, tr, ns, ay, _ in sorted(results, key=lambda x: -x[1])[:15]:
            print(f"  {tag:30s} pool={ps:+.4f} trades={tr:5d} n_syms={ns:3d} avg={ay:+.3f}% tier={tier_name(ps)}")
    else:
        metrics = run_backtest(args.mode, args.start, npz_dir, args.ltf_req, args.htf_req,
                               args.per_tf_min, args.global_min, args.tp, args.sl,
                               sample_every=args.sample_every,
                               require_pattern=not args.no_pattern)
        print("\n=== GR3 backtest result ===")
        print(json.dumps({k: v for k, v in metrics.items() if not isinstance(v, dict)}, indent=2, default=str))
        ps = float(metrics.get('pool_sharpe', 0))
        print()
        try:
            print(format_standard_set({
                'pool_sharpe': ps, 'sym_sharpe': float(metrics.get('sym_sharpe', 0)),
                'avg_gain_trade': float(metrics.get('avg_gain_trade', 0)),
                'gain_per_yr': float(metrics.get('gain_per_yr', 0)),
                'gain_sym_yr': float(metrics.get('gain_sym_yr', 0)),
                'trades': int(metrics.get('trades', 0)), 'max_dd_pct': 0.0,
                'n_syms': int(metrics.get('n_syms', 0)), 'years': float(metrics.get('years', 0)),
            }, mode='stocks' if args.mode == 'tradier' else 'crypto'))
            print(f"  tier={tier_name(ps)}")
        except Exception as e:
            print(f"format_standard_set err: {e}")
        _write_canonical(out, metrics, args.tag)


if __name__ == '__main__':
    main()
