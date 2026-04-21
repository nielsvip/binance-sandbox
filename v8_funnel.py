#!/usr/bin/env python3
"""
v8_funnel.py — Multi-tier parallel validation funnel for v8_quick_engine configs.

Pipeline:
  Stage-1 CSVs (6-12 sym autonomous workers)
      ↓ load top N candidates by sharpe
  [Tier-A] 12-sym: threads share ONE loaded dataset → parallel simulate() calls
      ↓ filter: sharpe >= min_sharpe_a, trades >= 12 * min_trades_per_sym
  [Tier-B] 24-sym: same thread-parallel approach, bigger dataset
      ↓ filter: sharpe >= min_sharpe_b, trades >= 24 * min_trades_per_sym
  [Tier-C] 48/114-sym: final validated results (sequential on big datasets)
      → tier_c_<mode>.csv

Key design: NPZ loaded ONCE per tier, multiple threads call simulate() concurrently.
numpy releases the GIL during array ops → real speedup without RAM multiplication.
This avoids the OOM that occurs when ProcessPoolExecutor spawns N copies of the dataset.

Typical usage:
  # Crypto — on S1 or MacBook
  python3 v8_funnel.py \\
    --stage1-glob "data/autonomous/crypto_swarm/w*/autonomous_crypto.csv" \\
    --mode crypto --start 2022-01-01 --npz-dir backtest_v8/indicators \\
    --out-dir data/funnel_validated/crypto \\
    --tier-a-symbols 12 --tier-a-workers 6 --min-sharpe-a 0.25 \\
    --tier-b-symbols 24 --tier-b-workers 3 --min-sharpe-b 0.35 \\
    --tier-c-symbols 48 --tier-c-workers 1 --min-sharpe-c 0.45 \\
    --top-n 2000

  # Tradier — on S2
  python3 v8_funnel.py \\
    --stage1-glob "data/autonomous/tradier_swarm/w*/autonomous_tradier.csv" \\
    --mode tradier --start 2024-01-01 --npz-dir backtest_v8/indicators \\
    --out-dir data/funnel_validated/tradier \\
    --tier-a-symbols 12 --tier-a-workers 4 --min-sharpe-a 0.20 \\
    --tier-b-symbols 24 --tier-b-workers 3 --min-sharpe-b 0.30 \\
    --tier-c-symbols 114 --tier-c-workers 1 --min-sharpe-c 0.40 \\
    --top-n 2000
"""
import argparse, csv, gc, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

FORBIDDEN = {
    "AUGMENT_PT_ENABLED", "AUGMENT_PT_PCT",
    "CYCLE_TP_TIERED_ENABLED", "CYCLE_TP_PCT", "CYCLE_TP_TIERED_FRAC",
    "CYCLE_TP_CONDITIONAL_EXIT", "ACCOUNT_TP_PCT",
    "AUGMENT_WT_D_AUTO_CLOSE_ENABLED", "AUGMENT_WT_4H_AUTO_CLOSE_ENABLED",
}

CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")


def load_stage1_candidates(glob_patterns: list, min_sharpe: float, top_n: int) -> list:
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
    qualified = all_df[all_df.pool_sharpe >= min_sharpe].sort_values("pool_sharpe", ascending=False)
    seen = set()
    unique = []
    for _, row in qualified.iterrows():
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
            })
        if len(unique) >= top_n:
            break
    print(f"[FUNNEL] Loaded {len(unique)} unique candidates "
          f"(from {len(dfs)} files, {len(all_df)} rows total, "
          f"{len(qualified)} above min_sharpe={min_sharpe})")
    return unique


def _select_syms(npz_dir: str, mode: str, n: int) -> list:
    syms = []
    for p in sorted(Path(npz_dir).glob("*.npz")):
        sym = p.stem
        is_crypto = any(sym.endswith(s) for s in CRYPTO_SUFFIXES)
        if mode == "crypto" and not is_crypto:
            continue
        if mode == "tradier" and is_crypto:
            continue
        syms.append(sym)
    return syms[:n]


