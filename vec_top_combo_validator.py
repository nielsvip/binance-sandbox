#!/usr/bin/env python3
"""Validate top combos from vec_mass_scan_extended on the PUBLISHABLE universe.

Reads CSV, takes top N by effective_score, re-evaluates each combo on full
publishable universe (56 syms × 1.17 yr crypto / 56 stocks × 1+ yr tradier).
Writes canonical 9-field row via metrics_guard.write_sharpe_row.

Usage:
    python vec_top_combo_validator.py --input /tmp/mass_crypto_pick4_v2.csv \\
        --mode crypto --top 100 --bars 220000 --min-bars 175200 \\
        --out data/sweep_results/vec_top_combos_validated_$(date -u +%Y%m%dT%H%M).csv
"""
import argparse, csv, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import build_crypto_conditions, build_tradier_conditions, fwd_returns, score
from vec_trender_breakout import build_trender_breakout_conditions, load_filtered, CRYPTO_50_SYMS
from metrics_guard import write_sharpe_row, MIN_TRADES_PER_SYM_FOR_SYM_SHARPE


TRADIER_PUBLISHABLE = ("AAPL,MSFT,NVDA,AMZN,META,GOOGL,GOOG,AVGO,TSLA,LLY,JPM,V,XOM,UNH,MA,HD,PG,COST,ABBV,JNJ,"
                       "WMT,BAC,ORCL,CVX,KO,MRK,CRM,ADBE,AMD,PEP,NFLX,TMO,LIN,QCOM,WFC,DIS,DHR,CSCO,ABT,ACN,"
                       "VZ,TXN,INTC,AMGN,IBM,UNP,COP,SPGI,LOW,RTX,GS,HON,NEE,CAT,BLK,GE,BKNG,AXP,DE,UPS").split(",")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--bars", type=int, default=220000)
    ap.add_argument("--min-bars", type=int, default=175200)
    ap.add_argument("--min-trades", type=int, default=200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--npz-dir", default="")
    args = ap.parse_args()

    # Load top combos by effective score
    with open(args.input) as f:
        rdr = csv.DictReader(f)
        rows = [r for r in rdr]
    print(f"[VAL] {len(rows)} rows in {args.input}", flush=True)
    rows.sort(key=lambda r: float(r.get("eff_score", 0) or 0), reverse=True)
    top = rows[:args.top]
    print(f"[VAL] top eff_score: {top[0]['eff_score']} ({top[0]['side']} {top[0]['combo']} h={top[0]['horizon']})", flush=True)
    print(f"[VAL] eff_score @ rank {args.top}: {top[-1]['eff_score']}", flush=True)

    # Load PUBLISHABLE universe
    syms = CRYPTO_50_SYMS if args.mode == "crypto" else TRADIER_PUBLISHABLE
    npz_dir = args.npz_dir or "/home/niels/binance-sandbox/backtest_v8/indicators"
    if not Path(npz_dir).exists():
        for alt in ("/Users/niels/Documents/binance/backtest_v8/indicators",):
            if Path(alt).exists(): npz_dir = alt; break
    print(f"[VAL] loading {len(syms)} syms × {args.bars} bars from {npz_dir}", flush=True)
    t0 = time.time()
    loaded, n_bars, _ = load_filtered(Path(npz_dir), syms, args.bars, args.min_bars)
    print(f"[VAL] {time.time()-t0:.1f}s loaded {len(loaded)} syms × {n_bars} bars", flush=True)
    if len(loaded) < 30:
        print(f"[VAL] ABORT: only {len(loaded)} syms (need ≥30 for publishable)"); return

    builder = build_crypto_conditions if args.mode == "crypto" else build_tradier_conditions
    C, close = builder(loaded)
    C_new, *_ = build_trender_breakout_conditions(loaded, base_tf_minutes=3 if args.mode == "crypto" else 5)
    keep = [k for k in C_new if any(t in k for t in ("trender_l65", "trender_l75", "breakout_m2r50", "breakout_m3r50", "breakout_m2.5r50"))]
    for k in keep: C[k] = C_new[k]
    fwd = fwd_returns(close, [4, 8, 16, 32, 64, 128, 256])
    n_syms = len(loaded)
    base_tf = 3 if args.mode == "crypto" else 5
    if args.mode == "tradier":
        years = n_bars * base_tf / 60 / 6.5 / 252
    else:
        years = n_bars * base_tf / 60 / 24 / 365.25
    print(f"[VAL] {time.time()-t0:.1f}s built {len(C)} conditions, {n_syms} syms × {years:.2f} years", flush=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists(): out_path.unlink()
    ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Evaluate each top combo on PUBLISHABLE
    print(f"\n{'rank':>4s} {'side':>4s} {'combo':<70s} {'h':>4s} {'n':>7s} {'eff_sh':>7s} {'eff_wr':>7s} {'eff_score':>9s} {'verdict':<20s}", flush=True)
    print("─" * 130, flush=True)
    pub_results = []
    for rank, r in enumerate(top, 1):
        side = r["side"]
        combo_keys = [(side + "_" + k) for k in r["combo"].split("+")]
        h = int(r["horizon"])
        # Build mask
        missing = [k for k in combo_keys if k not in C]
        if missing: continue
        if h not in fwd: continue
        mask = np.ones_like(C[combo_keys[0]])
        for k in combo_keys: mask &= C[k]
        m = score(mask, fwd[h], args.min_trades)
        if m is None: continue
        # Sign-correct
        sign = -1 if side == "S" else 1
        eff_sh = sign * m["sharpe"]
        eff_wr = (100.0 - m["wr"]) if side == "S" else m["wr"]
        eff_mean_pct = sign * m["mean"] * 100
        eff_score = eff_sh * math.sqrt(max(m["n"]/1000, 0.001))
        # Per-symbol sharpe (average across syms with ≥30 trades)
        sym_sharpe_vals = []
        fwd_h = fwd[h]
        for _si in range(fwd_h.shape[1] if fwd_h.ndim == 2 else mask.shape[1] if mask.ndim == 2 else 0):
            _sm = mask[:, _si] if mask.ndim == 2 else mask
            _sr = fwd_h[:, _si] if fwd_h.ndim == 2 else fwd_h
            _rets = _sr[_sm]
            if len(_rets) < 30: continue
            _std = float(_rets.std())
            if _std <= 0: continue
            sym_sharpe_vals.append(sign * float(_rets.mean()) / _std)
        sym_sharpe = round(float(np.mean(sym_sharpe_vals)), 4) if sym_sharpe_vals else 0.0
        # max_dd_pct: worst-1st-percentile trade loss as proxy
        all_rets = fwd_h[mask]
        if len(all_rets) > 0:
            worst_frac = float(np.percentile(sign * all_rets, 1))
            max_dd_pct = round(abs(min(0.0, worst_frac)) * 100, 2)
        else:
            max_dd_pct = 0.0
        pub_results.append({
            "rank": rank, "side": side, "combo": r["combo"], "horizon": h,
            "n": m["n"], "eff_sh": eff_sh, "eff_wr": eff_wr,
            "eff_mean_pct": eff_mean_pct, "eff_score": eff_score, "pf": m["pf"],
        })
        # Write canonical row via metrics_guard
        verdict = "PUBLISHABLE" if (n_syms >= 48 and years >= 1.0) else "DIAGNOSTIC"
        flag = " ⭐" if eff_sh > 1.5 else (" ✓" if eff_sh > 1.0 else "")
        try:
            write_sharpe_row(out_path, {
                "pool_sharpe": round(eff_sh, 4),
                "sym_sharpe": sym_sharpe,
                "avg_gain_trade": round(eff_mean_pct, 4),
                "gain_per_yr": round(eff_mean_pct * m["n"] / max(years, 0.01) / max(n_syms, 1), 2),
                "gain_sym_yr": round(eff_mean_pct * m["n"] / max(years, 0.01) / max(n_syms, 1), 4),
                "trades": m["n"],
                "max_dd_pct": max_dd_pct,
                "n_syms": n_syms, "years": round(years, 2),
                "ts_utc": ts_iso, "mode": args.mode,
                # 2026-07-07: "iter" is coerced to float by the DB ingester (it's a
                # numeric column elsewhere) — that was silently nulling this row's
                # only identifier (combo+horizon string) for every one of this
                # producer's rows (the single largest source, ~700k rows / 56% of
                # the central DB). Recorded in "label" too, which the ingester
                # keeps as text, so "what was tested" survives ingestion.
                "iter": f"{r['side']}_{r['combo']}_h{h}",
                "label": f"{r['side']}_{r['combo']}_h{h}",
                "combo": r["combo"], "side": side, "horizon": h,
                "wr": round(eff_wr, 1), "pf": round(m["pf"], 3), "engine": "vec_top_combo_validator",
            }, mode=args.mode)
        except Exception as e:
            pass
        print(f"{rank:>4d} {side:>4s} {r['combo']:<70s} {h:>4d} {m['n']:>7d} {eff_sh:>7.4f} {eff_wr:>6.1f}% {eff_score:>8.3f}{flag}", flush=True)

    # Top 10 by publishable eff_score
    pub_results.sort(key=lambda x: x["eff_score"], reverse=True)
    el = time.time() - t0
    print(f"\n[VAL] DONE in {el:.0f}s — {len(pub_results)} validated\n", flush=True)
    print(f"=== TOP 10 ON PUBLISHABLE ({n_syms} syms × {years:.2f} yr) by eff_score ===", flush=True)
    for r in pub_results[:10]:
        print(f"  {r['side']} {r['combo']:<60s} h={r['horizon']:>3d} n={r['n']:>6d} ps={r['eff_sh']:>6.3f} wr={r['eff_wr']:>5.1f}% eff={r['eff_score']:>5.2f}", flush=True)


if __name__ == "__main__":
    main()
