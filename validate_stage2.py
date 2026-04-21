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
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import pandas as pd


FORBIDDEN = {
    "AUGMENT_PT_ENABLED", "AUGMENT_PT_PCT",
    "CYCLE_TP_TIERED_ENABLED", "CYCLE_TP_PCT", "CYCLE_TP_TIERED_FRAC",
    "CYCLE_TP_CONDITIONAL_EXIT", "ACCOUNT_TP_PCT",
    "AUGMENT_WT_D_AUTO_CLOSE_ENABLED", "AUGMENT_WT_4H_AUTO_CLOSE_ENABLED",
}

CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")


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


def _worker_run(batch: list[dict], mode: str, syms: list[str], start: str,
                npz_dir: str, floor_trades: int) -> list[dict]:
    """Subprocess worker: load own NPZ copy, run candidate batch, return results."""
    sys.path.insert(0, str(Path(__file__).parent))
    from v8_quick_engine import QuickConfig, load_npz, simulate
    try:
        subset = load_npz(mode, syms, start, npz_dir)
        gc.collect()
    except Exception as e:
        print(f"[S2:worker] load_npz error: {e}", flush=True)
        return []
    results = []
    for cand in batch:
        ovr = {k: v for k, v in cand["overrides"].items() if k not in FORBIDDEN}
        cfg = QuickConfig()
        cfg.MODE = mode
        cfg.LTF = "3m" if mode == "crypto" else "5m"
        for k, v in ovr.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        t0 = time.time()
        try:
            r = simulate(subset, cfg)
        except Exception as e:
            print(f"[S2:worker] simulate error: {e}", flush=True)
            continue
        el = time.time() - t0
        tr = int(r.get("trades", 0))
        results.append({
            "rank": cand.get("rank", -1),
            "pool_sharpe": round(float(r.get("pool_sharpe", 0.0)), 4),
            "acc_gain_pct": round(float(r.get("accumulated_gain_pct", 0.0)), 2),
            "max_dd_pct": round(float(r.get("max_dd_pct", 0.0)), 2),
            "trades": tr,
            "stage1_sharpe": cand["stage1_sharpe"],
            "stage1_gain": cand["stage1_gain"],
            "stage1_dd": cand["stage1_dd"],
            "stage1_trades": cand["stage1_trades"],
            "elapsed_s": round(el, 1),
            "symbols_used": len(syms),
            "overrides_json": json.dumps(ovr),
            "passed": tr >= floor_trades,
        })
        print(f"[S2:worker] rank={cand.get('rank','?'):3} stage1={cand['stage1_sharpe']:.3f} "
              f"→ stage2={r.get('pool_sharpe',0):.4f} gain={r.get('accumulated_gain_pct',0):.1f}% "
              f"tr={tr} el={el:.0f}s", flush=True)
    return results


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
    ap.add_argument("--workers", type=int, default=1, help="Parallel worker processes (each loads own NPZ copy)")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))

    candidates = load_stage1_candidates(args.stage1_glob, args.min_stage1_sharpe, args.top_n)
    if not candidates:
        print("[S2] No candidates found. Exiting.")
        return

    CRYPTO_SUFFIXES_LOCAL = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    all_files = sorted(Path(args.npz_dir).glob("*.npz"))
    syms = []
    for p in all_files:
        sym = p.stem
        is_crypto = any(sym.endswith(s) for s in CRYPTO_SUFFIXES_LOCAL)
        if args.mode == "crypto" and not is_crypto:
            continue
        if args.mode == "tradier" and is_crypto:
            continue
        syms.append(sym)
    syms = syms[: args.symbols]

    floor_trades = len(syms) * args.min_trades_per_sym
    n_workers = min(args.workers, len(candidates))

    print(f"[S2] Loading {len(syms)} symbols from {args.start}, floor={floor_trades} trades, workers={n_workers}")

    for i, cand in enumerate(candidates):
        cand["rank"] = i

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    out_csv = Path(args.out_dir) / f"stage2_{args.mode}.csv"
    csv_exists = out_csv.exists()

    k, m = divmod(len(candidates), n_workers)
    batches = [candidates[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n_workers)]
    batches = [b for b in batches if b]

    t0 = time.time()
    all_results = []

    if n_workers == 1:
        all_results = _worker_run(batches[0], args.mode, syms, args.start,
                                  args.npz_dir, floor_trades)
    else:
        print(f"[S2] Spawning {len(batches)} workers ({len(candidates)} configs split across them)")
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            futs = {ex.submit(_worker_run, b, args.mode, syms, args.start,
                              args.npz_dir, floor_trades): i
                    for i, b in enumerate(batches)}
            for fut in as_completed(futs):
                try:
                    all_results.extend(fut.result())
                except Exception as e:
                    print(f"[S2] worker error: {e}", flush=True)

    all_results.sort(key=lambda r: r["rank"])

    with open(out_csv, "a", newline="") as csv_f:
        w = csv.writer(csv_f)
        if not csv_exists:
            w.writerow(["rank", "pool_sharpe", "acc_gain_pct", "max_dd_pct", "trades",
                        "stage1_sharpe", "stage1_gain", "stage1_dd", "stage1_trades",
                        "elapsed_s", "symbols_used", "overrides_json"])
        for r in all_results:
            if not r["passed"]:
                print(f"[S2] rank={r['rank']:3d} REJECT tr={r['trades']}<{floor_trades} "
                      f"(stage1_sharpe={r['stage1_sharpe']:.3f})")
                continue
            w.writerow([r["rank"], r["pool_sharpe"], r["acc_gain_pct"], r["max_dd_pct"],
                        r["trades"], r["stage1_sharpe"], r["stage1_gain"], r["stage1_dd"],
                        r["stage1_trades"], r["elapsed_s"], r["symbols_used"], r["overrides_json"]])
            csv_f.flush()

    elapsed = time.time() - t0
    print(f"\n[S2] Done in {elapsed:.0f}s. Results in {out_csv}")

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