def _run_one(cand: dict, mode: str, subset: dict, floor_trades: int, simulate_fn, QuickConfig_cls) -> dict:
    """Thread worker: simulate one config on already-loaded subset."""
    ovr = {k: v for k, v in cand["overrides"].items() if k not in FORBIDDEN}
    cfg = QuickConfig_cls()
    cfg.MODE = mode
    cfg.LTF = "3m" if mode == "crypto" else "5m"
    for k, v in ovr.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    t0 = time.time()
    try:
        r = simulate_fn(subset, cfg)
    except Exception as e:
        return {"error": str(e), "pool_sharpe": 0.0, "trades": 0, "passed": False,
                "stage1_sharpe": cand["stage1_sharpe"], "overrides": ovr}
    el = time.time() - t0
    tr = int(r.get("trades", 0))
    return {
        "pool_sharpe": round(float(r.get("pool_sharpe", 0.0)), 4),
        "acc_gain_pct": round(float(r.get("accumulated_gain_pct", 0.0)), 2),
        "max_dd_pct": round(float(r.get("max_dd_pct", 0.0)), 2),
        "trades": tr,
        "elapsed_s": round(el, 1),
        "stage1_sharpe": cand["stage1_sharpe"],
        "stage1_gain": cand["stage1_gain"],
        "stage1_dd": cand["stage1_dd"],
        "stage1_trades": cand["stage1_trades"],
        "overrides": ovr,
        "passed": tr >= floor_trades,
    }


def run_tier(candidates: list, mode: str, syms: list, start: str, npz_dir: str,
             min_sharpe: float, min_trades_per_sym: int, workers: int,
             tier: str, out_csv: Path) -> list:
    """Run one tier: load data once, fan out to thread workers, filter survivors."""
    if not candidates:
        return []

    floor_trades = len(syms) * min_trades_per_sym
    n_workers = min(workers, len(candidates))

    print(f"\n[{tier}] Testing {len(candidates)} configs on {len(syms)} syms "
          f"(floor={floor_trades} trades, min_sharpe={min_sharpe}, threads={n_workers})", flush=True)

    sys.path.insert(0, str(Path(__file__).parent))
    from v8_quick_engine import QuickConfig, load_npz, simulate

    print(f"[{tier}] Loading {len(syms)} symbols ...", flush=True)
    t_load = time.time()
    subset = load_npz(mode, syms, start, npz_dir)
    gc.collect()
    print(f"[{tier}] Data loaded in {time.time()-t_load:.0f}s", flush=True)

    t_start = time.time()
    all_results = []

    if n_workers == 1:
        for i, cand in enumerate(candidates):
            r = _run_one(cand, mode, subset, floor_trades, simulate, QuickConfig)
            all_results.append(r)
            status = "PASS" if r["passed"] and r["pool_sharpe"] >= min_sharpe else "FAIL"
            print(f"[{tier}] {i+1}/{len(candidates)} {status} "
                  f"sharpe={r['pool_sharpe']:.4f} gain={r.get('acc_gain_pct',0):.0f}% "
                  f"tr={r['trades']} el={r['elapsed_s']:.0f}s", flush=True)
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = {ex.submit(_run_one, c, mode, subset, floor_trades, simulate, QuickConfig): i
                    for i, c in enumerate(candidates)}
            done = 0
            for fut in as_completed(futs):
                done += 1
                r = fut.result()
                all_results.append(r)
                status = "PASS" if r["passed"] and r["pool_sharpe"] >= min_sharpe else "FAIL"
                print(f"[{tier}] {done}/{len(candidates)} {status} "
                      f"sharpe={r['pool_sharpe']:.4f} gain={r.get('acc_gain_pct',0):.0f}% "
                      f"tr={r['trades']} el={r['elapsed_s']:.0f}s", flush=True)

    elapsed = time.time() - t_start
    valid = [r for r in all_results if r["passed"] and r["pool_sharpe"] >= min_sharpe]
    valid.sort(key=lambda r: r["pool_sharpe"], reverse=True)

    csv_exists = out_csv.exists()
    with open(out_csv, "a", newline="") as f:
        w = csv.writer(f)
        if not csv_exists:
            w.writerow(["pool_sharpe", "acc_gain_pct", "max_dd_pct", "trades", "elapsed_s",
                        "symbols_used", "stage1_sharpe", "overrides_json"])
        for r in valid:
            w.writerow([r["pool_sharpe"], r["acc_gain_pct"], r["max_dd_pct"], r["trades"],
                        r["elapsed_s"], len(syms), r["stage1_sharpe"], json.dumps(r["overrides"])])

    print(f"[{tier}] Done in {elapsed:.0f}s — {len(valid)} pass / "
          f"{len(all_results) - len(valid)} fail", flush=True)
    if valid:
        print(f"[{tier}] Best: sharpe={valid[0]['pool_sharpe']:.4f} "
              f"gain={valid[0]['acc_gain_pct']:.0f}% "
              f"dd={valid[0]['max_dd_pct']:.1f}% tr={valid[0]['trades']}", flush=True)

    del subset
    gc.collect()
    return valid


