#!/usr/bin/env python3
"""Per-symbol breakdown of v8_quick_engine baseline (the 2.25-Sharpe crypto config).

Runs simulate() on each symbol independently and aggregates per-symbol metrics
per the new CLAUDE.md rule: AVG across symbols (not pool), with range and DD.

Usage:
  python vec_v8q_per_symbol.py --mode crypto --start 2022-01-01 --symbols 48 --min-trades 10
"""
import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER


# Top-48 USDT crypto pairs (matches what the 2.25 baseline was measured on)
CRYPTO_48 = [
    "BTCUSDC","ETHUSDC","SOLUSDC","BNBUSDC","XRPUSDC","ADAUSDC","AVAXUSDC","DOTUSDT",
    "LINKUSDC","LTCUSDC","UNIUSDC","ATOMUSDT","BCHUSDT","ETCUSDT","FILUSDT","TRXUSDT",
    "NEARUSDT","AAVEUSDT","ALGOUSDT","APTUSDT","ARBUSDT","AXSUSDT","BANDUSDT","BATUSDT",
    "CHZUSDT","COMPUSDT","CRVUSDT","DOGEUSDT","EGLDUSDT","ENJUSDT","EOSUSDT","FLOWUSDT",
    "GALAUSDT","GMTUSDT","GRTUSDT","ICPUSDT","INJUSDT","KAVAUSDT","KSMUSDT","MANAUSDT",
    "MKRUSDT","NEOUSDT","ONTUSDT","OPUSDT","QTUMUSDT","ROSEUSDT","RUNEUSDT","SANDUSDT",
]


