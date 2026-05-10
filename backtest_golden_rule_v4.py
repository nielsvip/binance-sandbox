"""GOLDEN_RULE v4 backtest harness — counter-trend entry on directional universe.

Default mode is tradier (stocks). Universe from symbols_trb_long/short.json.
All Sharpe routed through metrics_guard per CLAUDE.md NO-LIES MANDATE.

Target: baseline pool_sharpe ≥ 0.7 over 100+ syms × 2yr.
"""
from __future__ import annotations
import argparse, sys, time, json
from pathlib import Path
from typing import Dict, List
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import golden_rule_v4 as gr4v
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


def _start_idx(npz: dict, start_iso: str, base_tf: str) -> int:
    import datetime as dt
    target = dt.datetime.fromisoformat(start_iso).timestamp()
    for cand in ('timestamp', f'timestamp_{base_tf}', 'timestamps', 'ts', f'ts_{base_tf}'):
        if cand in npz:
            ts = npz[cand]
            if isinstance(ts, np.ndarray) and ts.ndim == 1:
                if ts.dtype.kind == 'M':
                    target_dt = np.datetime64(int(target), 's')
                    return max(0, min(int(np.searchsorted(ts, target_dt)), ts.shape[0] - 1))
                t0 = float(ts[0])
                if t0 > 1e12: target *= 1000
                return max(0, min(int(np.searchsorted(ts, target)), ts.shape[0] - 1))
    return 0


class _Cfg:
    pass


