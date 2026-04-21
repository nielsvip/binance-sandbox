#!/usr/bin/env python3
"""
v8_parallel_run.py — Run backtest_v8_engine across all symbols in parallel.

Splits the symbol list across N worker subprocesses (default = cpu_count).
Each worker runs the REAL engine on its subset and writes a trades JSONL.
Aggregates all trade JSONLs at the end → pool Sharpe, gain, WR.

Usage:
    python3 v8_parallel_run.py --mode crypto --account ang --start 2022-01-01
    python3 v8_parallel_run.py --mode tradier --account trb --start 2022-01-01
    python3 v8_parallel_run.py --mode crypto --workers 8 --capital 10000
    python3 v8_parallel_run.py --mode crypto --start 2022-01-01 --npz-dir /path/to/npz
"""
import argparse
import glob
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path


PYTHON = os.environ.get("V8_PYTHON", sys.executable)
ENGINE = str(Path(__file__).parent / "backtest_v8_engine.py")


def _discover_symbols(mode: str, npz_dir: str = "") -> list[str]:
    if npz_dir:
        d = Path(npz_dir)
    else:
        base = Path(__file__).parent
        if mode == "tradier":
            candidates = [
                base / "backtest_v4_tradier" / "indicators",
                base / "backtest_v5" / "indicators_5m_tradier",
            ]
        else:
            candidates = [
                base / "backtest_v4" / "indicators",
                base / "backtest_v5" / "indicators_3m",
            ]
        d = next((p for p in candidates if p.exists()), None)
        if not d:
            print(f"[PARALLEL] No NPZ dir found for mode={mode}", flush=True)
            return []
    syms = sorted(set(Path(f).stem for f in glob.glob(str(d / "*.npz"))))
    print(f"[PARALLEL] Found {len(syms)} symbols in {d}", flush=True)
    return syms


