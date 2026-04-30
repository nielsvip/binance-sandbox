#!/usr/bin/env python3
"""flz_canonical_sweep — produce REAL flz multi-sym sweep numbers, gated by metrics_guard.

The engine (v8_quick_engine) still has 33 cockroach Sharpe emitters (audit_repo_baseline.json).
This wrapper treats the engine as a black-box trade-list producer:
  1. Run simulate() per symbol with V8_TRADES_OUT_DIR set.
  2. Read per-sym JSONLs (pnl_pct field, MtM-at-end already applied by engine).
  3. Compute pool_sharpe + standard_metric_set via metrics_guard.
  4. Write ONE canonical CSV via metrics_guard.write_sharpe_row (refuses on violation).

The only Sharpe number that reaches a human comes from metrics_guard.

Usage:
  python3 flz_canonical_sweep.py --override backtest_v8/btc_loop_results/override_btc_dedicated_v3.json --tag dedv3
  python3 flz_canonical_sweep.py --override <path> --syms-min 48 --years-min 1.0 --out data/sweep_results/canonical_flz.csv

Sample-floor enforcement: refuses to write if n_syms < 48 or years < 1.0 (still records [DIAGNOSTIC]).
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path
from typing import Dict, List

RUN_ROOT = Path(__file__).resolve().parent
NPZ_DIR = RUN_ROOT / "backtest_v8" / "indicators"
DEFAULT_OUT = RUN_ROOT / "data" / "sweep_results" / "canonical_flz.csv"

CRYPTO_SUFFIX = ("USDC", "USDT")
TRADIER_SUFFIX = ()


def list_crypto_npz(npz_dir: Path) -> List[str]:
    if not npz_dir.exists():
        sys.exit(f"NPZ dir missing: {npz_dir}")
    syms = []
    for p in sorted(npz_dir.glob("*.npz")):
        name = p.stem
        if any(name.endswith(s) for s in CRYPTO_SUFFIX) and not name.startswith("tradier"):
            syms.append(name)
    return syms


def load_jsonl(path: Path, gain_field: str = "pnl_pct") -> List[float]:
    rets: List[float] = []
    if not path.exists():
        return rets
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            g = rec.get(gain_field)
            if g is None:
                continue
            try:
                rets.append(float(g))
            except Exception:
                continue
    return rets


def compute_max_dd_pct(all_rets: List[float]) -> float:
    """Cumulative-additive DD on per-trade %s. Honest, no annualization."""
    if not all_rets:
        return 0.0
    eq = 0.0
    peak = 0.0
    worst = 0.0
    for r in all_rets:
        eq += r
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > worst:
            worst = dd
    return float(worst)


def years_from_npz(npz_path: Path) -> float:
    try:
        import numpy as np
        z = np.load(str(npz_path))
        ts = z["ts"] if "ts" in z.files else z[z.files[0]]
        if len(ts) < 2:
            return 0.0
        span = float(ts[-1] - ts[0])
        return span / (365.25 * 86400)
    except Exception:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--override", required=True, help="path to override JSON")
    ap.add_argument("--tag", required=True, help="run tag (used in run_id + CSV row)")
    ap.add_argument("--syms-min", type=int, default=48, help="sample-floor crypto syms")
    ap.add_argument("--years-min", type=float, default=1.0)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output CSV path")
    ap.add_argument("--max-syms", type=int, default=0, help="cap (0=all)")
    ap.add_argument("--mode", default="crypto")
    args = ap.parse_args()

    sys.path.insert(0, str(RUN_ROOT))
    import metrics_guard as mg  # only writer of Sharpe
    import chart_sweep  # imposter-check on override
    from v8_quick_engine import simulate, QuickConfig

    override_path = Path(args.override)
    overrides = chart_sweep.load_override(override_path)  # raises on imposter

    syms = list_crypto_npz(NPZ_DIR)
    if args.max_syms > 0:
        syms = syms[: args.max_syms]
    print(f"[canonical] {len(syms)} crypto syms; floor={args.syms_min}", flush=True)

    run_id = f"canonical_{args.tag}_{int(time.time())}"
    trades_dir = RUN_ROOT / "data" / "canonical_trades" / run_id
    trades_dir.mkdir(parents=True, exist_ok=True)
    os.environ["V8_TRADES_OUT_DIR"] = str(trades_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id

    import numpy as np
    returns_by_sym: Dict[str, List[float]] = {}
    years_max = 0.0
    t0 = time.time()
    for i, sym in enumerate(syms, 1):
        npz_path = NPZ_DIR / f"{sym}.npz"
        cfg = QuickConfig()
        for k, v in overrides.items():
            if k.startswith("_"):
                continue
            try:
                setattr(cfg, k, v)
            except Exception:
                pass
        try:
            z = np.load(str(npz_path))
            simulate({sym: z}, cfg, capital=10000.0)
        except Exception as e:
            print(f"  [{i}/{len(syms)}] {sym} sim error: {e}", flush=True)
            continue
        jsonl = trades_dir / f"{run_id}__{sym}.jsonl"
        rets = load_jsonl(jsonl)
        if rets:
            returns_by_sym[sym] = rets
        yr = years_from_npz(npz_path)
        years_max = max(years_max, yr)
        if i % 10 == 0 or i == len(syms):
            elapsed = time.time() - t0
            print(f"  [{i}/{len(syms)}] {sym} trades={len(rets)} elapsed={elapsed:.0f}s", flush=True)

    metrics = mg.standard_metric_set(returns_by_sym, years=years_max)
    all_rets = [r for v in returns_by_sym.values() for r in v]
    metrics["max_dd_pct"] = compute_max_dd_pct(all_rets)
    metrics["override_path"] = str(override_path)
    metrics["tag"] = args.tag
    metrics["run_id"] = run_id

    print()
    print("=== CANONICAL FLZ SWEEP RESULT ===")
    print(mg.format_standard_set(metrics, mode=args.mode))
    print(f"  override: {override_path.name}")
    print(f"  tier: {mg.tier_name(metrics['pool_sharpe'])}")
    print(f"  sample-floor pass: n_syms={metrics['n_syms']}>={args.syms_min} years={metrics['years']:.2f}>={args.years_min}: "
          f"{metrics['n_syms'] >= args.syms_min and metrics['years'] >= args.years_min}")

    out_path = Path(args.out)
    try:
        mg.write_sharpe_row(out_path, metrics, mode=args.mode, append=True)
        print(f"  → {out_path} (written via metrics_guard.write_sharpe_row)")
    except Exception as e:
        print(f"  REFUSED by metrics_guard: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
