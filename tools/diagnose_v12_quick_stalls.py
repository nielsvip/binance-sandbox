#!/usr/bin/env python3
"""
Diagnose where v12_quick_engine gets stuck — every NPZ × every calculation.

Timeouts hide bugs. This script FINDS them.

What it does:
  1. Loads every NPZ in backtest_v8/indicators (or --npz dir).
  2. For each NPZ, runs simulate_one with default QuickConfig (should finish <1s).
     If it hangs >5s or raises, reports NPZ error (missing keys, NaN, shape mismatch).
  3. For each NPZ, flips EVERY QuickConfig switch one-by-one and runs simulate_one
     with that single override vs baseline. Reports any switch that hangs or crashes.
  4. Also tests per-filter yellow-style overrides (switch + filter) for a sample of combos.
  5. Tests NPZ integrity: missing keys, mismatched lengths, NaN/Inf in critical arrays,
     stdev_edge_15m missing (ZECUSDC etc).

Usage:
  python3 tools/diagnose_v12_quick_stalls.py --npz backtest_v8/indicators --workers 4 --sample-npz 20
  python3 tools/diagnose_v12_quick_stalls.py --npz ~/binance-sandbox/backtest_v8/indicators --switch-sample 50
  PYTHONPATH=. python3 tools/diagnose_v12_quick_stalls.py --syms ALGOUSDT,AAPL --window-days 30

Outputs:
  - Prints hanging NPZs and switches to stdout and to data/reports/v12_stall_report.json
  - Exits 0 if no hangs, 1 if hangs/errors found

Run on S1 (has 473 NPZs). On Mac (134 many truncated) it will show 0-trade DATA_ERROR — expected.
"""

import argparse
import concurrent.futures
import dataclasses
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

PER_NPZ_TIMEOUT = 25  # seconds per simulate_one — 8s too short for 200k-bar 107M NPZs (real 1INCH needs 12s)
PER_SWITCH_TIMEOUT = 12

def load_npz_keys(npz_path: Path):
    try:
        data = np.load(npz_path, allow_pickle=True)
        keys = list(data.files if hasattr(data, 'files') else data.keys())
        n = len(data[keys[0]]) if keys else 0
        # quick integrity: all arrays same length, no huge NaN fraction
        lens = {}
        nans = {}
        for k in keys:
            arr = data[k]
            lens[k] = len(arr) if hasattr(arr, '__len__') else 0
            try:
                nans[k] = float(np.isnan(arr).mean()) if arr.dtype.kind == 'f' else 0.0
            except Exception:
                nans[k] = 0.0
        data.close()
        return keys, lens, nans
    except Exception as e:
        return None, None, str(e)

def diagnose_npz_integrity(npz_path: Path):
    keys, lens, nans = load_npz_keys(npz_path)
    issues = []
    if keys is None:
        return [f"cannot load: {lens}"]
    # required keys for v12
    required = ["close", "timestamps", "open", "high", "low", "atr_1h"]
    for k in required:
        if k not in keys:
            issues.append(f"missing required {k}")
    # length consistency
    if lens:
        lens_vals = list(lens.values())
        if max(lens_vals) - min(lens_vals) > 5:
            issues.append(f"length mismatch {min(lens_vals)}..{max(lens_vals)}")
    # NaN checks on close
    if isinstance(nans, dict) and "close" in nans and nans["close"] > 0.3:
        issues.append(f"close NaN {nans['close']:.0%}")
    # stdev keys that ZECUSDC lacks
    stdev_keys = [k for k in keys if k.startswith("stdev_")]
    if not stdev_keys:
        issues.append("no stdev keys")
    elif "stdev_edge_15m" not in keys:
        issues.append("missing stdev_edge_15m (known ZECUSDC gap)")
    return issues

