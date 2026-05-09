#!/usr/bin/env python3
"""Convert trender_breakout_replay_audit CSV → canonical 9-field rows via metrics_guard.

For each (variant, injector, side, exit_horizon), compute pool_sharpe (mean/std of
per-pick fwd return) + canonical metrics, write a canonical-compliant CSV row.

Comparison vs canonical_winners.csv crypto baseline (iter=2 pool_sharpe=0.4481,
iter=7 pool_sharpe=0.1854 with 405k trades).

Usage:
    python trender_breakout_metrics_summary.py \\
        --input data/inject_audit/replay_58sym_1yr_*.csv \\
        --output data/sweep_results/inject_audit_canonical_$(date -u +%Y%m%dT%H%M).csv \\
        --n-syms 58 --years 1.33 --mode crypto
"""
import argparse, csv, math, sys, time, statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics_guard import write_sharpe_row, pool_sharpe, MIN_TRADES_PER_SYM_FOR_SYM_SHARPE, PER_SYM_SHARPE_CAP


def synthetic_max_dd(returns, sort_by_ts=None):
    """Max drawdown of a cumulative pnl curve from sequential trade returns.
    Assumes returns are in % per trade (not fractional). Returns max DD as positive %.
    """
    if not returns: return 0.0
    cum = 0.0; peak = 0.0; max_dd = 0.0
    for r in returns:
        cum += r
        peak = max(peak, cum)
        dd = peak - cum
        if dd > max_dd: max_dd = dd
    return max_dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--n-syms", type=int, required=True)
    ap.add_argument("--years", type=float, required=True)
    ap.add_argument("--mode", default="crypto")
    args = ap.parse_args()

    print(f"[METRICS] reading {args.input}")
    rows = []
    with open(args.input) as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"[METRICS] {len(rows):,} picks loaded")

    # Group by (variant, injector, side) — compute metrics for each fwd horizon
    # key = (variant, injector, side, horizon_label)
    by_key = defaultdict(list)
    sym_returns = defaultdict(lambda: defaultdict(list))  # key -> sym -> [returns]

    horizons = [("1h", "fwd_1h"), ("4h", "fwd_4h"), ("24h", "fwd_24h")]

    for r in rows:
        key_base = (r["variant"], r["injector"], r["side"])
        sym = r["sym"]
        try: ts = r["ts"]
        except KeyError: ts = ""
        for hlbl, hcol in horizons:
            v = r.get(hcol, "")
            if v == "" or v is None: continue
            try: fv = float(v)
            except: continue
            key = (*key_base, hlbl)
            by_key[key].append((ts, sym, fv))
            sym_returns[key][sym].append(fv)

    print(f"[METRICS] grouped into {len(by_key)} buckets")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists(): out_path.unlink()

    timestamp_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Baseline reference for the "uplift" column
    BASELINE_POOL_SHARPE = 0.1854  # iter=7 from canonical_winners.csv (best by trade-weighted)
    BASELINE_TRADES = 405847
    BASELINE_DD = 10.39
    BASELINE_GAIN_SYM_YR = 1386.435

    summary = []
    for key, picks in sorted(by_key.items(), key=lambda x: (x[0][1], x[0][2], x[0][0], x[0][3])):
        variant, injector, side, hlbl = key
        # Sort by ts for proper sequential DD computation
        picks_sorted = sorted(picks, key=lambda p: p[0])
        returns = [p[2] for p in picks_sorted]
        n_trades = len(returns)
        if n_trades < 30: continue
        ps = pool_sharpe(returns)
        # Cap and clamp per CLAUDE.md rule 6
        if abs(ps) > PER_SYM_SHARPE_CAP and n_trades < 5000:
            print(f"[METRICS] skip {key}: pool_sharpe={ps:.4f} on {n_trades} trades (cap violation)")
            continue
        # Per-sym sharpe (qualifying syms with ≥30 trades)
        sym_ps_list = []
        for sym, sret in sym_returns[key].items():
            if len(sret) >= MIN_TRADES_PER_SYM_FOR_SYM_SHARPE:
                ssp = pool_sharpe(sret)
                if abs(ssp) > PER_SYM_SHARPE_CAP: ssp = math.copysign(PER_SYM_SHARPE_CAP, ssp)
                sym_ps_list.append(ssp)
        sym_sh = sum(sym_ps_list)/len(sym_ps_list) if sym_ps_list else 0.0
        n_syms_qual = len(sym_ps_list)
        avg_gain_trade = sum(returns)/n_trades
        acc_gain_pct = sum(returns)
        gain_per_yr = acc_gain_pct / args.years
        gain_sym_yr = gain_per_yr / max(args.n_syms, 1)
        wr = sum(1 for r in returns if r > 0) / n_trades * 100
        max_dd = synthetic_max_dd(returns)
        # effective score per revised tier framework
        eff_score = ps * math.sqrt(max(n_trades/1000, 0.001))
        beats_baseline = ps > BASELINE_POOL_SHARPE and n_trades >= 1000

        row = {
            "pool_sharpe": round(ps, 4),
            "sym_sharpe": round(sym_sh, 4),
            "avg_gain_trade": round(avg_gain_trade, 4),
            "gain_per_yr": round(gain_per_yr, 2),
            "gain_sym_yr": round(gain_sym_yr, 4),
            "trades": n_trades,
            "max_dd_pct": round(max_dd, 2),
            "n_syms": args.n_syms,
            "years": round(args.years, 2),
            "ts_utc": timestamp_iso,
            "mode": args.mode,
            "iter": variant,
            "injector": injector,
            "side": side,
            "exit_horizon": hlbl,
            "wr": round(wr, 1),
            "n_syms_qualifying": n_syms_qual,
            "effective_score": round(eff_score, 4),
            "beats_baseline_iter7": beats_baseline,
            "engine": "trender_breakout_replay",
        }
        try:
            write_sharpe_row(out_path, row, mode=args.mode)
            summary.append(row)
        except Exception as e:
            print(f"[METRICS] REFUSED {key}: {e}")

    # Print top 25 by effective score
    summary.sort(key=lambda r: r["effective_score"], reverse=True)
    print(f"\n=== TOP 25 (variant, injector, side, exit) by effective_score (pool_sharpe × √(trades/1000)) ===")
    print(f"{'iter':18s} {'inj':10s} {'side':6s} {'h':>3s} {'pool':>7s} {'sym':>7s} {'eff':>6s} {'tr':>7s} {'wr%':>5s} {'gain/yr':>10s} {'dd':>6s} {'beat':>5s}")
    print("="*120)
    for r in summary[:25]:
        flag = "✓" if r["beats_baseline_iter7"] else " "
        print(f"{r['iter']:18s} {r['injector']:10s} {r['side']:6s} {r['exit_horizon']:>3s} {r['pool_sharpe']:>7.4f} {r['sym_sharpe']:>7.4f} {r['effective_score']:>6.2f} {r['trades']:>7d} {r['wr']:>4.1f}% {r['gain_per_yr']:>9.1f}% {r['max_dd_pct']:>5.1f}% {flag}")
    print(f"\n[BASELINE] crypto canonical iter=7: pool_sharpe={BASELINE_POOL_SHARPE} trades={BASELINE_TRADES:,} dd={BASELINE_DD}% gain/yr={1386.435:.1f}% effective={BASELINE_POOL_SHARPE*math.sqrt(BASELINE_TRADES/1000):.2f}")
    print(f"[BASELINE] crypto canonical iter=2: pool_sharpe=0.4481 trades=4,354 dd=66.66% gain/yr=1086.0% effective={0.4481*math.sqrt(4354/1000):.2f}")
    print(f"\n[OUTPUT] canonical CSV: {out_path}")


if __name__ == "__main__":
    main()