def run_backtest(mode: str, start_iso: str, npz_dir: Path,
                 ltf_min: int, htf_min: int, global_min: int,
                 tp: float, sl: float, max_bars: int = 200,
                 sample_every: int = 20, require_dc_extreme: bool = True,
                 verbose: bool = True) -> dict:
    cfg = _Cfg()
    cfg.GR4_LTF_MIN_SCORE = ltf_min
    cfg.GR4_HTF_MIN_SCORE = htf_min
    cfg.GR4_GLOBAL_MIN = global_min
    cfg.GR4_TP_PCT = tp; cfg.GR4_SL_PCT = sl
    cfg.GR4_MAX_BARS = max_bars
    cfg.GR4_REQUIRE_DC_EXTREME = require_dc_extreme

    longs, shorts = gr4v.load_trb_universe()
    syms = sorted(longs | shorts)
    base_tf = '5m' if mode == 'tradier' else '3m'

    paired = []
    for sym in syms:
        path = npz_dir / f"{sym}.npz"
        if path.exists():
            paired.append((sym.upper(), str(path)))
    if verbose:
        print(f"[GR4_BT] mode={mode} universe={len(syms)} found={len(paired)} "
              f"ltf_min={ltf_min} htf_min={htf_min} global_min={global_min} "
              f"tp={tp}% sl={sl}% start={start_iso}", flush=True)

    returns_by_sym: Dict[str, List[float]] = {}
    fires_total = 0; earliest = None; latest = None
    t0 = time.time()
    for sidx, (sym, path) in enumerate(paired):
        try:
            npz = _load_npz(path)
        except Exception as e:
            if verbose: print(f"[GR4_BT] {sym}: load err {e}")
            continue
        close_field = f'close_{base_tf}' if f'close_{base_tf}' in npz else 'close'
        if close_field not in npz:
            continue
        close = npz[close_field]
        if not isinstance(close, np.ndarray) or close.shape[0] < 100:
            continue
        n_bars = close.shape[0]
        sidx_bar = _start_idx(npz, start_iso, base_tf)
        ts_field = f'timestamp_{base_tf}' if f'timestamp_{base_tf}' in npz else 'timestamp' if 'timestamp' in npz else None
        if ts_field is not None:
            try:
                ts_arr = npz[ts_field]
                if ts_arr.dtype.kind == 'M':
                    t_s = ts_arr[sidx_bar].astype('datetime64[s]').astype('int64')
                    t_e = ts_arr[-1].astype('datetime64[s]').astype('int64')
                else:
                    t_s = float(ts_arr[sidx_bar]); t_e = float(ts_arr[-1])
                    if t_s > 1e12: t_s /= 1000; t_e /= 1000
                    t_s = int(t_s); t_e = int(t_e)
                if earliest is None or t_s < earliest: earliest = t_s
                if latest is None or t_e > latest: latest = t_e
            except Exception: pass

        sym_rets = []
        for is_long in (True, False):
            if is_long and sym not in longs: continue
            if (not is_long) and sym not in shorts: continue
            in_pos = False; entry_px = 0.0; entry_idx = 0; entry_mult = 1.0
            for i in range(sidx_bar, n_bars, sample_every):
                px = float(close[i])
                if px <= 0 or px != px: continue
                ind = _ind_at_bar(npz, i)
                if not in_pos:
                    sig = gr4v.evaluate_golden_rule_v4(sym, ind, px, is_long, mode=mode, config=cfg)
                    if sig.fire:
                        in_pos = True; entry_px = px; entry_idx = i
                        entry_mult = max(0.1, float(sig.mult))
                        fires_total += 1
                else:
                    bars = i - entry_idx
                    se, _ = gr4v.evaluate_exit_v4(entry_px, px, is_long, bars, ind, mode=mode, config=cfg)
                    if se:
                        gp = (px - entry_px) / entry_px * 100.0
                        if not is_long: gp = -gp
                        sym_rets.append(gp)  # NOT mult-weighted (v3 found that hurt)
                        in_pos = False
            if in_pos and entry_px > 0:
                fp = float(close[-1])
                if fp > 0:
                    g = (fp - entry_px) / entry_px * 100.0
                    if not is_long: g = -g
                    sym_rets.append(g)
        if sym_rets:
            returns_by_sym[sym] = sym_rets
        if verbose and (sidx + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"[GR4_BT] {sidx+1}/{len(paired)} fires={fires_total} trades={sum(len(v) for v in returns_by_sym.values())} elapsed={elapsed:.0f}s", flush=True)

    if earliest and latest and latest > earliest:
        years = (latest - earliest) / (365.25 * 86400)
    else:
        years = 0.5
    metrics = standard_metric_set(returns_by_sym, years)
    metrics['fires_total'] = fires_total
    metrics['mode'] = mode
    metrics['start'] = start_iso
    metrics['elapsed_s'] = time.time() - t0
    metrics['ltf_min'] = ltf_min; metrics['htf_min'] = htf_min
    metrics['global_min'] = global_min; metrics['tp'] = tp; metrics['sl'] = sl
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
        'iter': f'gr4_{metrics["mode"]}_{tag}_{int(time.time())}',
        'pool_sharpe': pool_s, 'sym_sharpe': sym_s, 'acc_gain_pct': tot,
        'gain_sym_yr': gain_sym_yr, 'avg_gain_trade': avg_g, 'gain_per_yr': gain_yr,
        'max_dd_pct': 0.0, 'trades': trades, 'gain_vs_bh': 0.0,
        'elapsed_s': metrics.get('elapsed_s', 0), 'overrides_count': 0,
        'reliable': trades >= 1000 and n_syms >= 30, 'useless': trades < 100,
        'overrides_json': json.dumps({k: metrics.get(k) for k in ('ltf_min','htf_min','global_min','tp','sl')}),
        'n_syms': n_syms, 'years': years, 'mode': metrics['mode'], 'start': metrics['start'],
        '_meta': f'GR4 {tag} fires={metrics.get("fires_total",0)} cfg={metrics["ltf_min"]}/{metrics["htf_min"]}/{metrics["global_min"]} tp={metrics["tp"]} sl={metrics["sl"]}'
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_sharpe_row(out, row, mode='stocks' if metrics['mode'] == 'tradier' else 'crypto')
    except Exception as e:
        print(f"[GR4_BT] write_sharpe_row REFUSED for {tag}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['crypto', 'tradier'], default='tradier')
    ap.add_argument('--start', default='2024-06-01')
    ap.add_argument('--npz-dir', default='backtest_v8/indicators')
    ap.add_argument('--sample-every', type=int, default=20)
    ap.add_argument('--out', default='data/sweep_results/canonical_gr4.csv')
    ap.add_argument('--ltf-min', type=int, default=12)
    ap.add_argument('--htf-min', type=int, default=18)
    ap.add_argument('--global-min', type=int, default=1)
    ap.add_argument('--tp', type=float, default=2.5)
    ap.add_argument('--sl', type=float, default=0.8)
    ap.add_argument('--max-bars', type=int, default=200)
    ap.add_argument('--no-dc-extreme', action='store_true', help='disable DC extreme requirement')
    ap.add_argument('--tag', default='single')
    args = ap.parse_args()
    npz_dir = Path(args.npz_dir)
    metrics = run_backtest(args.mode, args.start, npz_dir, args.ltf_min, args.htf_min,
                           args.global_min, args.tp, args.sl, args.max_bars,
                           sample_every=args.sample_every,
                           require_dc_extreme=not args.no_dc_extreme)
    print("\n=== GR4 result ===")
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
    _write_canonical(Path(args.out), metrics, args.tag)


if __name__ == '__main__':
    main()
