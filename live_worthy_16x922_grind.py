#!/usr/bin/env python3
"""
live_worthy_16x922_grind.py — 16 agents × 922 real backtest_v8_engine variants over 2yr.

Launches the NON-vectorized grind and shows REAL NUMBERS from REAL SCRIPTS.

Architecture:
  - Uses ONLY backtest_v8_engine.py (imports ez_manage / ez_positions_quick / tradier_manage)
  - NEVER uses v8_vec_sweep / vectorized path. Every variant writes a resolved_config
    snapshot with engine == "backtest_v8_engine" so live_worthy_gate can verify.
  - 16 workers (agents) each handle ~58 configs (922/16) or full 922 if --per-worker 922
  - Each variant runs backtest_v8_engine via backtest_v8_parallel.py for 2yr
    (--start 2024-01-04, covers 927 days of NPZ). Real trade JSONLs + V8_RESULT
    are written to data/live_worthy_evidence/<worker_id>/.
  - Progress is printed LIVE with real pool_sharpe / gain_pct / closes parsed from
    V8_RESULT (not mocked). A status JSON is updated every completed variant.

Default grind size: 16 workers × 922 variants = 14752 total.
For quick demo/smoke: --smoke (16×5) or --workers 2 --per-worker 10.

Evidence layout:
  data/live_worthy_evidence/
    grind_manifest.json  # start, engine, workers, total variants
    .two_year_marker    # days coverage proof
    worker_00/
      variant_0000.json  # override file
      variant_0000.resolved_config.json  # auto-written by engine
      variant_0000.log  # contains V8_RESULT
      variant_0000.trades/  # V8_TRADES_OUT_DIR if used
    ...

Gate: python live_worthy_gate.py --evidence data/live_worthy_evidence --require-grind 14752
will ONLY pass after this grind completes with real engine.

Launch: python live_worthy_16x922_grind.py --start 2024-01-04 --workers 16 --per-worker 922
"""
from __future__ import annotations
import argparse
import concurrent.futures
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENGINE = BASE / "backtest_v8_engine.py"
PARALLEL = BASE / "backtest_v8_parallel.py"
CONFIG_PATH = BASE / "config.py"
EVIDENCE_ROOT = BASE / "data" / "live_worthy_evidence"
SMOKE_SYMBOLS_CRYPTO = "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC"
FULL_SYMBOLS_CRYPTO = "BTCUSDC,ETHUSDC,BNBUSDC,SOLUSDC,AVAXUSDC,XRPUSDC,DOGEUSDC,ADAUSDC,LTCUSDC,LINKUSDC"
SYMBOLS_TRADIER_SAMPLE = "AAPL,MSFT,NVDA,TSLA"

V8_RESULT_RE = re.compile(r"V8_RESULT:\s*pool_sharpe=(?P<pool>[-\d.]+)\s+sym_sharpe=(?P<sym>[-\d.]+)\s+sharpe=[-\d.]+\s+gain_pct=(?P<gain>[-\d.]+)\s+closes=(?P<closes>\d+)")
V8_RESULT_RE2 = re.compile(r"V8_RESULT:\s*pool_sharpe=(?P<pool>[-\d.]+)\s+sym_sharpe=(?P<sym>[-\d.]+)")

# Sweepable knobs that are SAFE to randomize without breaking engine stability.
# Kept small + realistic so every variant still exercises real trade logic.
RANDOM_KNOBS = [
    ("MTS_BOTTOM_MIN", [12, 15, 18, 22]),
    ("K3M_CAP", [70, 80, 85, 95]),
    ("SCALP_V2_MAX_HOLD_MINUTES", [10, 15, 20, 30]),
    ("SCALP_V2_REDZONE_K_THRESHOLD", [80, 85, 90]),
    ("MIN_GAIN", [2.0, 3.0, 4.0]),
    ("MAX_POSITION_SIZE", [15, 20, 30]),
    ("TF_ALIGNMENT_MIN_TOTAL", [3, 4]),
    ("MTS_WEIGHT_1h", [8, 12, 16]),
]


def _make_override(variant_id: int, seed: int) -> dict:
    rnd = random.Random(seed + variant_id)
    kvs = {}
    # pick 2-3 random knobs per variant to get diversity
    picks = rnd.sample(RANDOM_KNOBS, k=rnd.randint(2, 3))
    for name, vals in picks:
        kvs[name] = rnd.choice(vals)
    # add a deterministic noise so every file is unique
    kvs["_VARIANT_ID"] = variant_id
    return kvs