def _split_chunks(lst: list, n: int) -> list[list]:
    k, m = divmod(len(lst), n)
    return [lst[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]


def _run_worker(args_list: list[str], worker_id: int, log_path: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["V8_WORKER_ID"] = str(worker_id)
    with open(log_path, "w") as f:
        proc = subprocess.Popen(
            args_list, stdout=f, stderr=subprocess.STDOUT,
            env=env, text=True,
        )
    return proc


def _parse_result_line(log_path: str) -> dict | None:
    try:
        with open(log_path) as f:
            for line in f:
                if line.startswith("V8_RESULT:"):
                    parts = dict(kv.split("=", 1) for kv in line.strip().split()[1:] if "=" in kv)
                    return {k: float(v) if v.replace(".", "").replace("-", "").replace("+", "").isdigit() else v
                            for k, v in parts.items()}
    except Exception:
        pass
    return None


def _find_trades_jsonl(log_path: str) -> str | None:
    try:
        with open(log_path) as f:
            for line in f:
                if line.startswith("V8_LOG:"):
                    return line.strip().split("V8_LOG:", 1)[-1].strip()
    except Exception:
        pass
    return None


def _aggregate_trades(jsonl_paths: list[str]) -> dict:
    all_pcts = []
    n_wins = n_losses = n_closes = 0
    sum_pct = 0.0
    by_symbol: dict = {}
    by_reason: dict = {}
    for path in jsonl_paths:
        if not path or not Path(path).exists():
            continue
        with open(path) as f:
            for line in f:
                try:
                    t = json.loads(line)
                except Exception:
                    continue
                pnl = t.get("pnl_pct")
                if pnl is None:
                    continue
                pnl = float(pnl)
                all_pcts.append(pnl)
                sum_pct += pnl
                n_closes += 1
                if pnl >= 0:
                    n_wins += 1
                else:
                    n_losses += 1
                sym = t.get("symbol", t.get("position_key", "?"))
                reason = t.get("close_reason", t.get("reason", "?"))
                by_symbol.setdefault(sym, {"n": 0, "sum": 0.0})
                by_symbol[sym]["n"] += 1
                by_symbol[sym]["sum"] += pnl
                by_reason.setdefault(reason, {"n": 0, "sum": 0.0})
                by_reason[reason]["n"] += 1
                by_reason[reason]["sum"] += pnl
    n = len(all_pcts)
    sharpe_pt = 0.0
    if n >= 2:
        mean_t = sum(all_pcts) / n
        std_t = math.sqrt(sum((x - mean_t) ** 2 for x in all_pcts) / n)
        sharpe_pt = mean_t / std_t if std_t > 0 else (1e9 if mean_t > 0 else 0.0)
    wr = n_wins * 100.0 / max(1, n_closes)
    return {
        "pool_sharpe_pt": sharpe_pt,
        "sum_trade_pcts": sum_pct,
        "n_closes": n_closes,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate": wr,
        "by_symbol": by_symbol,
        "by_reason": by_reason,
        "n_symbols": len(by_symbol),
    }


def main():
    parser = argparse.ArgumentParser(description="V8 parallel runner — splits symbols across workers")
    parser.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    parser.add_argument("--account", default="ang")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--workers", type=int, default=0, help="0 = cpu_count (capped at 8)")
    parser.add_argument("--symbols", default="", help="Comma-separated override (empty=all)")
    parser.add_argument("--npz-dir", default="", help="Explicit NPZ directory")
    args = parser.parse_args()

    n_cpus = os.cpu_count() or 4
    n_workers = args.workers if args.workers > 0 else min(n_cpus, 8)

    if args.symbols:
        all_syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        all_syms = _discover_symbols(args.mode, args.npz_dir)
    if not all_syms:
        print("[PARALLEL] No symbols found — exiting", flush=True)
        sys.exit(1)

    n_workers = min(n_workers, len(all_syms))
    chunks = _split_chunks(all_syms, n_workers)
    capital_per_worker = args.capital / n_workers

    out_dir = Path(__file__).parent / "backtest_v8" / "parallel_logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_ts = time.strftime("%Y%m%d_%H%M%S")

    print(f"[PARALLEL] mode={args.mode} account={args.account} start={args.start}", flush=True)
    print(f"[PARALLEL] {len(all_syms)} symbols / {n_workers} workers / capital_each=${capital_per_worker:.0f}", flush=True)

    procs = []
    log_paths = []
    for i, chunk in enumerate(chunks):
        log_path = str(out_dir / f"worker_{i}_{run_ts}.log")
        log_paths.append(log_path)
        cmd = [
            PYTHON, ENGINE,
            "--mode", args.mode,
            "--account", args.account,
            "--start", args.start,
            "--capital", str(capital_per_worker),
            "--symbols", ",".join(chunk),
        ]
        if args.npz_dir:
            cmd += ["--npz-dir", args.npz_dir]
        print(f"[PARALLEL] Worker {i}: {len(chunk)} syms → {log_path}", flush=True)
        proc = _run_worker(cmd, i, log_path)
        procs.append(proc)

    t0 = time.time()
    done = [False] * n_workers
    while not all(done):
        for i, proc in enumerate(procs):
            if not done[i] and proc.poll() is not None:
                rc = proc.returncode
                elapsed = time.time() - t0
                status = "OK" if rc == 0 else f"EXIT={rc}"
                print(f"[PARALLEL] Worker {i} done in {elapsed:.0f}s — {status}", flush=True)
                done[i] = True
        time.sleep(2)

    total_elapsed = time.time() - t0
    print(f"\n[PARALLEL] All workers done in {total_elapsed:.0f}s", flush=True)

    # Collect per-worker results
    print("\n[PARALLEL] === PER-WORKER RESULTS ===", flush=True)
    jsonl_paths = []
    for i, lp in enumerate(log_paths):
        r = _parse_result_line(lp)
        jl = _find_trades_jsonl(lp)
        if jl:
            jsonl_paths.append(jl)
        if r:
            chunk_syms = len(chunks[i])
            print(f"  Worker {i} ({chunk_syms} syms): sharpe_pt={r.get('sharpe_pt', '?')} gain_pct={r.get('gain_pct', '?')} closes={r.get('closes', '?')} WR={r.get('wins', '?')}/{r.get('closes', '?')}", flush=True)
        else:
            print(f"  Worker {i}: no V8_RESULT line found (check {lp})", flush=True)

    # Aggregate
    agg = _aggregate_trades(jsonl_paths)
    print("\n[PARALLEL] === AGGREGATED RESULT ===", flush=True)
    print(f"  pool_sharpe_pt = {agg['pool_sharpe_pt']:.4f}", flush=True)
    print(f"  sum_trade_pcts = {agg['sum_trade_pcts']:+.2f}%", flush=True)
    print(f"  closes={agg['n_closes']} wins={agg['n_wins']} losses={agg['n_losses']} WR={agg['win_rate']:.1f}%", flush=True)
    print(f"  symbols_with_trades={agg['n_symbols']}", flush=True)
    print(f"V8_PARALLEL_RESULT: pool_sharpe_pt={agg['pool_sharpe_pt']:.4f} sum_pct={agg['sum_trade_pcts']:+.2f} closes={agg['n_closes']} wr={agg['win_rate']:.1f} syms={agg['n_symbols']}", flush=True)

    if agg["by_symbol"]:
        print("\n[PARALLEL] Top symbols by gain:", flush=True)
        top = sorted(agg["by_symbol"].items(), key=lambda x: x[1]["sum"], reverse=True)[:10]
        for sym, d in top:
            print(f"  {sym:<20} n={d['n']:>4} sum={d['sum']:+.2f}%", flush=True)

    if agg["by_reason"]:
        print("\n[PARALLEL] By close reason:", flush=True)
        for reason, d in sorted(agg["by_reason"].items(), key=lambda x: x[1]["sum"]):
            print(f"  {reason:<30} n={d['n']:>4} sum={d['sum']:+.2f}%", flush=True)

    out_json = out_dir / f"parallel_result_{run_ts}.json"
    with open(out_json, "w") as f:
        json.dump(agg, f, indent=2)
    print(f"\n[PARALLEL] Aggregated result saved to {out_json}", flush=True)


if __name__ == "__main__":
    main()
