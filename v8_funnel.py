#!/usr/bin/env python3
"""
v8_funnel.py — Multi-tier parallel validation funnel for v8_quick_engine configs.

Replaces the sequential validate_stage2.py with a 3-tier parallel pipeline:

  Tier-A (quick): test on tier_a_symbols (default 12) using P workers
                  → filter by min_sharpe_a + min_trades_per_sym
  Tier-B (mid):   test surviving configs on tier_b_symbols (default 24) using P workers
                  → filter by min_sharpe_b + min_trades_per_sym
  Tier-C (full):  test surviving configs on full symbols using P workers
                  → final validated results

Typical usage:
  # Crypto — on S1 (30GB RAM, 16 cores)
  python3 v8_funnel.py \
    --stage1-glob "data/autonomous/crypto_swarm/w*/autonomous_crypto.csv" \
    --mode crypto --start 2022-01-01 --npz-dir backtest_v8/indicators \
    --out-dir data/funnel_validated/crypto \
    --tier-a-symbols 12 --tier-a-workers 8 --min-sharpe-a 0.25 \
    --tier-b-symbols 24 --tier-b-workers 4 --min-sharpe-b 0.35 \
    --tier-c-symbols 48 --tier-c-workers 2 --min-sharpe-c 0.45 \
    --top-n 2000

  # Tradier — on S2 (30GB RAM, 8 cores)
  python3 v8_funnel.py \
    --stage1-glob "data/autonomous/tradier_swarm/w*/autonomous_tradier.csv" \
    --mode tradier --start 2024-01-01 --npz-dir backtest_v8/indicators \
    --out-dir data/funnel_validated/tradier \
    --tier-a-symbols 12 --tier-a-workers 6 --min-sharpe-a 0.20 \
    --tier-b-symbols 24 --tier-b-workers 4 --min-sharpe-b 0.30 \
    --tier-c-symbols 114 --tier-c-workers 2 --min-sharpe-c 0.40 \
    --top-n 2000
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


def load_stage1_candidates(glob_patterns: list[str], min_sharpe: float, top_n: int) -> list[dict]:
    import glob as _glob
    files = []
    for pat in glob_patterns:
        files.extend(_glob.glob(pat))
    if not files:
        print(f"[FUNNEL] No files matching: {glob_patterns}")
        return []
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f)
            if len(df) > 0:
                dfs.append(df)
        except Exception as e:
            print(f"[FUNNEL] Skip {f}: {e}")
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
            try:
                ovr = json.loads(key)
            except Exception:
                continue
            unique.append({
                "stage1_sharpe": float(row["pool_sharpe"]),
                "stage1_gain": float(row.get("acc_gain_pct", 0) or 0),
                "stage1_dd": float(row.get("max_dd_pct", 0) or 0),
                "stage1_trades": int(row.get("trades", 0) or 0),
                "overrides": ovr,
                "overrides_json": key,
            })
        if len(unique) >= top_n:
            break
    print(f"[FUNNEL] Loaded {len(unique)} unique candidates (from {len(dfs)} files, {len(all_df)} total rows)")
    return unique


def _select_syms(npz_dir: str, mode: str, n: int) -> list[str]:
    all_files = sorted(Path(npz_dir).glob("*.npz"))
    syms = []
    for p in all_files:
        sym = p.stem
        is_crypto = any(sym.endswith(s) for s in CRYPTO_SUFFIXES)
        if mode == "crypto" and not is_crypto:
            continue
        if mode == "tradier" and is_crypto:
            continue
        syms.append(sym)
    return syms[:n]


def _build_cfg(mode: str, overrides: dict):
    from v8_quick_engine import QuickConfig
    cfg = QuickConfig()
    cfg.MODE = mode
    cfg.LTF = "3m" if mode == "crypto" else "5m"
    for k, v in overrides.items():
        if k not in FORBIDDEN and hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg


def _worker_batch(batch: list[dict], mode: str, syms: list[str], start: str,
                  npz_dir: str, floor_trades: int, tier: str) -> list[dict]:
    """Subprocess worker: load its own NPZ slice, run all candidates in batch."""
    sys.path.insert(0, str(Path(__file__).parent))
    from v8_quick_engine import load_npz, simulate
    try:
        subset = load_npz(mode, syms, start, npz_dir)
        gc.collect()
    except Exception as e:
        print(f"[{tier}:worker] load_npz failed: {e}", flush=True)
        return []
    results = []
    for cand in batch:
        ovr = {k: v for k, v in cand["overrides"].items() if k not in FORBIDDEN}
        cfg = _build_cfg(mode, ovr)
        t0 = time.time()
        try:
            r = simulate(subset, cfg)
        except Exception as e:
            print(f"[{tier}:worker] simulate error: {e}", flush=True)
            continue
        el = time.time() - t0
        results.append({
            "pool_sharpe": round(float(r.get("pool_sharpe", 0.0)), 4),
            "acc_gain_pct": round(float(r.get("accumulated_gain_pct", 0.0)), 2),
            "max_dd_pct": round(float(r.get("max_dd_pct", 0.0)), 2),
            "trades": int(r.get("trades", 0)),
            "elapsed_s": round(el, 1),
            "symbols_used": len(syms),
            "stage1_sharpe": cand["stage1_sharpe"],
            "stage1_gain": cand["stage1_gain"],
            "stage1_dd": cand["stage1_dd"],
            "stage1_trades": cand["stage1_trades"],
            "overrides": ovr,
            "overrides_json": json.dumps(ovr),
            "passed_floor": int(r.get("trades", 0)) >= floor_trades,
        })
    return results


def _split_batches(lst: list, n: int) -> list[list]:
    k, m = divmod(len(lst), n)
    return [lst[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n) if lst[i * k + min(i, m):(i + 1) * k + min(i + 1, m)]]


def run_tier(candidates: list[dict], mode: str, syms: list[str], start: str,
             npz_dir: str, min_sharpe: float, min_trades_per_sym: int,
             workers: int, tier: str, out_csv: Path) -> list[dict]:
    """Run one tier: parallel workers, each loads own NPZ, returns filtered+sorted survivors."""
    if not candidates:
        return []
    floor_trades = len(syms) * min_trades_per_sym
    n_workers = min(workers, len(candidates))
    batches = _split_batches(candidates, n_workers)

    t_start = time.time()
    print(f"\n[{tier}] Testing {len(candidates)} configs on {len(syms)} syms "
          f"(floor={floor_trades} trades, min_sharpe={min_sharpe}, workers={n_workers})", flush=True)

    all_results = []
    if n_workers == 1:
        all_results = _worker_batch(batches[0], mode, syms, start, npz_dir, floor_trades, tier)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            futs = {ex.submit(_worker_batch, b, mode, syms, start, npz_dir, floor_trades, tier): i
                    for i, b in enumerate(batches)}
            for fut in as_completed(futs):
                try:
                    all_results.extend(fut.result())
                except Exception as e:
                    print(f"[{tier}] worker error: {e}", flush=True)

    elapsed = time.time() - t_start
    valid = [r for r in all_results if r["passed_floor"] and r["pool_sharpe"] >= min_sharpe]
    valid.sort(key=lambda r: r["pool_sharpe"], reverse=True)

    csv_exists = out_csv.exists()
    with open(out_csv, "a", newline="") as f:
        w = csv.writer(f)
        if not csv_exists:
            w.writerow(["pool_sharpe", "acc_gain_pct", "max_dd_pct", "trades", "elapsed_s",
                        "symbols_used", "stage1_sharpe", "overrides_json"])
        for r in valid:
            w.writerow([r["pool_sharpe"], r["acc_gain_pct"], r["max_dd_pct"], r["trades"],
                        r["elapsed_s"], r["symbols_used"], r["stage1_sharpe"], r["overrides_json"]])

    rejected = len(all_results) - len(valid)
    print(f"[{tier}] Done in {elapsed:.0f}s — {len(valid)} pass / {rejected} rejected "
          f"(sharpe<{min_sharpe} or trades<{floor_trades})", flush=True)
    if valid:
        print(f"[{tier}] Best: sharpe={valid[0]['pool_sharpe']:.4f} gain={valid[0]['acc_gain_pct']:.0f}% "
              f"dd={valid[0]['max_dd_pct']:.1f}% tr={valid[0]['trades']}", flush=True)
    return valid


def main():
    ap = argparse.ArgumentParser(description="Multi-tier parallel v8_quick funnel")
    ap.add_argument("--stage1-glob", nargs="+", required=True,
                    help="One or more glob patterns for Stage-1 CSV files")
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--min-stage1-sharpe", type=float, default=0.2,
                    help="Minimum sharpe to pull from Stage-1 files")
    ap.add_argument("--top-n", type=int, default=2000,
                    help="Max Stage-1 candidates to test")
    ap.add_argument("--min-trades-per-sym", type=int, default=30)

    ap.add_argument("--tier-a-symbols", type=int, default=12)
    ap.add_argument("--tier-a-workers", type=int, default=6)
    ap.add_argument("--min-sharpe-a", type=float, default=0.25)

    ap.add_argument("--tier-b-symbols", type=int, default=24)
    ap.add_argument("--tier-b-workers", type=int, default=4)
    ap.add_argument("--min-sharpe-b", type=float, default=0.35)

    ap.add_argument("--tier-c-symbols", type=int, default=48)
    ap.add_argument("--tier-c-workers", type=int, default=2)
    ap.add_argument("--min-sharpe-c", type=float, default=0.45)

    ap.add_argument("--skip-tier-a", action="store_true",
                    help="Skip Tier-A (use when Stage-1 already on 12+ syms)")
    ap.add_argument("--skip-tier-b", action="store_true",
                    help="Skip Tier-B (go straight from A to C)")

    args = ap.parse_args()

    sys.path.insert(0, str(Path(args.npz_dir).parent))
    sys.path.insert(0, str(Path(__file__).parent))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t_total = time.time()

    candidates = load_stage1_candidates(args.stage1_glob, args.min_stage1_sharpe, args.top_n)
    if not candidates:
        print("[FUNNEL] No candidates. Exiting.")
        return

    # Tier-A
    if not args.skip_tier_a:
        syms_a = _select_syms(args.npz_dir, args.mode, args.tier_a_symbols)
        survivors_a = run_tier(
            candidates, args.mode, syms_a, args.start, args.npz_dir,
            args.min_sharpe_a, args.min_trades_per_sym,
            args.tier_a_workers, "TIER-A",
            out / f"tier_a_{args.mode}.csv",
        )
    else:
        survivors_a = candidates
        print(f"[FUNNEL] Tier-A skipped — {len(survivors_a)} candidates passed to Tier-B")

    if not survivors_a:
        print("[FUNNEL] Tier-A produced 0 survivors. Done.")
        return

    # Tier-B
    if not args.skip_tier_b:
        syms_b = _select_syms(args.npz_dir, args.mode, args.tier_b_symbols)
        survivors_b = run_tier(
            survivors_a, args.mode, syms_b, args.start, args.npz_dir,
            args.min_sharpe_b, args.min_trades_per_sym,
            args.tier_b_workers, "TIER-B",
            out / f"tier_b_{args.mode}.csv",
        )
    else:
        survivors_b = survivors_a
        print(f"[FUNNEL] Tier-B skipped — {len(survivors_b)} candidates passed to Tier-C")

    if not survivors_b:
        print("[FUNNEL] Tier-B produced 0 survivors. Done.")
        return

    # Tier-C (full validation)
    syms_c = _select_syms(args.npz_dir, args.mode, args.tier_c_symbols)
    survivors_c = run_tier(
        survivors_b, args.mode, syms_c, args.start, args.npz_dir,
        args.min_sharpe_c, args.min_trades_per_sym,
        args.tier_c_workers, "TIER-C",
        out / f"tier_c_{args.mode}.csv",
    )

    total_elapsed = time.time() - t_total
    print(f"\n[FUNNEL] Complete in {total_elapsed:.0f}s")
    print(f"  Stage-1 candidates: {len(candidates)}")
    print(f"  Tier-A survivors:   {len(survivors_a)}")
    print(f"  Tier-B survivors:   {len(survivors_b)}")
    print(f"  Tier-C survivors:   {len(survivors_c)}")

    if survivors_c:
        print(f"\n=== FUNNEL TOP 10 (Tier-C validated, {len(syms_c)} syms) ===")
        for i, r in enumerate(survivors_c[:10]):
            print(f"  #{i+1:2d} sharpe={r['pool_sharpe']:.4f} gain={r['acc_gain_pct']:.0f}% "
                  f"dd={r['max_dd_pct']:.1f}% tr={r['trades']} "
                  f"(stage1={r['stage1_sharpe']:.3f})")

    try:
        df = pd.read_csv(out / f"tier_c_{args.mode}.csv")
        df["pool_sharpe"] = pd.to_numeric(df["pool_sharpe"], errors="coerce")
        print(f"\n[FUNNEL] Tier-C CSV: {len(df)} rows, "
              f"best={df['pool_sharpe'].max():.4f}, "
              f"median={df['pool_sharpe'].median():.4f}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
