#!/usr/bin/env python3
"""
SCALP_MODE Sweep — runs the V8 backtest engine across HUNDREDS of parameter
combinations of the new HTF Breakout Scalper. Reports the top configs by
Sharpe / PnL / WR / trades.

Live restriction: only accounts in SCALP_ACCOUNTS may scalp. For backtest
testing the override forces the test account into SCALP_ACCOUNTS so the
strategy fires for that account only inside the simulator. Live config in
config.py is not touched — flipping SCALP_MODE=True there is the only knob
that activates V2 in the running scripts.

Sweep dimensions (default ~288 combos):
  - variant       (8): V1_WT_CONFIRM, V2_LH_LL_3M, V3_LH_LL_1M, V4_HA_FLIP,
                       V5_BREAK_HIGH_REENTRY, V6_COMBINED_WT_HA,
                       V7_TIGHT_TRAILING, V8_HTF_RECLAIM
  - dc_tf_list    (4): ["15m","1h"], ["15m"], ["1h"], ["15m","1h","4h"]
  - require_all   (1): True (single value — sweep can flip via --explore)
  - max_hold_min  (3): 15, 30, 60
  - max_concurrent(3): 3, 5, 10
                  = 8 × 4 × 3 × 3 = 288 combos

Each combo runs the V8 engine once on the 48-symbol crypto sample × 4yr.
Results saved to backtest_v8/sweeps/scalp_v2_sweep_<ts>.json + intermediate
progress files so a long sweep can be resumed.

Usage:
    # Full sweep (288 combos × ~3-5 min each ≈ 14-24h)
    python3 backtest_v8_scalp_v2_sweep.py --account ang --start 2022-01-01

    # Quick smoke test (8 variants only, defaults for everything else)
    python3 backtest_v8_scalp_v2_sweep.py --quick

    # Single variant deep parameter dive
    python3 backtest_v8_scalp_v2_sweep.py --variants V4_HA_FLIP --explore

    # Parallel workers (recommended for the full sweep)
    python3 backtest_v8_scalp_v2_sweep.py --workers 4
"""
import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

BASE_PATH = Path("/Users/niels/Documents/binance")
ENGINE = BASE_PATH / "backtest_v8_engine.py"
PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
SWEEP_DIR = BASE_PATH / "backtest_v8" / "sweeps"
SWEEP_DIR.mkdir(parents=True, exist_ok=True)

VARIANTS = (
    "V1_WT_CONFIRM",
    "V2_LH_LL_3M",
    "V3_LH_LL_1M",
    "V4_HA_FLIP",
    "V5_BREAK_HIGH_REENTRY",
    "V6_COMBINED_WT_HA",
    "V7_TIGHT_TRAILING",
    "V8_HTF_RECLAIM",
)

# Standard 48-symbol crypto sample, same set every backtest uses
SYMBOLS_48 = json.load(open(BASE_PATH / "backtest_48_symbols.json"))
SYMBOLS_QUICK = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT",
                 "DOGEUSDT", "MATICUSDT", "DOTUSDT", "ATOMUSDT", "NEARUSDT",
                 "TRXUSDT", "LTCUSDT"]

TIMEOUT_PER_RUN = 7200  # 2h hard cap per combo

# ──────────────────────────────────────────────────────────────────────
# Sweep grids
# ──────────────────────────────────────────────────────────────────────
DEFAULT_GRID = {
    "variant": list(VARIANTS),
    "dc_tf_list": [
        ("15m", "1h"),
        ("15m",),
        ("1h",),
        ("15m", "1h", "4h"),
    ],
    "require_all": [True],
    "max_hold_min": [15, 30, 60],
    "max_concurrent": [3, 5, 10],
}

# Deep parameter exploration on a single (or few) variant(s)
EXPLORE_GRID = {
    "variant": list(VARIANTS),
    "dc_tf_list": [
        ("15m", "1h"),
        ("15m",),
        ("1h",),
        ("15m", "1h", "4h"),
        ("3m", "15m"),
    ],
    "require_all": [True, False],
    "max_hold_min": [5, 15, 30, 60, 120],
    "max_concurrent": [3, 5, 8, 12],
}


