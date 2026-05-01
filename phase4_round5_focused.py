"""Round 5 — focused universes + longer windows.
Hypothesis: concentration on liquid majors / tech beats wide universe.
Also extend start_date to push tradier above the 1yr sample floor.
"""
import os, sys, time, argparse, json
from pathlib import Path
import numpy as np
os.environ.setdefault("V8_RATE_GUARD_DISABLED", "1")
sys.path.insert(0, str(Path(__file__).parent))

from v8_quick_engine import QuickConfig, simulate, iter_npz, _resolve_npz_dir
from phase4_round3_baseline_plus import (
    load_baseline_overrides, apply_overrides,
    CRYPTO_BASELINE_PATH, TRADIER_BASELINE_PATH,
)
import metrics_guard


CRYPTO_MAJORS_USDC = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "ADAUSDC", "BNBUSDC",
                       "AVAXUSDC", "XRPUSDC", "LINKUSDC", "LTCUSDC", "UNIUSDC", "DOGEUSDC"]
CRYPTO_BTC_ETH = ["BTCUSDC", "ETHUSDC"]

TECH_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD",
                 "ADBE", "CRM", "ORCL", "INTC", "AVGO", "NFLX", "QCOM", "TXN",
                 "IBM", "MU", "ASML", "ARM", "PLTR", "SHOP", "PYPL", "COIN",
                 "SNOW", "CRWD", "WDAY", "ROKU", "SPOT", "ABNB", "UBER", "LYFT"]


def run_one(mode, syms, npz_dir, label, base_overrides, phase4_flags, max_bars, start_date):
    cfg = QuickConfig()
    if mode == 'tradier':
        cfg.apply_tradier_defaults()
    cfg.MAX_BARS = max_bars
    cfg.MODE = mode
    apply_overrides(cfg, base_overrides)
    cfg.USE_LIVE_EVALUATOR_VEC = phase4_flags[0]
    cfg.APPLY_QTY_PIPELINE_TO_PNL = phase4_flags[1]
    cfg.USE_PROCESS_POSITION_EXIT_GATES = phase4_flags[2]
    if phase4_flags[2]:
        cfg.WT_4H_VEL_EXIT_K_EXTREME_HIGH = 70.0
        cfg.DC_HOPELESS_EXIT_MIN_AGE_S = 300.0
        cfg.WT_PERCENTILE_EXIT_OB_D = 80.0
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
    out_path = f"data/sweep_results/phase4_round5_focused_{args.mode}_{int(time.time())}.csv"
    print(f"[R5_{args.mode.upper()}] output → {out_path}", flush=True)

    if args.mode == 'crypto':
        base_overrides = load_baseline_overrides(CRYPTO_BASELINE_PATH)
        # Crypto recipe: focused universe + Phase 4 OFF (proven detrimental)
        # Try: full / majors / BTC+ETH × 2 date windows
        # Larger max_bars for longer history
        recipes = [
            # (label, syms, max_bars, start, phase4)
            ("full60_2024",       None, 200000, "2024-01-01", (False, False, False)),
            ("majors11_2024",     CRYPTO_MAJORS_USDC, 200000, "2024-01-01", (False, False, False)),
            ("btceth_2024",       CRYPTO_BTC_ETH, 200000, "2024-01-01", (False, False, False)),
            ("majors11_2023",     CRYPTO_MAJORS_USDC, 350000, "2023-01-01", (False, False, False)),
            ("btceth_2023",       CRYPTO_BTC_ETH, 350000, "2023-01-01", (False, False, False)),
            ("btceth_2022",       CRYPTO_BTC_ETH, 500000, "2022-01-01", (False, False, False)),
            # Also test phase 4 ON for comparison on focused
            ("majors11_p4on",     CRYPTO_MAJORS_USDC, 200000, "2024-01-01", (True, False, True)),
            ("btceth_p4on",       CRYPTO_BTC_ETH, 200000, "2024-01-01", (True, False, True)),
        ]
        from phase4_validation_sweep import _crypto_symbols
        for label, syms, max_bars, start, p4 in recipes:
            if syms is None:
                syms = _crypto_symbols(npz_dir)
            try:
                row = run_one('crypto', syms, npz_dir, label, base_overrides, p4, max_bars, start)
                metrics_guard.write_sharpe_row(Path(out_path), row, mode='crypto', append=True)
                print(f"[{label:<22}] pool={row['pool_sharpe']:>7.4f} sym={row['sym_sharpe']:>7.4f} dd={row['max_dd_pct']:>5.2f}% trades={row['trades']:>5} n_syms={row['n_syms']:>3} yrs={row['years']:.2f} elapsed={row['elapsed_s']:.0f}s", flush=True)
            except Exception as e:
                print(f"[{label}] ERROR: {type(e).__name__}: {e}", flush=True)
    else:
        base_overrides = load_baseline_overrides(TRADIER_BASELINE_PATH)
        from phase4_validation_sweep import _tradier_symbols
        all_syms = _tradier_symbols(npz_dir)
        tech_avail = [s for s in TECH_TICKERS if s in all_syms]
        # Top-7 mega-cap tech only
        mega_tech = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA"]
        mega_avail = [s for s in mega_tech if s in all_syms]
        recipes = [
            ("full246_2024",          all_syms, 100000, "2024-01-01", (True, True, True)),
            ("tech32_2024",           tech_avail, 100000, "2024-01-01", (True, True, True)),
            ("megatech7_2024",        mega_avail, 100000, "2024-01-01", (True, True, True)),
            ("tech32_2023",           tech_avail, 175000, "2023-01-01", (True, True, True)),
            ("megatech7_2023",        mega_avail, 175000, "2023-01-01", (True, True, True)),
            ("tech32_p4off_2024",     tech_avail, 100000, "2024-01-01", (False, False, False)),
            ("megatech7_p4off_2024",  mega_avail, 100000, "2024-01-01", (False, False, False)),
            ("tech32_2022",           tech_avail, 250000, "2022-01-01", (True, True, True)),
        ]
        for label, syms, max_bars, start, p4 in recipes:
            try:
                row = run_one('tradier', syms, npz_dir, label, base_overrides, p4, max_bars, start)
                metrics_guard.write_sharpe_row(Path(out_path), row, mode='tradier', append=True)
                print(f"[{label:<22}] pool={row['pool_sharpe']:>7.4f} sym={row['sym_sharpe']:>7.4f} dd={row['max_dd_pct']:>5.2f}% trades={row['trades']:>5} n_syms={row['n_syms']:>3} yrs={row['years']:.2f} elapsed={row['elapsed_s']:.0f}s", flush=True)
            except Exception as e:
                print(f"[{label}] ERROR: {type(e).__name__}: {e}", flush=True)


if __name__ == '__main__':
    main()