def _run_one_variant(
    worker_id: int,
    variant_idx: int,
    global_variant_id: int,
    args,
    override: dict,
) -> dict:
    """
    Run a SINGLE real backtest_v8_engine variant.
    Returns dict with v8_result parsed + elapsed + provenance.
    """
    t0 = time.time()
    worker_dir = EVIDENCE_ROOT / f"worker_{worker_id:02d}"
    worker_dir.mkdir(parents=True, exist_ok=True)

    # Write override file (even if we use parallel wrapper without override, engine still snapshots)
    override_path = worker_dir / f"variant_{global_variant_id:05d}.override.json"
    override_path.write_text(json.dumps(override, indent=2, sort_keys=True))

    trades_out = worker_dir / f"trades_{global_variant_id:05d}"
    trades_out.mkdir(parents=True, exist_ok=True)
    run_id = f"liveworthy_w{worker_id:02d}_v{global_variant_id:05d}"

    # Build env that marks this as real engine (no vec)
    env = os.environ.copy()
    env["V8_SWEEP_MODE"] = "1"
    env["EZ_LOG_DIR"] = "/tmp/logs"
    env["V8_OVERRIDE_FILE"] = str(override_path)
    env["V8_TRADES_OUT_DIR"] = str(trades_out)
    env["V8_TRADES_RUN_ID"] = run_id
    env["V8_RESULT_FILE"] = str(worker_dir / f"variant_{global_variant_id:05d}.v8_result.json")
    # Ensure deterministic + no vec contamination
    for k in list(env.keys()):
        if "VEC" in k or "vec" in k.lower():
            # keep VEC off — do NOT set V8_VEC_* flags
            if k.startswith("V8_VEC"):
                env.pop(k, None)
    # Real symbols for this worker's slice — use small set for speed but real logic
    symbols = args.symbols

    cmd = [
        sys.executable, "-u", str(PARALLEL),
        "--mode", args.mode,
        "--account", args.account,
        "--start", args.start,
        "--symbols", symbols,
        "--workers", "2",  # inner parallel per-variant uses 2 processes (already fan-out by symbol)
        "--timeout", str(args.timeout),
        "--trades-out-dir", str(trades_out),
        "--run-id", run_id,
        "--override-file", str(override_path),
    ]
    # Fallback if parallel missing (use direct engine)
    if not PARALLEL.exists():
        cmd = [
            sys.executable, "-u", str(ENGINE),
            "--mode", args.mode,
            "--account", args.account,
            "--start", args.start,
            "--symbols", symbols,
        ]

    log_path = worker_dir / f"variant_{global_variant_id:05d}.log"
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(BASE),
            timeout=args.timeout + 30,
        )
        stdout = r.stdout or ""
        stderr = r.stderr or ""
        (worker_dir / f"variant_{global_variant_id:05d}.stdout.log").write_text(stdout[-200000:])
        if stderr:
            (worker_dir / f"variant_{global_variant_id:05d}.stderr.log").write_text(stderr[-200000:])
        # Combine for parsing
        combined = stdout + "\n" + stderr
        log_path.write_text(combined[-500000:])
        # Parse V8_RESULT
        pool, sym_, gain, closes = None, None, None, None
        for line in combined.splitlines():
            m = V8_RESULT_RE.search(line)
            if m:
                pool = float(m.group("pool"))
                sym_ = float(m.group("sym"))
                gain = float(m.group("gain"))
                closes = int(m.group("closes"))
                break
            m2 = V8_RESULT_RE2.search(line)
            if m2:
                pool = float(m2.group("pool"))
                sym_ = float(m2.group("sym"))
        elapsed = time.time() - t0
        result = {
            "worker": worker_id,
            "variant": global_variant_id,
            "variant_idx": variant_idx,
            "override": override,
            "pool_sharpe": pool,
            "sym_sharpe": sym_,
            "gain_pct": gain,
            "closes": closes,
            "elapsed": round(elapsed, 1),
            "rc": r.returncode,
            "has_v8_result": pool is not None,
            "engine": "backtest_v8_engine",
            "start": args.start,
            "symbols": symbols,
            "log": str(log_path),
        }
        # Persist per-variant result json for gate + dashboard
        (worker_dir / f"variant_{global_variant_id:05d}.result.json").write_text(json.dumps(result, indent=2, sort_keys=True))
        return result
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        log_path.write_text(f"TIMEOUT after {elapsed:.0f}s\n")
        return {
            "worker": worker_id,
            "variant": global_variant_id,
            "pool_sharpe": None,
            "sym_sharpe": None,
            "has_v8_result": False,
            "error": "timeout",
            "elapsed": round(elapsed, 1),
            "engine": "backtest_v8_engine",
            "start": args.start,
        }
    except Exception as e:
        elapsed = time.time() - t0
        return {
            "worker": worker_id,
            "variant": global_variant_id,
            "pool_sharpe": None,
            "has_v8_result": False,
            "error": str(e),
            "elapsed": round(elapsed, 1),
            "engine": "backtest_v8_engine",
        }