def main():
    ap = argparse.ArgumentParser(description="Multi-tier parallel v8_quick funnel")
    ap.add_argument("--stage1-glob", nargs="+", required=True)
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--min-stage1-sharpe", type=float, default=0.2)
    ap.add_argument("--top-n", type=int, default=2000)
    ap.add_argument("--min-trades-per-sym", type=int, default=30)

    ap.add_argument("--tier-a-symbols", type=int, default=12)
    ap.add_argument("--tier-a-workers", type=int, default=4)
    ap.add_argument("--min-sharpe-a", type=float, default=0.25)

    ap.add_argument("--tier-b-symbols", type=int, default=24)
    ap.add_argument("--tier-b-workers", type=int, default=3)
    ap.add_argument("--min-sharpe-b", type=float, default=0.35)

    ap.add_argument("--tier-c-symbols", type=int, default=48)
    ap.add_argument("--tier-c-workers", type=int, default=1)
    ap.add_argument("--min-sharpe-c", type=float, default=0.45)

    ap.add_argument("--skip-tier-a", action="store_true")
    ap.add_argument("--skip-tier-b", action="store_true")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t_total = time.time()
    candidates = load_stage1_candidates(args.stage1_glob, args.min_stage1_sharpe, args.top_n)
    if not candidates:
        print("[FUNNEL] No candidates. Exiting.")
        return

    if not args.skip_tier_a:
        syms_a = _select_syms(args.npz_dir, args.mode, args.tier_a_symbols)
        survivors_a = run_tier(candidates, args.mode, syms_a, args.start, args.npz_dir,
                               args.min_sharpe_a, args.min_trades_per_sym,
                               args.tier_a_workers, "TIER-A",
                               out / f"tier_a_{args.mode}.csv")
    else:
        survivors_a = candidates
        print(f"[FUNNEL] Tier-A skipped — {len(survivors_a)} to Tier-B")

    if not survivors_a:
        print("[FUNNEL] Tier-A → 0 survivors. Done.")
        return

    if not args.skip_tier_b:
        syms_b = _select_syms(args.npz_dir, args.mode, args.tier_b_symbols)
        survivors_b = run_tier(survivors_a, args.mode, syms_b, args.start, args.npz_dir,
                               args.min_sharpe_b, args.min_trades_per_sym,
                               args.tier_b_workers, "TIER-B",
                               out / f"tier_b_{args.mode}.csv")
    else:
        survivors_b = survivors_a
        print(f"[FUNNEL] Tier-B skipped — {len(survivors_b)} to Tier-C")

    if not survivors_b:
        print("[FUNNEL] Tier-B → 0 survivors. Done.")
        return

    syms_c = _select_syms(args.npz_dir, args.mode, args.tier_c_symbols)
    survivors_c = run_tier(survivors_b, args.mode, syms_c, args.start, args.npz_dir,
                           args.min_sharpe_c, args.min_trades_per_sym,
                           args.tier_c_workers, "TIER-C",
                           out / f"tier_c_{args.mode}.csv")

    elapsed = time.time() - t_total
    print(f"\n[FUNNEL] Pipeline complete in {elapsed:.0f}s")
    print(f"  Stage-1: {len(candidates)} → Tier-A: {len(survivors_a)} "
          f"→ Tier-B: {len(survivors_b)} → Tier-C: {len(survivors_c)}")

    if survivors_c:
        print(f"\n=== TOP 10 (Tier-C, {len(syms_c)} syms) ===")
        for i, r in enumerate(survivors_c[:10]):
            print(f"  #{i+1:2d} sharpe={r['pool_sharpe']:.4f} gain={r['acc_gain_pct']:.0f}% "
                  f"dd={r['max_dd_pct']:.1f}% tr={r['trades']} (s1={r['stage1_sharpe']:.3f})")


if __name__ == "__main__":
    main()
