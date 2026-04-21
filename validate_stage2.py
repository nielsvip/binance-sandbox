"""Stage-2 validator: take top configs from small-sample Stage-1 sweeps and re-run on full symbol set.

Usage:
  python3 validate_stage2.py \
    --stage1-glob "data/autonomous/crypto_swarm/w*/autonomous_crypto.csv" \
    --mode crypto \
    --min-stage1-sharpe 0.4 \
    --top-n 50 \
    --symbols 50 \
    --start 2022-01-01 \
    --npz-dir backtest_v8/indicators \
    --out-dir data/stage2_validated/crypto \
    --workers 4

For tradier, point at tradier_swarm instead and change --mode tradier.
"""
import argparse, csv, gc, json, os, sys, time
from pathlib import Path
import pandas as pd


def load_stage1_candidates(glob_pattern: str, min_sharpe: float, top_n: int) -> list[dict]:
    """Read all Stage-1 CSV files, return top_n configs by pool_sharpe."""
    import glob as _glob
    files = _glob.glob(glob_pattern)
    if not files:
        print(f"[S2] No files matching: {glob_pattern}")
        return []
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f)
            if len(df) > 0:
                dfs.append(df)
        except Exception as e:
            print(f"[S2] Skip {f}: {e}")
    if not dfs:
        return []
    all_df = pd.concat(dfs, ignore_index=True)
    all_df["pool_sharpe"] = pd.to_numeric(all_df["pool_sharpe"], errors="coerce")
    all_df["overrides_json"] = all_df["overrides_json"].fillna("{}")
    candidates = all_df[all_df.pool_sharpe >= min_sharpe].sort_values("pool_sharpe", ascending=False)
    # Deduplicate by overrides_json to avoid testing same config twice
    seen = set()
    unique = []
    for _, row in candidates.iterrows():
        key = row["overrides_json"]
        if key not in seen:
            seen.add(key)
            unique.append({
                "stage1_sharpe": row["pool_sharpe"],
                "stage1_gain": row.get("acc_gain_pct", 0),
                "stage1_dd": row.get("max_dd_pct", 0),
                "stage1_trades": row.get("trades", 0),
                "overrides": json.loads(key),
            })
        if len(unique) >= top_n:
            break
    print(f"[S2] Loaded {len(unique)} unique candidates (of {len(candidates)} qualifying rows from {len(dfs)} files)")
    return unique


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage1-glob", required=True, help="Glob pattern for Stage-1 CSV files")
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier"])
    ap.add_argument("--min-stage1-sharpe", type=float, default=0.4)
    ap.add_argument("--top-n", type=int, default=100, help="Test at most this many Stage-1 candidates")
    ap.add_argument("--symbols", type=int, default=50)
    ap.add_argument("--start", required=True)
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--min-trades-per-sym", type=int, default=30)
    ap.add_argument("--workers", type=int, default=1, help="Parallel workers (caution: RAM)")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from v8_quick_engine import QuickConfig, load_npz, simulate

    candidates = load_stage1_candidates(args.stage1_glob, args.min_stage1_sharpe, args.top_n)
    if not candidates:
        print("[S2] No candidates found. Exiting.")
        return

    CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    all_files = sorted(Path(args.npz_dir).glob("*.npz"))
    syms = []
    for p in all_files:
        sym = p.stem
        is_crypto = any(sym.endswith(s) for s in CRYPTO_SUFFIXES)
        if args.mode == "crypto" and not is_crypto:
            continue
        if args.mode == "tradier" and is_crypto:
            continue
        syms.append(sym)
    syms = syms[: args.symbols]

    print(f"[S2] Loading {len(syms)} symbols from {args.start} ...")
    subset = load_npz(args.mode, syms, args.start, args.npz_dir)
    gc.collect()
    print(f"[S2] Data loaded. Testing {len(candidates)} candidates ...")

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    out_csv = Path(args.out_dir) / f"stage2_{args.mode}.csv"
    csv_exists = out_csv.exists()

    FORBIDDEN = {
        "AUGMENT_PT_ENABLED", "AUGMENT_PT_PCT",
        "CYCLE_TP_TIERED_ENABLED", "CYCLE_TP_PCT", "CYCLE_TP_TIERED_FRAC",
        "CYCLE_TP_CONDITIONAL_EXIT", "ACCOUNT_TP_PCT",
        "AUGMENT_WT_D_AUTO_CLOSE_ENABLED", "AUGMENT_WT_4H_AUTO_CLOSE_ENABLED",
    }

    n_syms = len(subset)
    floor_trades = n_syms * args.min_trades_per_sym

    with open(out_csv, "a", newline="") as csv_f:
        w = csv.writer(csv_f)
        if not csv_exists:
            w.writerow(["rank", "pool_sharpe", "acc_gain_pct", "max_dd_pct", "trades",
                        "stage1_sharpe", "stage1_gain", "stage1_dd", "stage1_trades",
                        "elapsed_s", "symbols_used", "overrides_json"])

        for rank, cand in enumerate(candidates):
            ovr = {k: v for k, v in cand["overrides"].items() if k not in FORBIDDEN}
            cfg = QuickConfig()
            cfg.MODE = args.mode
            cfg.LTF = "3m" if args.mode == "crypto" else "5m"
            for k, v in ovr.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            t0 = time.time()
            try:
                r = simulate(subset, cfg)
            except Exception as e:
                print(f"[S2] rank={rank} ERR: {e}")
                continue
            el = time.time() - t0
            gain = r.get("accumulated_gain_pct", 0.0)
            sharpe = r.get("pool_sharpe", 0.0)
            dd = r.get("max_dd_pct", 0.0)
            tr = r.get("trades", 0)
            if tr < floor_trades:
                print(f"[S2] rank={rank} REJECT tr={tr}<{floor_trades} (stage1_sharpe={cand['stage1_sharpe']:.3f})")
                continue
            w.writerow([rank, round(sharpe, 4), round(gain, 2), round(dd, 2), tr,
                        cand["stage1_sharpe"], cand["stage1_gain"], cand["stage1_dd"], cand["stage1_trades"],
                        round(el, 1), len(syms), json.dumps(ovr)])
            csv_f.flush()
            print(f"[S2] rank={rank:3d} stage1={cand['stage1_sharpe']:.3f} → stage2_sharpe={sharpe:.4f} "
                  f"gain={gain:.1f}% dd={dd:.1f}% tr={tr} el={el:.0f}s")

    print(f"[S2] Done. Results in {out_csv}")
    # Quick summary
    try:
        df = pd.read_csv(out_csv)
        df["pool_sharpe"] = pd.to_numeric(df["pool_sharpe"], errors="coerce")
        best = df.nlargest(10, "pool_sharpe")
        print("\n=== Stage-2 Top 10 ===")
        print(best[["pool_sharpe", "acc_gain_pct", "max_dd_pct", "trades", "stage1_sharpe"]].to_string())
    except Exception as e:
        print(f"[S2] Summary failed: {e}")


if __name__ == "__main__":
    main()
