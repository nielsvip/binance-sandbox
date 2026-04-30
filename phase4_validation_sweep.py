"""Phase 4 validation sweep — A/B all combinations of the 3 new vec flags
on full 48-sym (crypto) / 100+-sym (tradier) universe to confirm the gates
hold above sample floor. Routes results through metrics_guard exclusively.

Usage on S1: python phase4_validation_sweep.py --mode crypto
Usage on S2: python phase4_validation_sweep.py --mode tradier
"""
import os, sys, time, argparse, glob, json
from itertools import product
from pathlib import Path

import numpy as np

# Disable FINAL_BROKEN_RATE guard noise (we're running short sweeps for triage)
os.environ.setdefault("V8_RATE_GUARD_DISABLED", "1")

sys.path.insert(0, str(Path(__file__).parent))

from v8_quick_engine import QuickConfig, simulate, iter_npz, _resolve_npz_dir
import metrics_guard


CRYPTO_USDC_MAJORS = ["BTC", "ETH", "SOL", "ADA", "BNB", "AVAX", "XRP", "LINK", "LTC", "UNI", "DOGE"]
CRYPTO_USDT_LEGACY = [
    "1INCH", "ALGO", "ANKR", "ATOM", "AXS", "BAND", "BAT", "BEL", "BTCDOM",
    "C98", "CELR", "CHR", "COMP", "COTI", "DASH", "DOT", "EGLD", "ENJ", "ETC",
    "GRT", "GTC", "HOT", "IOST", "IOTA", "IOTX", "KAVA", "KNC", "KSM", "LRC",
    "MANA", "MTL", "NKN", "QTUM", "RLC", "RSR", "RVN", "SAND", "SKL", "SNX",
    "STORJ", "SUSHI", "SXP", "THETA", "TRX", "VET", "XLM", "XMR", "XTZ", "YFI",
]


def _crypto_symbols(npz_dir: str):
    available = set()
    for f in glob.glob(os.path.join(npz_dir, "*.npz")):
        if f.endswith('.npz') and not f.endswith('.bak'):
            base = os.path.basename(f).replace('.npz', '')
            available.add(base)
    out = []
    for m in CRYPTO_USDC_MAJORS:
        if f"{m}USDC" in available:
            out.append(f"{m}USDC")
    for s in CRYPTO_USDT_LEGACY:
        if f"{s}USDT" in available:
            out.append(f"{s}USDT")
    return out


def _tradier_symbols(npz_dir: str):
    out = []
    for f in glob.glob(os.path.join(npz_dir, "*.npz")):
        base = os.path.basename(f).replace('.npz', '')
        if base.endswith('USDT') or base.endswith('USDC') or base == 'BTCDOMUSDT':
            continue
        if base.startswith('_'):
            continue
        out.append(base)
    return sorted(out)


FLAG_COMBOS = [
    ("baseline",          False, False, False),
    ("evaluator_only",    True,  False, False),
    ("qty_pipeline_only", False, True,  False),
    ("exit_gates_only",   False, False, True),
    ("eval+qty",          True,  True,  False),
    ("eval+exit",         True,  False, True),
    ("qty+exit",          False, True,  True),
    ("all_three",         True,  True,  True),
]


