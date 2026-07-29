#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""backtest_v8_parallel.py — fan-out wrapper around backtest_v8_engine.py.

For each symbol, spawns one backtest_v8_engine subprocess. After all workers
complete, reads per-symbol trade JSONLs from V8_TRADES_OUT_DIR and aggregates
into canonical pool_sharpe + sym_sharpe + standard metric set per CLAUDE.md.

Why: backtest_v8_engine is sequential per-symbol. With 4 syms × 6mo serial =
~40 min. Multi-process across 4-8 cores ~ 5-10 min.

Each subprocess auto-sets:
  V8_SWEEP_MODE=1                  # silence per-bar logger noise
  V8_SKIP_PROCESS_POSITION=1       # crypto only — bypass full process_position
  TEST_RATE_GUARD_MIN_PER_DAY=0    # disable rate-guard sys.exit(2)
  V8_TRADES_OUT_DIR=<arg>          # _write_chart_trades target
  V8_TRADES_RUN_ID=<run_id>__<sym> # nope — engine appends __<sym> itself

Usage:
  python backtest_v8_parallel.py --mode crypto --account ang \\
      --start 2024-01-01 --symbols BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC \\
      --workers 4 --timeout 1800
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENGINE = BASE / "backtest_v8_engine.py"


def run_one(symbol: str, mode: str, account: str, start: str, capital: float,
            timeout: int, env_extras: dict, py_exe: str, override_file: str = "") -> dict:
    """Run engine subprocess for one symbol. Returns dict with stdout/elapsed/status."""
    env = os.environ.copy()
    env.update(env_extras)
    env["V8_SWEEP_MODE"] = "1"
    env["TEST_RATE_GUARD_MIN_PER_DAY"] = "0"
    if mode == "crypto":
        env["V8_SKIP_PROCESS_POSITION"] = "1"
    if override_file:
        from stock_v8_override_contract import establish_stock_v8_override
        establish_stock_v8_override(env, override_file)
    cmd = [
        py_exe, "-u", str(ENGINE),
        "--mode", mode, "--account", account,
        "--start", start, "--capital", str(capital),
        "--symbols", symbol,
    ]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(BASE), timeout=timeout)
        return {"sym": symbol, "stdout": r.stdout, "stderr": r.stderr,
                "rc": r.returncode, "elapsed": time.time() - t0, "status": "ok"}
    except subprocess.TimeoutExpired:
        return {"sym": symbol, "stdout": "", "stderr": "", "rc": -1,
                "elapsed": time.time() - t0, "status": "timeout"}
    except Exception as e:
        return {"sym": symbol, "stdout": "", "stderr": str(e), "rc": -2,
                "elapsed": time.time() - t0, "status": f"error:{type(e).__name__}"}


def aggregate_from_jsonl(trades_dir: Path, run_id: str, syms: list) -> dict:
    """Read per-symbol trade JSONLs (chart-server schema) and pool returns."""
    all_returns = []
    by_sym = {}
    files_found = 0
    for sym in syms:
        path = trades_dir / f"{run_id}__{sym}.jsonl"
        if not path.exists():
            continue
        files_found += 1
        sym_returns = []
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                    if "pnl_pct" in t and t["pnl_pct"] is not None:
                        sym_returns.append(float(t["pnl_pct"]))
                except Exception:
                    continue
        except Exception:
            continue
        by_sym[sym] = sym_returns
        all_returns.extend(sym_returns)
    return {"all_returns": all_returns, "by_sym": by_sym, "files_found": files_found}


