#!/usr/bin/env python3
"""flz_canonical_48sym — full canonical multi-sym sweep, gated by metrics_guard.

Streams per-symbol progress to stdout (and log). Writes ONE canonical CSV row at end
via metrics_guard.write_sharpe_row. Sample-floor enforced.

Usage:
  python3 flz_canonical_48sym.py --override <path> --tag <name> [--limit N]
"""
from __future__ import annotations
import argparse, json, os, sys, time, traceback
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent
NPZ_DIR = ROOT / "backtest_v8" / "indicators"
OUT_CSV = ROOT / "data" / "sweep_results" / "canonical_flz.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--override", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--limit", type=int, default=0, help="cap n syms (0=all crypto)")
    ap.add_argument("--mode", default="crypto")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    import numpy as np
    import metrics_guard as mg
    import chart_sweep
    from v8_quick_engine import simulate, QuickConfig

    overrides = chart_sweep.load_override(Path(args.override))  # imposter-blocked

    syms_all = sorted([p.stem for p in NPZ_DIR.glob("*.npz")
                       if (p.stem.endswith("USDC") or p.stem.endswith("USDT"))
                       and not p.stem.startswith("tradier")])
    syms = syms_all[: args.limit] if args.limit else syms_all
    # ALL syms must be in BTC_DEDICATED_SYMBOLS so engine fires the dedicated path on each.
    DEDICATED_FULL = tuple(syms)
    print(f"[48sym] {len(syms)} crypto syms, override={Path(args.override).name}, tag={args.tag}", flush=True)

    out_dir = ROOT / "data" / "canonical_trades" / f"{args.tag}_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["V8_TRADES_OUT_DIR"] = str(out_dir)
    os.environ["V8_TRADES_RUN_ID"] = args.tag

    returns_by_sym: Dict[str, List[float]] = {}
    years_max = 0.0
    t0 = time.time()
    for i, sym in enumerate(syms, 1):
        cfg = QuickConfig()
        for k, v in overrides.items():
            if k.startswith("_"):
                continue
            setattr(cfg, k, v)
        # Engine checks `sym in BTC_DEDICATED_SYMBOLS` — must include this sym AND keep
        # the full list so other syms benefit too. Set fresh on every iter (cfg is fresh).
        cfg.BTC_DEDICATED_SYMBOLS = DEDICATED_FULL
        cfg.BTC_DEDICATED_ENABLED = True
        try:
            z = np.load(str(NPZ_DIR / f"{sym}.npz"))
            simulate({sym: z}, cfg, capital=10000.0)
            ts = z["timestamps"] if "timestamps" in z.files else z[z.files[0]]
            yr = float(ts[-1] - ts[0]) / (365.25 * 86400)
            years_max = max(years_max, yr)
        except Exception as e:
            print(f"  [{i}/{len(syms)}] {sym} ERR: {e}", flush=True)
            continue
        jp = out_dir / f"{args.tag}__{sym}.jsonl"
        rets: List[float] = []
        if jp.exists():
            with jp.open() as f:
                for ln in f:
                    try:
                        rets.append(float(json.loads(ln)["pnl_pct"]))
                    except Exception:
                        pass
        if rets:
            returns_by_sym[sym] = rets
        elapsed = time.time() - t0
        print(f"  [{i}/{len(syms)}] {sym}: {len(rets)} trades  elapsed={elapsed:.0f}s", flush=True)

    if not returns_by_sym:
        print("[48sym] NO trades produced — engine config likely wrong.", flush=True)
        return 2
    m = mg.standard_metric_set(returns_by_sym, years=years_max)
    all_rets = [r for v in returns_by_sym.values() for r in v]
    eq = peak = worst = 0.0
    for r in all_rets:
        eq += r
        if eq > peak:
            peak = eq
        if peak - eq > worst:
            worst = peak - eq
    m["max_dd_pct"] = worst
    m["override_path"] = Path(args.override).name
    m["tag"] = args.tag
    print()
    print(f"=== CANONICAL {args.tag} ===", flush=True)
    print(mg.format_standard_set(m, mode=args.mode), flush=True)
    print(f"tier: {mg.tier_name(m['pool_sharpe'])}", flush=True)
    try:
        mg.write_sharpe_row(OUT_CSV, m, mode=args.mode, append=True)
        print(f"  → {OUT_CSV}", flush=True)
    except Exception as e:
        print(f"  REFUSED: {e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(99)