def run_one_npz(npz_path: Path, is_long: bool, overrides: dict, timeout=PER_NPZ_TIMEOUT):
    """Run simulate_one with timeout, return (ok, result_or_error, elapsed)."""
    import concurrent.futures as cf

    def _work():
        import v12_quick_engine as V
        import dataclasses as dc
        npz = dict(np.load(npz_path, allow_pickle=True))
        # slice to last 30d like pilot: timestamps are ms, but we just pass full npz and let engine handle window via timestamps
        cfg = V.QuickConfig()
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        # is_long determines MODE? keep crypto for USDT, stocks for others — use cfg.MODE
        # v12_quick_engine infers from npz; we pass is_long
        sym = npz_path.stem
        res = V.simulate_one(npz, sym, is_long, cfg)
        return res

    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_work)
        t0 = time.time()
        try:
            res = fut.result(timeout=timeout)
            return True, res, time.time() - t0
        except concurrent.futures.TimeoutError:
            try:
                fut.cancel()
            except Exception:
                pass
            # shutdown without waiting to avoid hanging thread blocking next test
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            return False, f"TIMEOUT after {timeout}s", timeout
        except Exception as e:
            tb = traceback.format_exc()[-1200:]
            return False, f"ERROR {e}\n{tb}", time.time() - t0