def run_one(mode: str, syms: list, npz_dir: str, label: str,
            use_eval: bool, apply_qty: bool, use_exit: bool,
            start_date: str, max_bars: int) -> dict:
    cfg = QuickConfig()
    if mode == 'tradier':
        cfg.apply_tradier_defaults()
    cfg.MAX_BARS = max_bars
    cfg.USE_LIVE_EVALUATOR_VEC = use_eval
    cfg.APPLY_QTY_PIPELINE_TO_PNL = apply_qty
    cfg.USE_PROCESS_POSITION_EXIT_GATES = use_exit
    cfg.MODE = mode
    t0 = time.time()
    elapsed_load = 0.0
    t1 = time.time()
    # Stream NPZs (do NOT materialize into a list — 60 syms × 200k bars OOM's a 30GB box).
    res = simulate(iter_npz(mode, syms, start_date, npz_dir=npz_dir), cfg, capital=10000.0)
    elapsed_sim = time.time() - t1
    n_syms = len(syms)
    years = float(res.get('years', 0) or 0)
    if years <= 0:
        years = (max_bars * (3 if mode == 'crypto' else 5) / 60.0 / 24.0) / 365.25
    trades = int(res.get('trades', 0) or 0)
    pool_sharpe = float(res.get('pool_sharpe', 0) or 0)
    sym_sharpe = float(res.get('sym_sharpe', 0) or 0)
    acc_gain = float(res.get('acc_gain_pct', 0) or 0)
    max_dd = float(res.get('max_dd_pct', 0) or 0)
    avg_gain_trade = (acc_gain / trades) if trades > 0 else 0.0
    gain_per_yr = (acc_gain / years) if years > 0 else 0.0
    gain_sym_yr = (gain_per_yr / max(n_syms, 1)) if n_syms > 0 else 0.0
    return {
        "label": label,
        "pool_sharpe": round(pool_sharpe, 4),
        "sym_sharpe": round(sym_sharpe, 4),
        "avg_gain_trade": round(avg_gain_trade, 4),
        "gain_per_yr": round(gain_per_yr, 4),
        "gain_sym_yr": round(gain_sym_yr, 6),
        "trades": trades,
        "max_dd_pct": round(max_dd, 4),
        "n_syms": n_syms,
        "years": round(years, 4),
        "use_live_evaluator_vec": use_eval,
        "apply_qty_pipeline_to_pnl": apply_qty,
        "use_process_position_exit_gates": use_exit,
        "elapsed_load_s": round(elapsed_load, 1),
        "elapsed_sim_s": round(elapsed_sim, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['crypto', 'tradier'], required=True)
    ap.add_argument('--start', default='2024-01-01')
    ap.add_argument('--max-bars', type=int, default=120000)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    npz_dir = _resolve_npz_dir("")
    if args.mode == 'crypto':
        syms = _crypto_symbols(npz_dir)
        print(f"[PHASE4_SWEEP] crypto: {len(syms)} symbols available — using all", flush=True)
    else:
        syms = _tradier_symbols(npz_dir)
        print(f"[PHASE4_SWEEP] tradier: {len(syms)} symbols available — using all", flush=True)

    if len(syms) < 30:
        print(f"WARNING: only {len(syms)} syms — below sample floor, results will be DIAGNOSTIC", flush=True)

    out_path = args.out or f"data/sweep_results/phase4_validation_{args.mode}_{int(time.time())}.csv"
    print(f"[PHASE4_SWEEP] output → {out_path}", flush=True)

    results = []
    for combo in FLAG_COMBOS:
        label, ue, aq, ux = combo
        print(f"\n[PHASE4_SWEEP] running {label} (eval={ue} qty={aq} exit={ux}) ...", flush=True)
        try:
            row = run_one(args.mode, syms, npz_dir, label, ue, aq, ux, args.start, args.max_bars)
            print(f"  → pool_sharpe={row['pool_sharpe']:.4f} sym={row['sym_sharpe']:.4f} trades={row['trades']} dd={row['max_dd_pct']:.2f}% gain_sym_yr={row['gain_sym_yr']:.4f} elapsed={row['elapsed_sim_s']:.0f}s", flush=True)
            metrics_guard.write_sharpe_row(Path(out_path), row, mode=args.mode, append=True)
            results.append(row)
        except metrics_guard.FakeMetricRefused as e:
            print(f"  REFUSED: {e}", flush=True)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"  ERROR: {e}", flush=True)

    # Summary
    print("\n" + "=" * 90)
    print(f"{'label':<22} {'pool_sharpe':>11} {'sym_sharpe':>10} {'trades':>8} {'dd_pct':>7} {'g_sym_yr':>9}")
    print("=" * 90)
    for r in sorted(results, key=lambda x: -x['pool_sharpe']):
        print(f"{r['label']:<22} {r['pool_sharpe']:>11.4f} {r['sym_sharpe']:>10.4f} {r['trades']:>8} {r['max_dd_pct']:>7.2f} {r['gain_sym_yr']:>9.4f}")
    if results:
        best = max(results, key=lambda x: x['pool_sharpe'])
        print(f"\nBEST: {best['label']} pool_sharpe={best['pool_sharpe']:.4f}")


if __name__ == '__main__':
    main()
