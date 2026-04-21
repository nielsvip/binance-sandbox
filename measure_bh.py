"""Buy-and-hold benchmark for a symbol corpus:
  - per-symbol total return
  - accumulated_gain_pct = sum(per_symbol_return) [same definition strategy uses]
  - B&H per-bar pool Sharpe: pool all bar-level returns across symbols, mean/std
  - B&H max_dd_pct: worst drawdown of cap-weighted equal-weight basket
"""
import argparse, json, sys, time
from pathlib import Path
import numpy as np

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier"])
    ap.add_argument("--symbols", type=int, default=50)
    ap.add_argument("--start", default=None)
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--label", default="BH")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from v8_quick_engine import load_npz

    start_date = args.start or ("2022-01-01" if args.mode == "crypto" else "2024-01-01")
    stores = load_npz(args.mode, None, start_date, args.npz_dir)
    syms = sorted(stores.keys())[:args.symbols]

    per_sym_total_ret = []
    per_sym_bar_rets = []  # list-of-arrays (equal-weight basket)
    per_sym_eqcurves = []
    ltf_key = "close_3m" if args.mode == "crypto" else "close_5m"
    for s in syms:
        npz = stores[s]
        close = npz.get(ltf_key)
        if close is None or len(close) < 10:
            continue
        close = np.asarray(close, dtype=np.float64)
        # Trim leading/trailing zeros
        nz = np.where(close > 0)[0]
        if len(nz) < 10: continue
        close = close[nz[0]:nz[-1]+1]
        total_ret = (close[-1] / close[0] - 1.0) * 100.0
        per_sym_total_ret.append(total_ret)
        # Per-bar log returns (percentage)
        bar_ret = np.diff(close) / close[:-1] * 100.0
        per_sym_bar_rets.append(bar_ret)
        # Equity curve for this symbol (normalized)
        per_sym_eqcurves.append(close / close[0])

    # Equal-weight basket equity curve via aligned-length truncation
    min_len = min(len(e) for e in per_sym_eqcurves)
    basket = np.mean(np.stack([e[:min_len] for e in per_sym_eqcurves]), axis=0)
    peak = np.maximum.accumulate(basket)
    dd_series = (peak - basket) / peak * 100.0
    max_dd = float(dd_series.max())

    # Pool Sharpe on per-bar returns (concatenated across symbols)
    all_bar_rets = np.concatenate(per_sym_bar_rets) if per_sym_bar_rets else np.array([0.0])
    ps = float(all_bar_rets.mean() / all_bar_rets.std()) if all_bar_rets.std() > 0 else 0.0

    out = {
        "label": args.label, "mode": args.mode,
        "symbols_used": len(per_sym_total_ret),
        "start": start_date,
        "bh_per_bar_pool_sharpe": round(ps, 4),
        "bh_accumulated_gain_pct": round(float(np.sum(per_sym_total_ret)), 2),
        "bh_avg_per_symbol_gain_pct": round(float(np.mean(per_sym_total_ret)), 2),
        "bh_max_dd_pct_basket": round(max_dd, 2),
        "bh_bars": int(min_len),
    }
    print("BH_RESULT: " + json.dumps(out), flush=True)

if __name__ == "__main__":
    main()