def build_combos(grid: dict, variants_filter=None) -> list:
    """Cartesian product of grid → list of dicts. Each dict is one combo."""
    keys = list(grid.keys())
    out = []
    for vals in itertools.product(*[grid[k] for k in keys]):
        combo = dict(zip(keys, vals))
        if variants_filter and combo["variant"] not in variants_filter:
            continue
        # convert tuple → list (json-friendly)
        combo["dc_tf_list"] = list(combo["dc_tf_list"])
        out.append(combo)
    return out


def combo_name(combo: dict) -> str:
    tfs = "+".join(combo["dc_tf_list"])
    req = "ALL" if combo["require_all"] else "ANY"
    return f"{combo['variant']}|{tfs}|{req}|hold{combo['max_hold_min']}|conc{combo['max_concurrent']}"


def run_combo(args_tuple) -> dict:
    """Run V8 engine with one parameter combo. Returns result dict."""
    combo, account, start, capital, symbols, npz_dir, run_id = args_tuple
    name = combo_name(combo)
    override = {
        "SCALP_MODE": True,
        "SCALP_V2_ISOLATE": True,  # backtest: V2 is the ONLY entry/exit path on the test account
        # Force test account into SCALP_ACCOUNTS for the in-process backtest only.
        # Live config.py is untouched.
        "SCALP_ACCOUNTS": [account] if account not in ["ang", "men"] else ["ang", "men"],
        "SCALP_V2_VARIANT": combo["variant"],
        "SCALP_V2_DC_HTF_LIST": combo["dc_tf_list"],
        "SCALP_V2_DC_HTF_REQUIRE_ALL": bool(combo["require_all"]),
        "SCALP_V2_MAX_HOLD_MINUTES": float(combo["max_hold_min"]),
        "SCALP_V2_MAX_CONCURRENT": int(combo["max_concurrent"]),
        "SCALP_V2_REENTRY_COOLDOWN_S": 300,
        # Disable ALL other entry strategies in process_position eval_funcs so the
        # only thing trading on the test account is the V2 breakout scalper.
        "ABLATION_DISABLE_ENTRY_TECHNICAL": True,
        "ABLATION_DISABLE_ENTRY_LEADERBOARD": True,
        "ABLATION_DISABLE_ENTRY_RANKING": True,
        "ABLATION_DISABLE_ENTRY_REVERSAL": True,
        "ABLATION_DISABLE_REENTRY": True,
        "ABLATION_DISABLE_AUGMENTATION": True,
        # Disable delta engine entries (also fires from process_position)
        "DELTA_ENTRY_ENABLED": False,
        # Disable hedges — V2 is for gainers, not losers
        "HEDGE_MODE": False,
    }
    override_path = SWEEP_DIR / f"override_scalp_{run_id}.json"
    with open(override_path, "w") as f:
        json.dump(override, f)
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override_path)
    cmd = [PYTHON, str(ENGINE), "--mode", "crypto", "--account", account,
           "--start", start, "--capital", str(capital)]
    if symbols:
        cmd += ["--symbols", ",".join(symbols)]
    if npz_dir:
        cmd += ["--npz-dir", npz_dir]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_PER_RUN, env=env, cwd=str(BASE_PATH))
        elapsed = time.time() - t0
        out = proc.stdout + proc.stderr
        m = re.search(r"V8_RESULT:\s+sharpe=([-\d.]+)\s+pnl=([-\d.]+)\s+trades=(\d+)\s+wins=(\d+)\s+losses=(\d+)(?:\s+total_pnl_dollars=([-\d.]+))?(?:\s+avg_pnl=([-\d.]+))?", out)
        if m:
            wins, losses = int(m.group(4)), int(m.group(5))
            wr = wins / max(1, wins + losses) * 100
            res = {
                "run_id": run_id, "name": name, "combo": combo,
                "sharpe": float(m.group(1)),
                "pnl_pct": float(m.group(2)),
                "trades": int(m.group(3)),
                "wins": wins, "losses": losses, "wr_pct": round(wr, 1),
                "pnl_usd": float(m.group(6) or 0),
                "avg_pnl": float(m.group(7) or 0),
                "elapsed_s": round(elapsed, 1),
                "status": "ok",
            }
        else:
            res = {"run_id": run_id, "name": name, "combo": combo,
                   "sharpe": 0, "pnl_pct": 0, "trades": 0, "wins": 0, "losses": 0,
                   "wr_pct": 0, "pnl_usd": 0, "avg_pnl": 0,
                   "elapsed_s": round(elapsed, 1), "status": "no_result",
                   "tail": out[-300:]}
    except subprocess.TimeoutExpired:
        res = {"run_id": run_id, "name": name, "combo": combo,
               "sharpe": 0, "pnl_pct": 0, "trades": 0, "wins": 0, "losses": 0,
               "wr_pct": 0, "pnl_usd": 0, "avg_pnl": 0,
               "elapsed_s": TIMEOUT_PER_RUN, "status": "timeout"}
    except Exception as e:
        res = {"run_id": run_id, "name": name, "combo": combo,
               "sharpe": 0, "pnl_pct": 0, "trades": 0, "wins": 0, "losses": 0,
               "wr_pct": 0, "pnl_usd": 0, "avg_pnl": 0,
               "elapsed_s": round(time.time() - t0, 1), "status": "error", "tail": str(e)[:300]}
    override_path.unlink(missing_ok=True)
    return res