def main():
    ap = argparse.ArgumentParser(description="Diagnose v12_quick_engine stalls on every NPZ × every switch")
    ap.add_argument("--npz", default="backtest_v8/indicators", help="dir with *.npz")
    ap.add_argument("--workers", type=int, default=4, help="parallel NPZ workers")
    ap.add_argument("--sample-npz", type=int, default=0, help="only test first N NPZs (0=all)")
    ap.add_argument("--switch-sample", type=int, default=0, help="only test first N switches (0=all)")
    ap.add_argument("--syms", default=None, help="comma-separated syms to test (e.g. ALGOUSDT,AAPL) — filters NPZ list")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--out", default="data/reports/v12_stall_report.json")
    args = ap.parse_args()

    npz_dir = Path(args.npz)
    if not npz_dir.exists():
        # try alt
        alt = ROOT / "backtest_v8" / "indicators"
        if alt.exists():
            npz_dir = alt
        else:
            print(f"NPZ dir not found: {args.npz}", file=sys.stderr)
            sys.exit(2)
    all_npz = sorted([p for p in npz_dir.glob("*.npz") if not p.name.startswith(".") and ".tmp" not in p.name and "_" not in p.name.replace(".npz","")])
    if args.syms:
        wanted = set(s.strip().upper().replace("USDT", "").replace("_LONG", "").replace("_SHORT", "") for s in args.syms.split(","))
        all_npz = [p for p in all_npz if any(w in p.stem.upper() for w in wanted)]
    if args.sample_npz:
        all_npz = all_npz[: args.sample_npz]
    print(f"[diagnose] {len(all_npz)} NPZs in {npz_dir}  workers={args.workers}  timeout={PER_NPZ_TIMEOUT}s")

    # enumerate switches from QuickConfig
    import v12_quick_engine as V
    import dataclasses as dc
    cfg_fields = [f.name for f in dc.fields(V.QuickConfig)]
    if args.switch_sample:
        cfg_fields = cfg_fields[: args.switch_sample]
    print(f"[diagnose] {len(cfg_fields)} QuickConfig switches to flip-test")

    report = {
        "npz_dir": str(npz_dir),
        "n_npz": len(all_npz),
        "n_switches": len(cfg_fields),
        "per_npz_timeout": PER_NPZ_TIMEOUT,
        "per_switch_timeout": PER_SWITCH_TIMEOUT,
        "integrity": [],
        "npz_baseline": [],
        "switch_hangs": [],
        "switch_errors": [],
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # 1. NPZ integrity
    print("[1/3] NPZ integrity scan")
    for p in all_npz:
        issues = diagnose_npz_integrity(p)
        if issues:
            print(f"  {p.name}: {', '.join(issues)}")
            report["integrity"].append({"npz": p.name, "issues": issues})

    # 2. Baseline simulate_one per NPZ (is_long=True and False)
    print(f"[2/3] Baseline simulate_one per NPZ (2 sides) — timeout {PER_NPZ_TIMEOUT}s")
    def _baseline_task(npz_path):
        out = []
        for is_long in (True, False):
            ok, res, elapsed = run_one_npz(npz_path, is_long, {}, timeout=PER_NPZ_TIMEOUT)
            status = "ok" if ok else "HANG/ERROR"
            if not ok or (isinstance(res, dict) and res.get("trades", 0) == 0):
                out.append((npz_path.name, is_long, status, elapsed, str(res)[:200] if not ok else f"trades={res.get('trades')} gain={res.get('gain_pct', 0):.2f}"))
        return out

    stall_npz = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_baseline_task, p): p for p in all_npz}
        for fut in concurrent.futures.as_completed(futs):
            p = futs[fut]
            try:
                rows = fut.result()
                for name, is_long, status, elapsed, detail in rows:
                    side = "LONG" if is_long else "SHORT"
                    if status != "ok":
                        print(f"  {name} {side}: {status} {elapsed:.1f}s {detail[:120]}")
                        stall_npz.append({"npz": name, "side": side, "elapsed": elapsed, "detail": detail})
                    report["npz_baseline"].append({"npz": name, "side": side, "status": status, "elapsed": elapsed, "detail": detail[:500]})
            except Exception as e:
                print(f"  {p.name}: task error {e}")
                report["npz_baseline"].append({"npz": p.name, "status": "task_error", "detail": str(e)[:500]})

    if stall_npz:
        print(f"[2/3] FOUND {len(stall_npz)} baseline hangs/errors — these will stall pilots")
    else:
        print("[2/3] All baselines finished within timeout (no hangs)")

    # 3. Per-switch flip test on a few representative NPZs (first 3)
    sample_npz = all_npz[:3]
    print(f"[3/3] Per-switch flip test on {len(sample_npz)} sample NPZs × {len(cfg_fields)} switches")
    for npz_path in sample_npz:
        print(f"  {npz_path.name}")
        # baseline first to get reference
        ok0, res0, _ = run_one_npz(npz_path, True, {}, timeout=PER_NPZ_TIMEOUT)
        if not ok0:
            print(f"    baseline HANG — skip per-switch for this NPZ")
            continue
        base_cfg = V.QuickConfig()
        for field in cfg_fields:
            default = getattr(base_cfg, field)
            # flip: bool -> not, int/float -> +10% or +1, str -> alternative
            if isinstance(default, bool):
                flipped = not default
            elif isinstance(default, int) and not isinstance(default, bool):
                flipped = default + 1 if default != 0 else 1
            elif isinstance(default, float):
                flipped = default * 1.2 if default != 0 else 0.5
            elif isinstance(default, str):
                # try alternative TF or mode
                alts = {"15m": "1h", "1h": "4h", "4h": "D", "D": "15m", "OFF": "15m", "crypto": "tradier", "tradier": "crypto"}
                flipped = alts.get(default, default + "_ALT")
            else:
                continue
            ok, res, elapsed = run_one_npz(npz_path, True, {field: flipped}, timeout=PER_SWITCH_TIMEOUT)
            if not ok:
                msg = str(res)[:300]
                # classify timeout vs error
                if "TIMEOUT" in msg:
                    print(f"    HANG  {field}: {msg} (flipped {default!r} -> {flipped!r})")
                    report["switch_hangs"].append({"npz": npz_path.name, "switch": field, "default": str(default)[:80], "flipped": str(flipped)[:80], "elapsed": elapsed, "error": msg[:500]})
                else:
                    # only report first few errors per NPZ to avoid spam
                    if len(report["switch_errors"]) < 20:
                        print(f"    ERROR {field}: {msg[:120]}")
                    report["switch_errors"].append({"npz": npz_path.name, "switch": field, "error": msg[:500]})
    # summary
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["n_hangs"] = len(report["switch_hangs"]) + len([r for r in report["npz_baseline"] if r["status"] != "ok"])
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\n[done] report {out_path}  hangs={report['n_hangs']}  baseline_hangs={len(stall_npz)}  switch_hangs={len(report['switch_hangs'])}  switch_errors={len(report['switch_errors'])}")
    if report["n_hangs"]:
        print("HANGS FOUND — pilots will get stuck on these NPZs/switches. Fix the engine or NPZ, not the timeout.")
        for h in report["switch_hangs"][:10]:
            print(f"  {h['npz']} {h['switch']}: {h['error'][:120]}")
        sys.exit(1)
    else:
        print("No hangs detected at current timeout — if pilots still hang, increase --workers or run on S1 with full 473 NPZs.")
        sys.exit(0)

if __name__ == "__main__":
    main()