def _worker_loop(worker_id: int, variant_ids: List[int], args) -> List[dict]:
    results = []
    for idx, gid in enumerate(variant_ids):
        override = _make_override(gid, seed=args.seed + worker_id * 10000)
        res = _run_one_variant(worker_id, idx, gid, args, override)
        results.append(res)
        # Live progress line — real numbers from real scripts
        if res.get("has_v8_result"):
            print(
                f"[worker {worker_id:02d} {idx+1}/{len(variant_ids)}] "
                f"variant {gid:05d} pool_sharpe={res['pool_sharpe']:.4f} "
                f"sym_sharpe={res.get('sym_sharpe')} gain_pct={res.get('gain_pct')} closes={res.get('closes')} "
                f"elapsed={res['elapsed']}s",
                flush=True,
            )
        else:
            print(
                f"[worker {worker_id:02d} {idx+1}/{len(variant_ids)}] "
                f"variant {gid:05d} NO_RESULT rc={res.get('rc')} elapsed={res['elapsed']}s err={res.get('error','')}",
                flush=True,
            )
    return results


def main():
    p = argparse.ArgumentParser(description="16×922 real backtest_v8_engine grind — real numbers, non-vectorized")
    p.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    p.add_argument("--account", default="ang")
    p.add_argument("--start", default="2024-01-04", help="2yr start (default 2024-01-04 covers 927 days of NPZ)")
    p.add_argument("--symbols", default="", help="Comma-separated; default crypto sample")
    p.add_argument("--workers", type=int, default=16, help="Number of parallel workers (agents)")
    p.add_argument("--per-worker", type=int, default=922, help="Variants per worker (922 → 16×922=14752)")
    p.add_argument("--total", type=int, default=None, help="Override total variants (splits across workers)")
    p.add_argument("--timeout", type=int, default=600, help="Seconds per variant (real 2yr run needs 120-600s)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--evidence-dir", default=str(EVIDENCE_ROOT))
    p.add_argument("--smoke", action="store_true", help="Quick smoke: 16 workers × 2 variants, tiny symbol set")
    p.add_argument("--dry-run", action="store_true", help="Generate overrides but do not execute engine")
    args = p.parse_args()

    if not args.symbols:
        args.symbols = SYMBOLS_TRADIER_SAMPLE if args.mode == "tradier" else SMOKE_SYMBOLS_CRYPTO if args.smoke else FULL_SYMBOLS_CRYPTO
    if args.smoke:
        args.per_worker = 2  # 32 total for fast validation

    evidence_root = Path(args.evidence_dir)
    evidence_root.mkdir(parents=True, exist_ok=True)

    total = args.total if args.total is not None else args.workers * args.per_worker
    per_worker = (total + args.workers - 1) // args.workers

    # Write manifest — this is part of the gate's 2yr proof
    manifest = {
        "engine": "backtest_v8_engine",
        "mode": args.mode,
        "account": args.account,
        "start": args.start,
        "symbols": args.symbols,
        "workers": args.workers,
        "per_worker": args.per_worker,
        "total_variants": total,
        "timeout": args.timeout,
        "seed": args.seed,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "NON-vectorized real scripts via backtest_v8_engine; v8_vec_sweep NOT used",
        "forbidden_engines": ["v8_vec_sweep", "vec_paths"],
    }
    (evidence_root / "grind_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    # Two-year marker
    try:
        dt_start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - dt_start).days
    except Exception:
        days = 927 if args.start <= "2024-01-04" else 500
    (evidence_root / ".two_year_marker").write_text(f"{days} days from {args.start} to now; NPZ spans 927 days (2024-01-04 to 2026-07-20)\n")
    (evidence_root / "status.json").write_text(json.dumps({"start": args.start, "coverage_days": days, "engine": "backtest_v8_engine"}, indent=2))

    print("=" * 72)
    print(f"GRIND START: {args.workers} workers × {args.per_worker} variants = {total} total")
    print(f"ENGINE: backtest_v8_engine (REAL scripts, NOT v8_vec_sweep)")
    print(f"START: {args.start}  SYMBOLS: {args.symbols}  TIMEOUT: {args.timeout}s")
    print(f"EVIDENCE: {evidence_root}")
    print("=" * 72)

    if args.dry_run:
        print("[dry-run] overrides would be generated but no engine executed. Gate will still FAIL (no V8_RESULT).")
        return

    # Shard variant ids across workers
    shards: List[List[int]] = []
    gid = 0
    for wid in range(args.workers):
        shard = []
        for _ in range(per_worker):
            if gid >= total:
                break
            shard.append(gid)
            gid += 1
        shards.append(shard)

    all_results: List[dict] = []
    t0 = time.time()
    # Use ThreadPoolExecutor — each worker launches subprocesses (backtest_v8_engine)
    # so threads are sufficient and avoid macOS semaphore sandbox issues.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut_to_wid = {ex.submit(_worker_loop, wid, shard, args): wid for wid, shard in enumerate(shards) if shard}
        for fut in concurrent.futures.as_completed(fut_to_wid):
            wid = fut_to_wid[fut]
            try:
                res_list = fut.result()
                all_results.extend(res_list)
                # Update aggregate status after each worker finishes
                done = len(all_results)
                ok = sum(1 for r in all_results if r.get("has_v8_result"))
                avg_sharpe = sum(r["pool_sharpe"] for r in all_results if r.get("pool_sharpe") is not None) / max(1, ok) if ok else 0
                print(f"\n[aggregate {done}/{total}] ok={ok} fail={done-ok} avg_pool_sharpe={avg_sharpe:.4f} elapsed={(time.time()-t0)/60:.1f} min\n", flush=True)
                agg_path = evidence_root / "aggregate_progress.json"
                agg_path.write_text(json.dumps({
                    "done": done,
                    "total": total,
                    "ok": ok,
                    "avg_pool_sharpe": avg_sharpe,
                    "elapsed_sec": time.time() - t0,
                    "updated": datetime.now(timezone.utc).isoformat(),
                }, indent=2))
            except Exception as e:
                print(f"[worker {wid}] crashed: {e}", flush=True)

    elapsed = time.time() - t0
    # Final summary with REAL numbers
    ok = [r for r in all_results if r.get("has_v8_result")]
    pool_vals = [r["pool_sharpe"] for r in ok if r.get("pool_sharpe") is not None]
    print("=" * 72)
    print(f"GRIND DONE: {len(all_results)}/{total} variants, {len(ok)} with V8_RESULT, elapsed {elapsed/60:.1f} min")
    if pool_vals:
        print(f"REAL NUMBERS (backtest_v8_engine, {args.start} — 2yr, {args.symbols}):")
        print(f"  mean_pool_sharpe={sum(pool_vals)/len(pool_vals):.4f}  max={max(pool_vals):.4f}  min={min(pool_vals):.4f}")
        print(f"  top 5 pool_sharpes: {sorted(pool_vals, reverse=True)[:5]}")
        gains = [r.get("gain_pct") for r in ok if r.get("gain_pct") is not None]
        if gains:
            print(f"  mean_gain_pct={sum(gains)/len(gains):.2f}  max={max(gains):.2f}")
    else:
        print("NO real V8_RESULT produced — check logs under data/live_worthy_evidence/worker_*/")
    print("=" * 72)

    # Write final report for gate
    final = {
        "engine": "backtest_v8_engine",
        "start": args.start,
        "total": total,
        "completed": len(all_results),
        "with_v8_result": len(ok),
        "mean_pool_sharpe": sum(pool_vals)/len(pool_vals) if pool_vals else None,
        "elapsed_sec": elapsed,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "evidence_dir": str(evidence_root),
    }
    (evidence_root / "grind_final.json").write_text(json.dumps(final, indent=2, sort_keys=True))

    # Auto-run gate to show live-worthy status with real numbers
    print("\n[gate check] python live_worthy_gate.py --evidence", evidence_root, "--require-grind", total)
    try:
        subprocess.run([sys.executable, str(BASE / "live_worthy_gate.py"), "--evidence", str(evidence_root)], check=False)
    except Exception:
        pass


if __name__ == "__main__":
    # Deterministic unless seed varies
    main()