def print_progress(done: int, total: int, t_start: float):
    elapsed = time.time() - t_start
    eta = (elapsed / max(done, 1)) * (total - done)
    print(f"  ▶ {done}/{total} ({done*100//max(total,1)}%) | elapsed {elapsed/60:.0f}m | ETA {eta/3600:.1f}h")


def print_report(results: list, top_n: int = 20):
    ok = [r for r in results if r["status"] == "ok" and r["trades"] > 0]
    print(f"\n{'='*112}")
    print(f"  SCALP_MODE SWEEP REPORT — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*112}")
    print(f"  Total combos: {len(results)} | OK with trades: {len(ok)} | Failed/empty: {len(results) - len(ok)}")
    if not ok:
        print("  No successful combos.")
        return
    print(f"\n  TOP {top_n} BY SHARPE:")
    print(f"  {'#':<3} {'Sharpe':>8} {'PnL%':>8} {'PnL$':>10} {'Trades':>7} {'WR%':>6}  Combo")
    print(f"  {'-'*108}")
    for i, r in enumerate(sorted(ok, key=lambda x: -x["sharpe"])[:top_n], 1):
        print(f"  {i:<3} {r['sharpe']:>8.3f} {r['pnl_pct']:>8.2f} {r['pnl_usd']:>10.2f} {r['trades']:>7} {r['wr_pct']:>6.1f}  {r['name']}")
    print(f"\n  TOP 5 BY PnL$:")
    for i, r in enumerate(sorted(ok, key=lambda x: -x["pnl_usd"])[:5], 1):
        print(f"  {i:<3} {r['sharpe']:>8.3f} {r['pnl_pct']:>8.2f} {r['pnl_usd']:>10.2f} {r['trades']:>7} {r['wr_pct']:>6.1f}  {r['name']}")
    # Aggregate by variant
    by_variant = {}
    for r in ok:
        v = r["combo"]["variant"]
        by_variant.setdefault(v, []).append(r["sharpe"])
    print(f"\n  PER-VARIANT MEAN SHARPE (across all parameter combos):")
    for v in sorted(by_variant.keys(), key=lambda k: -sum(by_variant[k]) / len(by_variant[k])):
        scores = by_variant[v]
        print(f"    {v:<24} mean={sum(scores)/len(scores):>+.3f}  best={max(scores):>+.3f}  combos={len(scores)}")
    print(f"{'='*112}\n")


