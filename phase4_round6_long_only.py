"""Round 6 — long-only + narrow high-conviction signals.
Hypothesis: stocks have positive drift, shorts hurt. Crypto bull-bias too.
Also restrict to only highest-conviction B-blocks (B15 STRONG_TREND + B04 DC_RETEST).
"""
import os, sys, time, argparse, json
from pathlib import Path
import numpy as np
os.environ.setdefault("V8_RATE_GUARD_DISABLED", "1")
sys.path.insert(0, str(Path(__file__).parent))

from v8_quick_engine import QuickConfig, simulate, iter_npz, _resolve_npz_dir
from phase4_round3_baseline_plus import load_baseline_overrides, apply_overrides, CRYPTO_BASELINE_PATH, TRADIER_BASELINE_PATH
from phase4_round5_focused import CRYPTO_MAJORS_USDC, TECH_TICKERS
import metrics_guard


MEGATECH = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA"]


def run_one(mode, syms, npz_dir, label, base_overrides, extras, max_bars, start_date):
    cfg = QuickConfig()
    if mode == 'tradier':
        cfg.apply_tradier_defaults()
    cfg.MAX_BARS = max_bars
    cfg.MODE = mode
    apply_overrides(cfg, base_overrides)
    apply_overrides(cfg, extras)
    t1 = time.time()
    res = simulate(iter_npz(mode, syms, start_date, npz_dir=npz_dir), cfg, capital=10000.0)
    elapsed = time.time() - t1
    n_syms = len(syms)
    years = float(res.get('years', 0) or 0) or (max_bars * (3 if mode == 'crypto' else 5) / 60.0 / 24.0 / 365.25)
    trades = int(res.get('trades', 0) or 0)
    acc_gain = float(res.get('acc_gain_pct', 0) or 0)
    return {
        "label": label,
        "pool_sharpe": round(float(res.get('pool_sharpe', 0)), 4),
        "sym_sharpe": round(float(res.get('sym_sharpe', 0)), 4),
        "avg_gain_trade": round(acc_gain / trades, 4) if trades > 0 else 0.0,
        "gain_per_yr": round(acc_gain / years, 4) if years > 0 else 0.0,
        "gain_sym_yr": round(acc_gain / max(n_syms, 1) / years, 6) if years > 0 else 0.0,
        "trades": trades,
        "max_dd_pct": round(float(res.get('max_dd_pct', 0)), 4),
        "n_syms": n_syms,
        "years": round(years, 4),
        "elapsed_s": round(elapsed, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['crypto', 'tradier'], required=True)
    args = ap.parse_args()

    npz_dir = _resolve_npz_dir("")
    out_path = f"data/sweep_results/phase4_round6_longonly_{args.mode}_{int(time.time())}.csv"
    print(f"[R6_{args.mode.upper()}] output → {out_path}", flush=True)

    if args.mode == 'crypto':
        from phase4_validation_sweep import _crypto_symbols
        all_syms = _crypto_symbols(npz_dir)
        base_overrides = load_baseline_overrides(CRYPTO_BASELINE_PATH)
        # LONG_ONLY = drop short leg (CONFLUENCE_MIN_BLOCKS_SHORT very high or sim impossible).
        # The engine simulates both sides; we toggle flags that suppress shorts.
        recipes = [
            # (label, syms, max_bars, start, extras_dict)
            ("ref_full60",                 all_syms, 200000, "2024-01-01", {}),
            ("strict_b15_b04_only",        all_syms, 200000, "2024-01-01", {
                "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
                "REENTRY_B10_STOCH_REV_ENABLED": False,
                "REENTRY_B11_DC_BREAK_ENABLED": False,
                "REENTRY_B12_WT_MOM_ENABLED": False,
                "REENTRY_B14_HA_TREND_ENABLED": False,
            }),
            ("b15_only",                   all_syms, 200000, "2024-01-01", {
                "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
                "REENTRY_B04_DC_RETEST_ENABLED": False,
                "REENTRY_B10_STOCH_REV_ENABLED": False,
                "REENTRY_B11_DC_BREAK_ENABLED": False,
                "REENTRY_B12_WT_MOM_ENABLED": False,
                "REENTRY_B14_HA_TREND_ENABLED": False,
            }),
            ("b04_only",                   all_syms, 200000, "2024-01-01", {
                "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
                "REENTRY_B10_STOCH_REV_ENABLED": False,
                "REENTRY_B11_DC_BREAK_ENABLED": False,
                "REENTRY_B12_WT_MOM_ENABLED": False,
                "REENTRY_B14_HA_TREND_ENABLED": False,
                "REENTRY_B15_STRONG_TREND_ENABLED": False,
            }),
            ("majors11_strict",            CRYPTO_MAJORS_USDC, 200000, "2024-01-01", {
                "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
                "REENTRY_B10_STOCH_REV_ENABLED": False,
                "REENTRY_B11_DC_BREAK_ENABLED": False,
                "REENTRY_B12_WT_MOM_ENABLED": False,
                "REENTRY_B14_HA_TREND_ENABLED": False,
            }),
            # Tighter HTF requirements — push HTF discount harder
            ("htf_required",               all_syms, 200000, "2024-01-01", {
                "WT_HTF_DISCOUNT_ENABLED": True,
                # Drop tracks that fire on 0/2 HTF aligned (0.3× already; can't be more drastic)
            }),
        ]
        for label, syms, max_bars, start, extras in recipes:
            try:
                row = run_one('crypto', syms, npz_dir, label, base_overrides, extras, max_bars, start)
                metrics_guard.write_sharpe_row(Path(out_path), row, mode='crypto', append=True)
                print(f"[{label:<25}] pool={row['pool_sharpe']:>7.4f} sym={row['sym_sharpe']:>7.4f} dd={row['max_dd_pct']:>5.2f}% trades={row['trades']:>5} n={row['n_syms']:>3} elapsed={row['elapsed_s']:.0f}s", flush=True)
            except Exception as e:
                print(f"[{label}] ERROR: {type(e).__name__}: {e}", flush=True)
    else:
        from phase4_validation_sweep import _tradier_symbols
        all_syms = _tradier_symbols(npz_dir)
        tech_avail = [s for s in TECH_TICKERS if s in all_syms]
        mega_avail = [s for s in MEGATECH if s in all_syms]
        base_overrides = load_baseline_overrides(TRADIER_BASELINE_PATH)
        # Tradier LONG-ONLY hypothesis: stocks drift up, longs work better
        long_only_extras = {
            # Most engine doesn't have a clean LONG_ONLY flag — close approximation:
            # Drop short reentry blocks (engine simulates both sides; this kills short signals)
        }
        # Also try long-only via STDEV_BREAKOUT_LONG only / SHORT disabled
        recipes = [
            ("ref_megatech7",              mega_avail, 100000, "2024-01-01", {
                "USE_LIVE_EVALUATOR_VEC": True,
                "APPLY_QTY_PIPELINE_TO_PNL": True,
                "USE_PROCESS_POSITION_EXIT_GATES": True,
                "WT_4H_VEL_EXIT_K_EXTREME_HIGH": 70.0,
                "DC_HOPELESS_EXIT_MIN_AGE_S": 300.0,
                "WT_PERCENTILE_EXIT_OB_D": 80.0,
            }),
            ("megatech_strict_b04",        mega_avail, 100000, "2024-01-01", {
                "USE_LIVE_EVALUATOR_VEC": True,
                "APPLY_QTY_PIPELINE_TO_PNL": True,
                "USE_PROCESS_POSITION_EXIT_GATES": True,
                "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
                "REENTRY_B10_STOCH_REV_ENABLED": False,
                "REENTRY_B11_DC_BREAK_ENABLED": False,
                "REENTRY_B12_WT_MOM_ENABLED": False,
                "REENTRY_B14_HA_TREND_ENABLED": False,
                "REENTRY_B15_STRONG_TREND_ENABLED": False,
            }),
            ("megatech_strict_b15",        mega_avail, 100000, "2024-01-01", {
                "USE_LIVE_EVALUATOR_VEC": True,
                "APPLY_QTY_PIPELINE_TO_PNL": True,
                "USE_PROCESS_POSITION_EXIT_GATES": True,
                "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
                "REENTRY_B04_DC_RETEST_ENABLED": False,
                "REENTRY_B10_STOCH_REV_ENABLED": False,
                "REENTRY_B11_DC_BREAK_ENABLED": False,
                "REENTRY_B12_WT_MOM_ENABLED": False,
                "REENTRY_B14_HA_TREND_ENABLED": False,
            }),
            ("tech32_strict_b04_b15",      tech_avail, 100000, "2024-01-01", {
                "USE_LIVE_EVALUATOR_VEC": True,
                "APPLY_QTY_PIPELINE_TO_PNL": True,
                "USE_PROCESS_POSITION_EXIT_GATES": True,
                "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
                "REENTRY_B10_STOCH_REV_ENABLED": False,
                "REENTRY_B11_DC_BREAK_ENABLED": False,
                "REENTRY_B12_WT_MOM_ENABLED": False,
                "REENTRY_B14_HA_TREND_ENABLED": False,
            }),
            # Try LE_DYNAMIC sizing more aggressively
            ("megatech_le_aggressive",     mega_avail, 100000, "2024-01-01", {
                "USE_LIVE_EVALUATOR_VEC": True,
                "APPLY_QTY_PIPELINE_TO_PNL": True,
                "USE_PROCESS_POSITION_EXIT_GATES": True,
                "LE_DYNAMIC_ENABLED": True,
            }),
            # Loose exits — let winners run
            ("megatech_loose_exits",       mega_avail, 100000, "2024-01-01", {
                "USE_LIVE_EVALUATOR_VEC": True,
                "APPLY_QTY_PIPELINE_TO_PNL": True,
                "USE_PROCESS_POSITION_EXIT_GATES": True,
                "WT_4H_VEL_EXIT_K_EXTREME_HIGH": 95.0,  # only fire at extreme overbought
                "DC_HOPELESS_EXIT_MIN_AGE_S": 1800.0,   # wait 30 min
                "WT_PERCENTILE_EXIT_OB_D": 95.0,
                "WT_PERCENTILE_EXIT_OB_4H": 90.0,
            }),
        ]
        for label, syms, max_bars, start, extras in recipes:
            try:
                row = run_one('tradier', syms, npz_dir, label, base_overrides, extras, max_bars, start)
                metrics_guard.write_sharpe_row(Path(out_path), row, mode='tradier', append=True)
                print(f"[{label:<25}] pool={row['pool_sharpe']:>7.4f} sym={row['sym_sharpe']:>7.4f} dd={row['max_dd_pct']:>5.2f}% trades={row['trades']:>5} n={row['n_syms']:>3} elapsed={row['elapsed_s']:.0f}s", flush=True)
            except Exception as e:
                print(f"[{label}] ERROR: {type(e).__name__}: {e}", flush=True)


if __name__ == '__main__':
    main()
