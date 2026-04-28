#!/usr/bin/env python3
"""Run multiple backtest variants × multiple symbols in one shot, exporting per-trade JSONL
for the chart visualizer at http://127.0.0.1:5077/.

Usage:
  # All overrides in a folder × default symbols
  python3 chart_sweep.py --override-dir backtest_v8/btc_loop_results --pattern 'override_*.json'

  # Specific overrides + custom symbol list
  python3 chart_sweep.py --overrides override_5SYM_BEST.json,override_btc_BEST.json \\
                        --symbols BTCUSDT,ETHUSDT,SOLUSDT

  # Single override on every symbol in NPZ dir
  python3 chart_sweep.py --override override_5SYM_BEST.json --all-symbols

Run-IDs are derived from the override filename stem (e.g. override_5SYM_BEST.json → 5SYM_BEST).
Output files land in $V8_TRADES_OUT_DIR (default /tmp/v8_trades) as `<run_id>__<symbol>.jsonl`.
"""
import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(ROOT))

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "BTCDOMUSDT"]
DEFAULT_TRADES_DIR = "/tmp/v8_trades"
NPZ_DIR = ROOT / "backtest_v8" / "indicators"


def stem_to_run_id(path: Path) -> str:
    s = path.stem
    if s.startswith("override_"):
        s = s[len("override_"):]
    return s