def compute_metrics(all_returns: list, by_sym: dict) -> dict:
    """Canonical pool_sharpe + sym_sharpe per CLAUDE.md NO-LIES MANDATE."""
    n_trades = len(all_returns)
    if n_trades < 2:
        return {"pool_sharpe": 0.0, "sym_sharpe": 0.0, "trades": n_trades,
                "acc_gain_pct": 0.0, "avg_gain_trade": 0.0,
                "n_syms_with_data": 0, "n_syms_eligible": 0}
    pool_mean = statistics.mean(all_returns)
    pool_std = statistics.stdev(all_returns)
    pool_sharpe = pool_mean / pool_std if pool_std > 0 else 0.0
    sym_sharpes = []
    for sym, returns in by_sym.items():
        if len(returns) < 30:
            continue
        m = statistics.mean(returns)
        s = statistics.stdev(returns) if len(returns) > 1 else 0.0
        if s > 0:
            ssh = max(-5.0, min(5.0, m / s))
            sym_sharpes.append(ssh)
    sym_sharpe = statistics.mean(sym_sharpes) if sym_sharpes else 0.0
    acc_gain = sum(all_returns)
    return {
        "pool_sharpe": round(pool_sharpe, 4),
        "sym_sharpe": round(sym_sharpe, 4),
        "trades": n_trades,
        "acc_gain_pct": round(acc_gain, 4),
        "avg_gain_trade": round(acc_gain / n_trades, 6) if n_trades else 0,
        "n_syms_with_data": len([s for s, t in by_sym.items() if t]),
        "n_syms_eligible": len(sym_sharpes),
    }


def main():
    p = argparse.ArgumentParser(description="Multi-process backtest_v8_engine fan-out")
    p.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    p.add_argument("--account", default="ang")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--symbols", required=True, help="Comma-separated symbol list")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--workers", type=int, default=int(os.environ.get("V8_PARALLEL_WORKERS", "8")))
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--trades-out-dir", default="/tmp/v8_trades")
    p.add_argument("--run-id", default=f"parallel_{int(time.time())}")
    p.add_argument("--override-file", default="", help="JSON file passed via V8_OVERRIDE_FILE")
    p.add_argument("--python", default=os.environ.get("V8_PYTHON", sys.executable))
    args = p.parse_args()

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not syms:
        print("ERROR: no symbols", file=sys.stderr)
        sys.exit(2)

    trades_dir = Path(args.trades_out_dir)
    trades_dir.mkdir(parents=True, exist_ok=True)
    env_extras = {
        "V8_TRADES_OUT_DIR": str(trades_dir),
        "V8_TRADES_RUN_ID": args.run_id,
    }

    print(f"V8_PARALLEL_INIT: workers={args.workers} syms={len(syms)} run_id={args.run_id} "
          f"trades_dir={trades_dir} timeout={args.timeout}s py={args.python}", flush=True)
    t0 = time.time()
    completed = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(run_one, s, args.mode, args.account, args.start,
                             args.capital, args.timeout, env_extras, args.python,
                             args.override_file): s for s in syms}
        for fut in as_completed(futures):
            res = fut.result()
            completed.append(res)
            print(f"V8_PARALLEL_DONE: {res['sym']} status={res['status']} "
                  f"rc={res['rc']} elapsed={res['elapsed']:.1f}s", flush=True)

    total = time.time() - t0
    n_ok = sum(1 for r in completed if r["status"] == "ok")
    n_to = sum(1 for r in completed if r["status"] == "timeout")
    n_err = len(completed) - n_ok - n_to
    print(f"V8_PARALLEL_WAVE_DONE: ok={n_ok} timeout={n_to} error={n_err} "
          f"wall={total:.1f}s ({total/max(1,len(syms)):.1f}s/sym amortized)", flush=True)

    agg = aggregate_from_jsonl(trades_dir, args.run_id, syms)
    metrics = compute_metrics(agg["all_returns"], agg["by_sym"])
    print(f"V8_PARALLEL_RESULT: pool_sharpe={metrics['pool_sharpe']} "
          f"sym_sharpe={metrics['sym_sharpe']} trades={metrics['trades']} "
          f"acc_gain_pct={metrics['acc_gain_pct']} "
          f"avg_gain_trade={metrics['avg_gain_trade']} "
          f"n_syms_with_data={metrics['n_syms_with_data']} "
          f"n_syms_eligible={metrics['n_syms_eligible']} "
          f"jsonl_files={agg['files_found']}", flush=True)


if __name__ == "__main__":
    main()
