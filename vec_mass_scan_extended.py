#!/usr/bin/env python3
"""Million-combo sweep: vec_mass_scan condition library + TRENDER/BREAKOUT additions.

Iterates pick=N combinations of boolean conditions, ANDs them, scores forward returns
across horizons. Designed to find combos with effective_score = pool_sharpe × √(n/1000) > 2.0.

Per CLAUDE.md NO-LIES: top results need vec_validate / vec_baseline_snapshot validation
on full publishable universe before promotion.

Usage:
    python vec_mass_scan_extended.py --mode crypto --bars 175200 --min-bars 175200 \\
        --pick 4 --min-trades 200 --top-report 200 \\
        --out data/sweep_results/mass_scan_extended_$(date -u +%Y%m%dT%H%M).csv
"""
import argparse, csv, itertools, math, sys, time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import (build_crypto_conditions, build_tradier_conditions,
                            fwd_returns, score, HORIZONS)
from vec_trender_breakout import (build_trender_breakout_conditions, load_filtered,
                                  CRYPTO_50_SYMS)


CRYPTO_BIG_UNIVERSE = CRYPTO_50_SYMS

# Tradier broad universe (use whatever NPZ has)
TRADIER_BIG_UNIVERSE = ("AAPL,MSFT,NVDA,AMZN,META,GOOGL,GOOG,AVGO,TSLA,LLY,JPM,V,XOM,UNH,MA,HD,PG,COST,ABBV,JNJ,"
                        "WMT,BAC,ORCL,CVX,KO,MRK,CRM,ADBE,AMD,PEP,NFLX,TMO,LIN,QCOM,WFC,DIS,DHR,CSCO,ABT,ACN,"
                        "VZ,TXN,INTC,AMGN,IBM,UNP,COP,SPGI,LOW,RTX,GS,HON,NEE,CAT,BLK,GE,BKNG,AXP,DE,UPS,"
                        "INTU,T,SCHW,ANET,PYPL,SBUX,GILD,SYK,ADP,MS,AMT,NOW,VRTX,TJX,REGN,PFE,LRCX,KLAC,MMM,ETN,"
                        "C,LMT,MDT,DUK,GS,SO,PNC,CB,CI,USB,TFC,PLD,FI,FCX,EOG,SLB,APD,ZTS,FDX,NSC,ITW").split(",")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    ap.add_argument("--bars", type=int, default=175200)
    ap.add_argument("--min-bars", type=int, default=175200)
    ap.add_argument("--pick", type=int, default=4)
    ap.add_argument("--min-trades", type=int, default=200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top-report", type=int, default=200)
    ap.add_argument("--npz-dir", default="")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--max-tests", type=int, default=0, help="Cap on total combo×horizon tests (0=unlimited)")
    ap.add_argument("--no-trender-breakout", action="store_true")
    args = ap.parse_args()

    # Pick universe
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = CRYPTO_BIG_UNIVERSE if args.mode == "crypto" else TRADIER_BIG_UNIVERSE
    npz_dir = args.npz_dir
    if not npz_dir:
        for base in ("/home/niels/binance-sandbox", str(Path(__file__).resolve().parent)):
            for sub in (("backtest_v8", "indicators"), ("backtest_v4_tradier", "indicators")):
                d = Path(base) / sub[0] / sub[1]
                if d.exists() and any(d.glob("*.npz")):
                    npz_dir = str(d); break
            if npz_dir: break
    print(f"[MASS] mode={args.mode} pick={args.pick} bars={args.bars} npz={npz_dir} syms={len(syms)}", flush=True)
    t0 = time.time()
    loaded, n_bars, skipped = load_filtered(Path(npz_dir), syms, args.bars, args.min_bars)
    print(f"[MASS] {time.time()-t0:.1f}s loaded {len(loaded)} syms × {n_bars} bars (skipped {len(skipped)})", flush=True)
    if len(loaded) < 8:
        print("[MASS] ABORT: too few syms"); return

    builder = build_crypto_conditions if args.mode == "crypto" else build_tradier_conditions
    C, close = builder(loaded)
    print(f"[MASS] {time.time()-t0:.1f}s built {len(C)} base conditions", flush=True)
    if not args.no_trender_breakout:
        C_new, *_ = build_trender_breakout_conditions(loaded, base_tf_minutes=3 if args.mode == "crypto" else 5)
        # Filter trender_breakout conditions to ONLY useful ones: drop redundant variants
        keep = [k for k in C_new if any(tag in k for tag in ("trender_l65", "trender_l75", "breakout_m2r50", "breakout_m3r50", "breakout_m2.5r50"))]
        for k in keep:
            C[k] = C_new[k]
        print(f"[MASS] {time.time()-t0:.1f}s + {len(keep)} TRENDER/BREAKOUT conditions = {len(C)} total", flush=True)

    long_keys = sorted([k for k in C if k.startswith("L_")])
    short_keys = sorted([k for k in C if k.startswith("S_")])
    fwd = fwd_returns(close, HORIZONS)
    n_lc = math.comb(len(long_keys), args.pick) if len(long_keys) >= args.pick else 0
    n_sc = math.comb(len(short_keys), args.pick) if len(short_keys) >= args.pick else 0
    n_total = (n_lc + n_sc) * len(fwd)
    print(f"[MASS] {time.time()-t0:.1f}s LONG combos={n_lc:,} SHORT combos={n_sc:,} × {len(fwd)} horizons = {n_total:,} tests", flush=True)

    n_syms = len(loaded)
    base_tf_min = 3 if args.mode == "crypto" else 5
    years = n_bars * base_tf_min / 60 / 24 / 365.25

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    f_out = open(out_path, "w", newline="")
    writer = csv.writer(f_out)
    writer.writerow(["side", "combo", "horizon", "n", "sharpe", "wr", "mean_pct", "pf", "eff_score", "n_syms", "years"])

    results = []
    done = 0
    for side, keys in [("L", long_keys), ("S", short_keys)]:
        for combo in itertools.combinations(keys, args.pick):
            mask = np.ones_like(C[combo[0]])
            for k in combo:
                mask &= C[k]
            for h, ret in fwd.items():
                m = score(mask, ret, args.min_trades)
                done += 1
                if m is None: continue
                # Sign-correct for SHORT
                eff_sh = -m["sharpe"] if side == "S" else m["sharpe"]
                eff_wr = (100.0 - m["wr"]) if side == "S" else m["wr"]
                eff_mean_pct = (-m["mean"] * 100) if side == "S" else (m["mean"] * 100)
                eff_score = eff_sh * math.sqrt(max(m["n"]/1000, 0.001))
                # Filter: only emit positive-effective-score rows to keep CSV reasonable
                if eff_score < 0.5: continue
                results.append({
                    "side": side, "combo": "+".join(k[2:] for k in combo), "horizon": h,
                    "n": m["n"], "sharpe": eff_sh, "wr": eff_wr, "mean_pct": eff_mean_pct,
                    "pf": m["pf"], "eff_score": eff_score, "n_syms": n_syms, "years": years
                })
                writer.writerow([side, "+".join(k[2:] for k in combo), h, m["n"], f"{eff_sh:.4f}", f"{eff_wr:.1f}", f"{eff_mean_pct:.4f}", f"{m['pf']:.3f}", f"{eff_score:.4f}", n_syms, f"{years:.2f}"])
                if args.max_tests and done >= args.max_tests: break
            if args.max_tests and done >= args.max_tests: break
            if done % 200000 == 0:
                el = time.time() - t0
                rate = done / max(el, 0.01)
                eta = (n_total - done) / rate if rate > 0 else 0
                print(f"[MASS] {el:.0f}s {done:,}/{n_total:,} ({100*done/n_total:.1f}%) rate={rate:.0f}/s eta={eta:.0f}s passed={len(results):,}", flush=True)
    f_out.close()

    el = time.time() - t0
    print(f"\n[MASS] DONE in {el:.0f}s. {done:,} tests, {len(results):,} passed eff_score≥0.5 written to {out_path}", flush=True)

    # Top by effective score
    results.sort(key=lambda r: r["eff_score"], reverse=True)
    print(f"\n=== TOP {min(args.top_report, len(results))} by effective score (eff_sh × √(n/1000)) ===", flush=True)
    print(f"{'side':>4s} {'combo':<78} {'h':>4s} {'n':>7s} {'eff_sh':>7s} {'eff_wr':>7s} {'eff_mean':>9s} {'eff_score':>9s}", flush=True)
    print("─"*135, flush=True)
    for r in results[:args.top_report]:
        flag = " ⭐" if r["eff_score"] > 2.0 else (" ✓" if r["eff_score"] > 1.0 else "")
        print(f"{r['side']:>4s} {r['combo']:<78s} {r['horizon']:>4d} {r['n']:>7d} {r['sharpe']:>7.4f} {r['wr']:>6.1f}% {r['mean_pct']:>8.4f}% {r['eff_score']:>8.3f}{flag}", flush=True)


if __name__ == "__main__":
    main()