def main():
    p = argparse.ArgumentParser(description="SCALP_MODE backtest sweep — hundreds of variations")
    p.add_argument("--account", type=str, default="ang", help="Account name for sim")
    p.add_argument("--start", type=str, default="2022-01-01", help="Start date YYYY-MM-DD")
    p.add_argument("--capital", type=float, default=1000.0)
    p.add_argument("--variants", type=str, default="", help="Comma-separated variants (default: all 8)")
    p.add_argument("--quick", action="store_true", help="8 variants × default params only (8 combos)")
    p.add_argument("--explore", action="store_true", help="Use deep EXPLORE_GRID (more combos)")
    p.add_argument("--workers", type=int, default=1, help="Parallel workers")
    p.add_argument("--quick-symbols", action="store_true", help="Use 12-symbol set instead of 48")
    p.add_argument("--npz-dir", type=str, default="", help="Override NPZ dir")
    p.add_argument("--limit", type=int, default=0, help="Stop after N combos (0 = all)")
    args = p.parse_args()

    symbols = SYMBOLS_QUICK if args.quick_symbols else SYMBOLS_48
    variants_filter = [v.strip() for v in args.variants.split(",") if v.strip()] if args.variants else None
    if variants_filter:
        bad = [v for v in variants_filter if v not in VARIANTS]
        if bad:
            print(f"Unknown variants: {bad}. Available: {', '.join(VARIANTS)}")
            sys.exit(1)

    # Build combo list
    if args.quick:
        combos = [{"variant": v, "dc_tf_list": ["15m", "1h"], "require_all": True,
                   "max_hold_min": 30, "max_concurrent": 5} for v in (variants_filter or VARIANTS)]
    elif args.explore:
        combos = build_combos(EXPLORE_GRID, variants_filter)
    else:
        combos = build_combos(DEFAULT_GRID, variants_filter)
    if args.limit > 0:
        combos = combos[:args.limit]

    print(f"\n{'='*112}")
    print(f"  SCALP_MODE SWEEP")
    print(f"  Account: {args.account} | Start: {args.start} | Capital: ${args.capital}")
    print(f"  Symbols: {len(symbols)} ({'QUICK_12' if args.quick_symbols else 'FULL_48'})")
    print(f"  Combos:  {len(combos)} | Workers: {args.workers}")
    print(f"  Engine:  {ENGINE}")
    if combos:
        # Estimate
        est_per_combo_min = 4
        est_total_h = len(combos) * est_per_combo_min / max(args.workers, 1) / 60
        print(f"  Estimate: ~{est_per_combo_min}min × {len(combos)} ÷ {args.workers}w ≈ {est_total_h:.1f}h")
    print(f"{'='*112}\n")

    ts_run = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    progress_path = SWEEP_DIR / f"scalp_v2_progress_{ts_run}.json"

    tasks = [(combo, args.account, args.start, args.capital, symbols, args.npz_dir, f"{ts_run}_{i:04d}")
             for i, combo in enumerate(combos)]

    results = []
    t_start = time.time()
    if args.workers <= 1:
        for i, task in enumerate(tasks, 1):
            r = run_combo(task)
            results.append(r)
            print(f"  [{i}/{len(tasks)}] {r['status']:<10} Sharpe={r['sharpe']:>+.3f} PnL={r['pnl_pct']:>+6.2f}% trades={r['trades']:>5} WR={r['wr_pct']:>5.1f}% | {r['name']}")
            with open(progress_path, "w") as f:
                json.dump({"completed": i, "total": len(tasks), "results": results}, f, indent=2)
            if i % 10 == 0:
                print_progress(i, len(tasks), t_start)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(run_combo, t): t for t in tasks}
            for i, fut in enumerate(as_completed(futures), 1):
                r = fut.result()
                results.append(r)
                print(f"  [{i}/{len(tasks)}] {r['status']:<10} Sharpe={r['sharpe']:>+.3f} PnL={r['pnl_pct']:>+6.2f}% trades={r['trades']:>5} WR={r['wr_pct']:>5.1f}% | {r['name']}")
                with open(progress_path, "w") as f:
                    json.dump({"completed": i, "total": len(tasks), "results": results}, f, indent=2)
                if i % 10 == 0:
                    print_progress(i, len(tasks), t_start)

    print_report(results, top_n=20)
    out_path = SWEEP_DIR / f"scalp_v2_sweep_{args.account}_{ts_run}.json"
    with open(out_path, "w") as f:
        json.dump({"args": vars(args), "results": results,
                   "elapsed_total_s": round(time.time() - t_start, 1)}, f, indent=2)
    print(f"  Saved: {out_path}\n")


if __name__ == "__main__":
    main()
