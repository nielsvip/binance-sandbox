#!/usr/bin/env python3
"""v15_causal_worker — leave-one-out causal probe per sym_side (2026-10-08, user-approved design).

For one sym_side and one window (30 or 365 days) it evaluates the base override set, then the same set with
each key removed (the key reverts to its template/QuickConfig default). The causal effect of a key is
base_gain - ablated_gain: positive = the key helps in this sym_side. Only real evaluate_sanitized numbers
(the engine the sweeps use). Nothing is estimated and nothing is deployed.

ABLATION_DISABLE_* keys are refused unless --momentary is given (USER 2026-10-08: ablation is never on
outside a momentary test to find problem areas).

  python tools/v15_causal_worker.py --symside 1000BONKUSDC_LONG --window 30 --base base.json [--keys k1,k2] [--momentary]
  python tools/v15_causal_worker.py --merge        # rebuild data/causal/causal_index.json from all worker CSVs

Outputs: data/causal/worker/{SYMSIDE}_{window}d.csv (rows via metrics_guard.write_sharpe_row, canonical 9 cols)
         data/causal/worker/{SYMSIDE}_{window}d.json (same rows, full field set)
         data/causal/causal_index.json (switch -> per-sym_side causal effects, valid rows only)
"""
import argparse
import csv
import glob
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BASE_PATH", str(ROOT))
os.environ.setdefault("V12_NPZ_CACHE", "8")

CAUSAL_DIR = ROOT / "data" / "causal"
WORKER_DIR = CAUSAL_DIR / "worker"
INDEX_PATH = CAUSAL_DIR / "causal_index.json"
ABLATION_PREFIX = "ABLATION_DISABLE_"
CANONICAL = ["pool_sharpe", "sym_sharpe", "avg_gain_trade", "gain_per_yr", "gain_sym_yr", "trades",
             "max_dd_pct", "n_syms", "years"]


def _mode_for(symside):
    return "crypto" if symside.rsplit("_", 1)[0].endswith(("USDT", "USDC")) else "stocks"


def _refuse_ablation(keys, momentary):
    banned = [k for k in keys if k.startswith(ABLATION_PREFIX)]
    if banned and not momentary:
        raise SystemExit(f"REFUSED: ablation keys {banned} — ablation is never on outside a momentary test (use --momentary).")


def _row(symside, window, role, key, base_val, ablated_val, res, base_gain):
    trades = int(res.get("trades") or 0)
    gain = float(res.get("gain_pct") or 0.0)
    years = window / 365.0
    pool = float(res.get("pool_sharpe") or 0.0)
    causal = None if role == "base" else round(base_gain - gain, 4)
    return {
        "symside": symside, "window_days": window, "role": role, "key": key or "",
        "base_value": base_val, "ablated_value": ablated_val,
        "trades": trades, "gain_pct": round(gain, 4), "bh_pct": res.get("bh_pct"),
        "delta_vs_bh": res.get("delta_vs_bh"), "tim_pct": res.get("tim_pct"),
        "max_dd_pct": round(float(res.get("max_dd_pct") or 0.0), 4),
        "pool_sharpe": round(pool, 6), "sym_sharpe": round(pool, 6),
        "avg_gain_trade": round(gain / trades, 6) if trades else 0.0,
        "gain_per_yr": round(gain / years, 4), "gain_sym_yr": round(gain / years, 4),
        "n_syms": 1, "years": round(years, 4),
        "causal_delta_gain": causal, "valid": bool(res.get("valid")),
        "invalid_reason": res.get("invalid_reason") or "",
    }


def run(symside, window, base, keys, momentary):
    from tools.opt.v12_pilot import evaluate_sanitized
    from metrics_guard import write_sharpe_row
    keys = list(keys) if keys else list(base.keys())
    _refuse_ablation(list(base.keys()) + keys, momentary)
    base_res = evaluate_sanitized(symside, dict(base), window_days=window)
    base_gain = float(base_res.get("gain_pct") or 0.0)
    rows = [_row(symside, window, "base", None, None, None, base_res, base_gain)]
    for key in keys:
        if key not in base:
            continue
        ablated = {k: v for k, v in base.items() if k != key}
        res = evaluate_sanitized(symside, ablated, window_days=window)
        rows.append(_row(symside, window, "without", key, base[key], None, res, base_gain))
    WORKER_DIR.mkdir(parents=True, exist_ok=True)
    stem = WORKER_DIR / f"{symside}_{window}d"
    csv_path = stem.with_suffix(".csv")
    if csv_path.exists():
        csv_path.unlink()
    for r in rows:
        canonical = {c: r[c] for c in CANONICAL}
        write_sharpe_row(csv_path, {**r, **canonical}, mode=_mode_for(symside))
    stem.with_suffix(".json").write_text(json.dumps(rows, indent=1))
    return rows


def merge():
    index = {}
    for path in sorted(glob.glob(str(WORKER_DIR / "*.csv"))):
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("role") != "without" or r.get("valid") != "True" or not r.get("causal_delta_gain"):
                    continue
                index.setdefault(r["key"], []).append({
                    "symside": r["symside"], "window_days": int(r["window_days"]),
                    "causal_delta_gain": float(r["causal_delta_gain"]), "trades": int(r["trades"]),
                })
    summary = {}
    for key, effects in index.items():
        deltas = [e["causal_delta_gain"] for e in effects]
        summary[key] = {
            "n": len(deltas), "mean_causal_delta_gain": round(statistics.fmean(deltas), 4),
            "median_causal_delta_gain": round(statistics.median(deltas), 4),
            "share_helpful": round(sum(d > 0 for d in deltas) / len(deltas), 4), "effects": effects,
        }
    CAUSAL_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps({"keys": summary}, indent=1, sort_keys=True))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symside")
    ap.add_argument("--window", type=int, default=30, choices=[30, 365])
    ap.add_argument("--base", help="JSON file: {KEY: value} base override set (empty = engine defaults)")
    ap.add_argument("--keys", help="comma-separated keys to ablate (default: every key in --base)")
    ap.add_argument("--momentary", action="store_true", help="allow ABLATION_DISABLE_* keys (momentary test only)")
    ap.add_argument("--merge", action="store_true")
    args = ap.parse_args(argv)
    if args.merge:
        summary = merge()
        print(f"causal index: {len(summary)} keys -> {INDEX_PATH}")
        return 0
    if not args.symside:
        ap.error("--symside is required unless --merge")
    base = json.loads(Path(args.base).read_text()) if args.base else {}
    keys = [k for k in (args.keys or "").split(",") if k]
    rows = run(args.symside, args.window, base, keys, args.momentary)
    for r in rows:
        print(f"{r['role']:8s} {r['key'] or '-':40s} trades={r['trades']:5d} gain={r['gain_pct']:9.3f} "
              f"causal={r['causal_delta_gain']} valid={r['valid']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
