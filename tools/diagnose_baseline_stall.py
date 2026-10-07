#!/usr/bin/env python3
"""
Diagnose why baseline evaluations are stalling after first calculation.
The pattern shows all red cells at same cumulative baseline, suggesting
the ThreadPool or evaluation cache is getting stuck after baseline.
"""

import sys
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def test_baseline_then_variants():
    """Test if baseline works but variants hang."""
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch

    sym = "ZECUSDC"
    is_long = True

    print(f"[baseline-test] Testing {sym}_LONG")

    # Prepare once
    print(f"[baseline-test] Loading NPZ...")
    t0 = time.time()
    prepared = prepare_batch(sym, 30)
    print(f"[baseline-test] Prepared in {time.time()-t0:.2f}s")

    # Test baseline
    print(f"[baseline-test] Baseline eval...")
    t0 = time.time()
    baseline = evaluate_prepared_sanitized(prepared, {}, 30)
    baseline_time = time.time() - t0
    print(f"[baseline-test] Baseline: {baseline_time:.3f}s, gain={baseline.get('gain_pct'):.4f}, trades={baseline.get('trades')}")

    # Now test a simple variant
    print(f"[baseline-test] Testing simple variant (WT_15M_BOUNCE_ENABLED=True)...")
    t0 = time.time()
    variant1 = evaluate_prepared_sanitized(prepared, {"WT_15M_BOUNCE_ENABLED": True}, 30)
    variant1_time = time.time() - t0
    print(f"[baseline-test] Variant1: {variant1_time:.3f}s, gain={variant1.get('gain_pct'):.4f}, trades={variant1.get('trades')}")

    # Test with ThreadPool (like v15_pilot does)
    print(f"[baseline-test] Testing with ThreadPool (16 workers)...")

    configs = [
        {},
        {"BB_SQUEEZE_ENTRY_ENABLED": True},
        {"BB_SQUEEZE_ENTRY_ENABLED": False},
        {"WT_15M_BOUNCE_OPEN_ENABLED": True},
        {"WT_15M_BOUNCE_OPEN_ENABLED": False},
    ]

    results = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {}
        for i, cfg in enumerate(configs):
            def eval_fn(c):
                return evaluate_prepared_sanitized(prepared, c, 30)

            fut = executor.submit(eval_fn, cfg)
            futures[i] = (fut, cfg)

        # Wait with timeout
        deadline = time.time() + 15.0  # 15s total timeout
        for i, (fut, cfg) in futures.items():
            try:
                remaining = deadline - time.time()
                if remaining <= 0:
                    print(f"[baseline-test] Timeout waiting for eval {i}")
                    results[i] = {"error": "deadline exceeded"}
                else:
                    t0 = time.time()
                    res = fut.result(timeout=min(10.0, remaining))
                    elapsed = time.time() - t0
                    print(f"[baseline-test] Config {i} ({str(cfg)[:50]}): {elapsed:.3f}s, valid={res.get('valid')}, gain={res.get('gain_pct')}")
                    results[i] = {"time": elapsed, "valid": res.get("valid"), "gain": res.get("gain_pct")}
            except TimeoutError:
                print(f"[baseline-test] TIMEOUT on config {i} ({str(cfg)[:50]})")
                results[i] = {"error": "timeout 10s"}
            except Exception as e:
                print(f"[baseline-test] ERROR on config {i}: {e}")
                results[i] = {"error": str(e)[:100]}

    print(f"\n[baseline-test] Summary: {len([r for r in results.values() if 'error' not in r])}/{len(results)} successful")

    # Check for RAM/cache issues
    import psutil
    mem = psutil.virtual_memory()
    print(f"\n[baseline-test] Memory: {mem.percent}% used, {mem.available/1e9:.1f}GB free")

    # Check if prepared is still in scope
    if prepared is not None:
        print(f"[baseline-test] NPZ still loaded (prepared object valid)")

    return results


def test_sequential_vs_parallel():
    """Test if ThreadPool vs serial makes a difference."""
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch

    sym = "ZECUSDC"
    prepared = prepare_batch(sym, 30)

    configs = [
        {},
        {"WT_15M_BOUNCE_OPEN_ENABLED": True},
        {"BB_SQUEEZE_ENTRY_ENABLED": True},
        {"DC_BREAKOUT_TF": "4h"},
        {"TECHNICAL_DC_STOP_TF": "1h"},
    ]

    print(f"\n[sequential-test] Testing {len(configs)} configs sequentially...")
    t0 = time.time()
    seq_results = []
    for i, cfg in enumerate(configs):
        t1 = time.time()
        res = evaluate_prepared_sanitized(prepared, cfg, 30)
        elapsed = time.time() - t1
        seq_results.append({"config": i, "time": elapsed, "valid": res.get("valid")})
        print(f"  Config {i}: {elapsed:.3f}s")
    seq_total = time.time() - t0
    print(f"  Total: {seq_total:.3f}s")

    print(f"\n[parallel-test] Testing {len(configs)} configs in parallel (4 workers)...")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    t0 = time.time()
    par_results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(evaluate_prepared_sanitized, prepared, cfg, 30): i for i, cfg in enumerate(configs)}

        for fut in as_completed(futures, timeout=30.0):
            i = futures[fut]
            t1 = time.time()
            try:
                res = fut.result()
                elapsed = time.time() - t1
                par_results.append({"config": i, "time": elapsed, "valid": res.get("valid")})
                print(f"  Config {i}: {elapsed:.3f}s")
            except Exception as e:
                print(f"  Config {i}: ERROR {e}")
    par_total = time.time() - t0
    print(f"  Total: {par_total:.3f}s")

    print(f"\n[comparison] Sequential: {seq_total:.1f}s, Parallel: {par_total:.1f}s")


if __name__ == "__main__":
    try:
        print("=== BASELINE STALL DIAGNOSIS ===\n")
        test_baseline_then_variants()
        test_sequential_vs_parallel()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        traceback.print_exc()