def run_one(mode, symbol, start_date, capital, npz_dir, cfg):
    """Simulate one symbol in isolation. Returns dict with sharpe/wr/mean/dd/n_trades."""
    stores = load_npz(mode, [symbol], start_date, npz_dir)
    if not stores:
        return None
    r = simulate(stores, cfg, capital)
    # simulate() returns pool-style result; with 1 symbol, it IS the per-symbol result
    # Add drawdown from per-trade PnL stream
    # The engine doesn't expose trade-return array directly — use pnl + trades as proxy
    return {
        "symbol": symbol,
        "sharpe": r.get("sharpe", 0),
        "wr": r.get("wr", 0),
        "mean": r.get("avg_pnl_pct", 0),
        "trades": r.get("trades", 0),
        "pnl_total": r.get("pnl", 0),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--npz-dir", default="")
    ap.add_argument("--min-trades", type=int, default=10)
    ap.add_argument("--symbols", type=str, default="48",
                    help="'48' for CRYPTO_48 list, 'fast' for the 11/12 fast list, or comma-separated symbol list")
    from vector_mandatory_coverage import add_coverage_claim_arguments, enforce_coverage_claim
    add_coverage_claim_arguments(ap)
    args = ap.parse_args()
    coverage_contract = enforce_coverage_claim(args, runner="vec_v8q_per_symbol.py")
    print(f"V8_VECTOR_GROUND_RULE: {coverage_contract['coverage_status']} shortlist_sha256={coverage_contract['shortlist_sha256']}", flush=True)

    if args.symbols == "48":
        symbols = CRYPTO_48 if args.mode == "crypto" else FAST_SYMBOLS_TRADIER.split(",")
    elif args.symbols == "fast":
        symbols = (FAST_SYMBOLS_CRYPTO if args.mode == "crypto" else FAST_SYMBOLS_TRADIER).split(",")
    else:
        symbols = [s.strip() for s in args.symbols.split(",")]

    npz_dir = args.npz_dir
    if not npz_dir:
        for base in ("/home/niels/binance-sandbox", str(Path(__file__).resolve().parent)):
            d = Path(base) / ("backtest_v8") / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                npz_dir = str(d); break

    cfg = QuickConfig.from_override_file(os.environ.get("V8_OVERRIDE_FILE", ""))
    if args.mode == "tradier":
        cfg.apply_tradier_defaults()

    print(f"v8_quick_engine per-symbol baseline | mode={args.mode} npz={npz_dir} start={args.start} syms={len(symbols)}", flush=True)
    print(f"Config (key): CT_WT_VELOCITY_1H_MIN={getattr(cfg,'CT_WT_VELOCITY_1H_MIN','?')} REENTRY_RALLY_K15M_MAX={getattr(cfg,'REENTRY_RALLY_K15M_MAX','?')} SYMGATE={getattr(cfg,'REENTRY_SYMGATE_ENABLED','?')} SRS_TF={getattr(cfg,'STRUCTURAL_RANGE_SHIFT_TF','?')}", flush=True)
    print()

    t0 = time.time()
    results = []
    for i, s in enumerate(symbols):
        r = run_one(args.mode, s, args.start, args.capital, npz_dir, cfg)
        if r is None:
            print(f"[{i+1:>2}/{len(symbols)}] {s:>12}  — no data", flush=True); continue
        results.append(r)
        print(f"[{i+1:>2}/{len(symbols)}] {s:>12}  Sharpe={r['sharpe']:>6.3f} WR={r['wr']:>5.1f}% Mean={r['mean']:>6.3f}% N={r['trades']:>4d} PnL=${r['pnl_total']:>8.2f}", flush=True)

    # Filter to symbols with enough trades
    valid = [r for r in results if r["trades"] >= args.min_trades]
    if not valid:
        print(f"\nNo symbols with ≥{args.min_trades} trades — cannot compute baseline", flush=True)
        return

    sharpes = np.array([r["sharpe"] for r in valid])
    wrs = np.array([r["wr"] for r in valid])
    means = np.array([r["mean"] for r in valid])
    trades = np.array([r["trades"] for r in valid])
    pnls = np.array([r["pnl_total"] for r in valid])

    def pct(a, p): return float(np.percentile(a, p))

    print(f"\n{'='*88}", flush=True)
    print(f"PER-SYMBOL BASELINE — {len(valid)}/{len(symbols)} symbols with ≥{args.min_trades} trades", flush=True)
    print(f"(per CLAUDE.md rule: AVG across symbols, not pool; range shows spread)", flush=True)
    print(f"{'='*88}", flush=True)
    print(f"Metric            avg      min     p25     med     p75     max      std", flush=True)
    print(f"Sharpe         {sharpes.mean():>6.3f}   {sharpes.min():>6.3f}  {pct(sharpes,25):>6.3f}  {pct(sharpes,50):>6.3f}  {pct(sharpes,75):>6.3f}  {sharpes.max():>6.3f}   {sharpes.std():>6.3f}", flush=True)
    print(f"WR%            {wrs.mean():>6.1f}   {wrs.min():>6.1f}  {pct(wrs,25):>6.1f}  {pct(wrs,50):>6.1f}  {pct(wrs,75):>6.1f}  {wrs.max():>6.1f}   {wrs.std():>6.2f}", flush=True)
    print(f"Mean%/trade    {means.mean():>6.3f}   {means.min():>6.3f}  {pct(means,25):>6.3f}  {pct(means,50):>6.3f}  {pct(means,75):>6.3f}  {means.max():>6.3f}   {means.std():>6.3f}", flush=True)
    print(f"Trades/sym     {trades.mean():>6.0f}   {trades.min():>6d}  {int(pct(trades,25)):>6d}  {int(pct(trades,50)):>6d}  {int(pct(trades,75)):>6d}  {trades.max():>6d}", flush=True)
    print(f"$PnL/sym       {pnls.mean():>6.0f}   {pnls.min():>6.0f}  {pct(pnls,25):>6.0f}  {pct(pnls,50):>6.0f}  {pct(pnls,75):>6.0f}  {pnls.max():>6.0f}", flush=True)
    print(f"\nTotal: {int(trades.sum())} trades, ${pnls.sum():,.2f} pool PnL, {time.time()-t0:.0f}s runtime", flush=True)
    # Drawdown: approximate max per-symbol equity dd using cumulative PnL per symbol (requires per-trade sequence which simulate() doesn't expose).
    # For now: report equity-curve-style proxy via worst per-symbol PnL.
    print(f"\nWorst single-symbol PnL (proxy DD indicator): ${pnls.min():.2f} on {valid[pnls.argmin()]['symbol']}", flush=True)
    print(f"Best single-symbol PnL: ${pnls.max():.2f} on {valid[pnls.argmax()]['symbol']}", flush=True)
    # Concentration check
    top3_pnl = np.sort(pnls)[-3:].sum()
    total_pnl = pnls.sum()
    if total_pnl > 0:
        print(f"Top-3 PnL concentration: ${top3_pnl:,.2f} / ${total_pnl:,.2f} = {top3_pnl/total_pnl*100:.1f}% (lower = more broad-based)", flush=True)
    # Sharpe sanity vs user-reported baseline
    print(f"\nUser-reported baseline: Sharpe 2.25, WR 92.8%, Mean 1.35%, 500 trades total on 48 pairs × 4yr", flush=True)
    print(f"This run (per-symbol avg): Sharpe {sharpes.mean():.3f} WR {wrs.mean():.1f}% Mean {means.mean():.3f}% | {int(trades.sum())} total trades", flush=True)


if __name__ == "__main__":
    main()