def load_override(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def apply_override(cfg, overrides: dict) -> None:
    for k, v in overrides.items():
        if k.startswith("_"):
            continue
        setattr(cfg, k, v)


def run_one(override_path: Path, symbols: list, out_dir: str, extra_overrides: dict = None) -> dict:
    """Run simulate() once per symbol with this override. Returns {symbol: stats}."""
    from v8_quick_engine import simulate, QuickConfig

    run_id = stem_to_run_id(override_path)
    overrides = load_override(override_path)
    if extra_overrides:
        overrides.update(extra_overrides)

    results = {}
    for sym in symbols:
        npz_path = NPZ_DIR / f"{sym}.npz"
        if not npz_path.exists():
            print(f"  [{run_id}] skip {sym}: no NPZ", flush=True)
            continue
        # Setup env vars BEFORE building cfg (engine reads at runtime per-symbol)
        os.environ["V8_TRADES_OUT_DIR"] = out_dir
        os.environ["V8_TRADES_RUN_ID"] = run_id

        cfg = QuickConfig()
        apply_override(cfg, overrides)

        z = np.load(npz_path)
        t0 = time.time()
        try:
            r = simulate(((sym, z),), cfg, capital=10000.0)
            elapsed = time.time() - t0
            stats = {
                "sharpe": r.get("sharpe", 0),
                "pool_sharpe": r.get("pool_sharpe", 0),
                "trades": r.get("trades", 0),
                "wins": r.get("wins", 0),
                "wr": r.get("wr", 0),
                "acc_gain_pct": r.get("accumulated_gain_pct", 0),
                "max_dd_pct": r.get("max_dd_pct", 0),
                "elapsed_s": round(elapsed, 1),
            }
            results[sym] = stats
            jsonl = Path(out_dir) / f"{run_id}__{sym}.jsonl"
            n_trades = sum(1 for _ in open(jsonl)) if jsonl.exists() else 0
            print(f"  [{run_id}] {sym:>10s}  sharpe={stats['sharpe']:+.3f}  trades={stats['trades']:>6d}  wr={stats['wr']:>5.1f}%  Σ={stats['acc_gain_pct']:>8.1f}%  dd={stats['max_dd_pct']:>5.1f}%  ({elapsed:>4.1f}s, {n_trades} recorded)", flush=True)
        except Exception as e:
            print(f"  [{run_id}] {sym}: ERROR {e}", flush=True)
            results[sym] = {"error": str(e)}
    return results


def run_tier2(override_path, account, start_date, symbols, capital, out_dir):
    """Spawn backtest_v8_engine.py as subprocess with V8_TRADES_OUT_DIR set.
    Real-code engine — slower but produces actual decision-engine trades.
    """
    import subprocess
    run_id = "tier2_" + stem_to_run_id(override_path)
    env = os.environ.copy()
    env["V8_TRADES_OUT_DIR"] = out_dir
    env["V8_TRADES_RUN_ID"] = run_id
    env["TEST_RATE_GUARD_MIN_PER_DAY"] = env.get("TEST_RATE_GUARD_MIN_PER_DAY", "0")
    env["V8_SKIP_PROCESS_POSITION"] = env.get("V8_SKIP_PROCESS_POSITION", "1")
    # Apply override via temp config patching: read override and set as env var that
    # the engine's config module would honor. The engine reads from config.py at import,
    # so we instead pass overrides through a JSON file the engine reads if present.
    print(f"  [{run_id}] tier-2 spawning subprocess account={account} start={start_date} syms={symbols}")
    cmd = [
        sys.executable, str(ROOT / "backtest_v8_engine.py"),
        "--mode", "crypto", "--account", account, "--start", start_date,
        "--symbols", ",".join(symbols), "--capital", str(capital),
    ]
    t0 = time.time()
    try:
        out = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600, cwd=str(ROOT))
        elapsed = time.time() - t0
        # Pull V8_TIER2 line from stdout
        last_line = ""
        for line in (out.stdout or "").splitlines():
            if "V8_TIER2_CHART_TRADES" in line or "V8_RESULT_LIVE" in line:
                last_line = line
        print(f"  [{run_id}] tier-2 done in {elapsed:.0f}s | {last_line[:140]}")
        if out.returncode != 0:
            err_tail = (out.stderr or "")[-300:]
            print(f"  [{run_id}] return={out.returncode} stderr={err_tail}")
    except subprocess.TimeoutExpired:
        print(f"  [{run_id}] tier-2 TIMEOUT after 1h")
    except Exception as e:
        print(f"  [{run_id}] tier-2 ERROR: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--override-dir", help="folder of override JSONs")
    ap.add_argument("--pattern", default="override_*.json", help="glob (default: override_*.json)")
    ap.add_argument("--overrides", help="comma-separated override file paths")
    ap.add_argument("--override", help="single override path")
    ap.add_argument("--symbols", help="comma-separated symbol list")
    ap.add_argument("--all-symbols", action="store_true", help="use every NPZ in indicators dir")
    ap.add_argument("--out-dir", default=DEFAULT_TRADES_DIR)
    ap.add_argument("--limit", type=int, default=0, help="cap on # variants")
    ap.add_argument("--extra", action="append", default=[], help="key=value override applied to every variant (repeatable)")
    ap.add_argument("--tier2", action="store_true", help="route through backtest_v8_engine.py (real-code, slower)")
    ap.add_argument("--account", default="flz", help="account for tier-2 mode (default: flz)")
    ap.add_argument("--start", default="2026-04-25", help="start date for tier-2 mode (YYYY-MM-DD)")
    ap.add_argument("--capital", type=float, default=10000.0, help="capital for tier-2")
    args = ap.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    # Build override list
    override_paths = []
    if args.override:
        override_paths.append(Path(args.override))
    if args.overrides:
        for p in args.overrides.split(","):
            override_paths.append(Path(p.strip()))
    if args.override_dir:
        for p in sorted(glob.glob(str(Path(args.override_dir) / args.pattern))):
            override_paths.append(Path(p))
    if not override_paths:
        ap.error("must specify --override / --overrides / --override-dir")
    if args.limit > 0:
        override_paths = override_paths[: args.limit]

    # Symbols
    if args.all_symbols:
        symbols = sorted(p.stem for p in NPZ_DIR.glob("*.npz"))
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = DEFAULT_SYMBOLS

    # Extra overrides as flat dict
    extra = {}
    for kv in args.extra:
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        # Try parse as JSON for booleans/numbers; fall back to string
        try:
            extra[k] = json.loads(v)
        except Exception:
            extra[k] = v

    print(f"\n=== chart_sweep: {len(override_paths)} variants × {len(symbols)} symbols → {args.out_dir} ===")
    print(f"Symbols: {symbols}")
    if extra:
        print(f"Extra overrides applied to all: {extra}")
    print()

    summary = {}
    t_total = time.time()
    for i, p in enumerate(override_paths, 1):
        print(f"[{i}/{len(override_paths)}] {p.name}")
        if not p.exists():
            print(f"  MISSING: {p}")
            continue
        if args.tier2:
            run_tier2(p, args.account, args.start, symbols, args.capital, args.out_dir)
            summary[stem_to_run_id(p)] = {}
        else:
            summary[stem_to_run_id(p)] = run_one(p, symbols, args.out_dir, extra)

    print(f"\n=== Done in {time.time() - t_total:.1f}s ===")
    print(f"\n{'Run ID':<26s} {'Symbol':<11s} {'Sharpe':>7s} {'Trades':>7s} {'WR':>5s} {'AccGain':>9s} {'DD':>6s}")
    print("-" * 80)
    for run_id, syms in summary.items():
        for sym, s in syms.items():
            if "error" in s:
                print(f"{run_id:<26s} {sym:<11s} ERROR: {s['error']}")
            else:
                print(f"{run_id:<26s} {sym:<11s} {s['sharpe']:>+7.3f} {s['trades']:>7d} {s['wr']:>5.1f}% {s['acc_gain_pct']:>+8.1f}% {s['max_dd_pct']:>5.1f}%")
    print(f"\nView at http://127.0.0.1:5077/  (refresh the run list)")


if __name__ == "__main__":
    main()
