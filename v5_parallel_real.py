#!/usr/bin/env python3
"""
V5 Parallel Real — Runs ACTUAL process_position() across multiple workers.

Splits symbols into chunks, runs each chunk in a subprocess, collects results.
Each subprocess runs the REAL backtest_v5_full_tradier.py on its chunk.

Usage:
    python3 v5_parallel_real.py --workers 8 --start 2024-06-01 --noloss 0
    python3 v5_parallel_real.py --workers 8 --sweep-noloss  # sweep NOLOSS values
    python3 v5_parallel_real.py --workers 8 --sweep-ablation  # sweep ablation modes
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("parallel")

if sys.platform == "darwin":
    PY = "/opt/anaconda3/envs/binance_env/bin/python"
    BASE = Path("/Users/niels/Documents/binance")
    LOG_DIR = Path("/Users/niels/logs")
else:
    PY = "/home/niels/.conda/envs/binance_env/bin/python"
    if not Path(PY).exists():
        PY = "/home/niels/miniconda3/envs/binance_env/bin/python3"
    BASE = Path("/home/niels/binance")
    LOG_DIR = Path("/home/niels/logs")

NPZ_DIR = BASE / "backtest_v5" / "indicators_5m_tradier"
if not NPZ_DIR.exists():
    NPZ_DIR = BASE / "backtest_v4_tradier" / "indicators"
RESULTS_FILE = BASE / "v5_parallel_results.jsonl"


def get_all_symbols():
    return sorted([p.stem for p in NPZ_DIR.glob("*.npz")])


def run_single(name, symbols_str, start, noloss, ablation, timeout=600):
    """Run one backtest_v5_full_tradier.py in a subprocess."""
    logfile = LOG_DIR / f"par_{name}.log"
    cmd = [PY, "-u", str(BASE / "backtest_v5_full_tradier.py"),
           "--symbols", symbols_str, "--start", start,
           "--noloss", str(noloss), "--ablation", ablation]
    t0 = time.time()
    try:
        with open(logfile, "w") as lf:
            subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT,
                          timeout=timeout, cwd=str(BASE))
        elapsed = time.time() - t0
        return parse_log(logfile, name, elapsed)
    except subprocess.TimeoutExpired:
        return {"name": name, "error": "timeout", "elapsed": timeout}
    except Exception as e:
        return {"name": name, "error": str(e), "elapsed": time.time() - t0}


def parse_log(logfile, name, elapsed):
    r = {"name": name, "elapsed": round(elapsed, 1)}
    try:
        text = logfile.read_text()
        for line in text.split("\n"):
            if "Realized:" in line and "PnL:" in line:
                try: r["trades"] = int(line.split("Realized:")[1].split("trades")[0].strip())
                except: pass
                try: r["pnl"] = float(line.split("PnL: $")[1].split()[0].replace(",", ""))
                except: pass
            if "Win rate:" in line:
                try: r["wr"] = float(line.split("Win rate:")[1].split("%")[0].strip())
                except: pass
            if "Max DD:" in line:
                try: r["dd"] = float(line.split("Max DD:")[1].split("%")[0].strip())
                except: pass
            if "Sharpe:" in line:
                try: r["sharpe"] = float(line.split("Sharpe:")[1].split()[0].strip())
                except: pass
        # Exit breakdown
        for line in text.split("\n"):
            if "IBS" in line and "PnL:" in line and "n=" in line:
                try: r["ibs_pnl"] = r.get("ibs_pnl", 0) + float(line.split("PnL:")[1].split("$")[1].split()[0])
                except: pass
            if "WT" in line and "PnL:" in line and "n=" in line and "EXIT" not in line:
                try: r["wt_pnl"] = float(line.split("PnL:")[1].split("$")[1].split()[0])
                except: pass
        # Entry breakdown
        for line in text.split("\n"):
            if "ENTRY REASONS" in line:
                r["_in_entries"] = True
            if r.get("_in_entries") and "n=" in line:
                parts = line.strip().split()
                if parts:
                    entry_name = parts[0]
                    r.setdefault("entries", {})[entry_name] = line.strip()
        r.pop("_in_entries", None)
    except Exception as e:
        r["parse_error"] = str(e)
    return r


def print_leaderboard(results):
    valid = [r for r in results if "pnl" in r]
    valid.sort(key=lambda x: x["pnl"], reverse=True)
    logger.info(f"\n{'='*90}")
    logger.info(f"  LEADERBOARD — {len(valid)} tests completed")
    logger.info(f"{'='*90}")
    logger.info(f"  {'#':>3} {'Name':35s} {'PnL':>12s} {'Trades':>8s} {'WR':>7s} {'DD':>8s} {'Sharpe':>7s} {'Time':>6s}")
    logger.info(f"  {'-'*85}")
    for i, r in enumerate(valid):
        marker = " <<< WINNER" if i == 0 else ""
        logger.info(f"  {i+1:>3} {r['name']:35s} ${r.get('pnl',0):>11.2f} {r.get('trades',0):>8d} {r.get('wr',0):>6.1f}% {r.get('dd',0):>7.1f}% {r.get('sharpe',0):>7.2f} {r.get('elapsed',0):>5.0f}s{marker}")
    logger.info(f"{'='*90}")
    # Save to JSONL
    with open(RESULTS_FILE, "w") as f:
        for r in valid:
            f.write(json.dumps(r, default=str) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--start", type=str, default="2024-06-01")
    p.add_argument("--noloss", type=float, default=0)
    p.add_argument("--ablation", type=str, default="ALL")
    p.add_argument("--sweep-noloss", action="store_true")
    p.add_argument("--sweep-ablation", action="store_true")
    p.add_argument("--sweep-all", action="store_true")
    p.add_argument("--chunk-size", type=int, default=15)
    args = p.parse_args()

    all_syms = get_all_symbols()
    logger.info(f"Symbols: {len(all_syms)}, Workers: {args.workers}, Chunk: {args.chunk_size}")

    # Build test queue
    tests = []
    if args.sweep_all or args.sweep_ablation:
        for abl in ["ALL", "NO_STOP", "NO_AUGMENT", "NO_REENTRY", "STOP_ONLY"]:
            tests.append((f"abl_{abl}_nl{args.noloss}", ",".join(all_syms), args.start, args.noloss, abl))
    if args.sweep_all or args.sweep_noloss:
        for nl in [0, 0.5, 1.0, 2.0, 3.0]:
            tests.append((f"nl_{nl}", ",".join(all_syms), args.start, nl, "ALL"))
    if not tests:
        # Single run — split symbols across workers for speed
        chunks = [all_syms[i:i+args.chunk_size] for i in range(0, len(all_syms), args.chunk_size)]
        for ci, chunk in enumerate(chunks):
            tests.append((f"chunk_{ci}_{chunk[0]}_{chunk[-1]}", ",".join(chunk), args.start, args.noloss, args.ablation))

    logger.info(f"Tests queued: {len(tests)}")
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for name, syms, start, nl, abl in tests:
            f = executor.submit(run_single, name, syms, start, nl, abl)
            futures[f] = name
            logger.info(f"  Submitted: {name}")

        for f in as_completed(futures):
            name = futures[f]
            try:
                result = f.result()
                results.append(result)
                pnl = result.get("pnl", "?")
                trades = result.get("trades", "?")
                logger.info(f"  DONE: {name} → PnL=${pnl} trades={trades} ({result.get('elapsed',0):.0f}s)")
            except Exception as e:
                logger.error(f"  FAIL: {name} → {e}")
                results.append({"name": name, "error": str(e)})

    # Aggregate chunk results if single run
    if not (args.sweep_all or args.sweep_ablation or args.sweep_noloss):
        total_pnl = sum(r.get("pnl", 0) for r in results)
        total_trades = sum(r.get("trades", 0) for r in results)
        logger.info(f"\n  AGGREGATED: PnL=${total_pnl:.2f} from {total_trades} trades across {len(results)} chunks")

    print_leaderboard(results)


if __name__ == "__main__":
    main()
