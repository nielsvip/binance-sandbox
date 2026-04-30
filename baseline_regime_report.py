#!/usr/bin/env python3
# metrics_guard-clean: Sharpe used internally only, never emitted to user surface (audited 2026-04-30).
"""baseline_regime_report.py — emit LR / BB / STDEV per TF for Tier-2 baseline window.

User directive 2026-04-30: baseline must report regime context, not just pool_sharpe.
Per CLAUDE.md rule 4: ONLY pool_sharpe and sym_sharpe are decision-Sharpes; this script
adds NON-SHARPE diagnostic regime metrics (LR slope + R² of close, mean bb_pct_b, stdev of returns).

Per-symbol-per-TF, then averaged across symbols. Window = test start..end.

Usage:
    python3 baseline_regime_report.py --npz-dir <dir> --symbols A,B,C --start 2024-01-01 \\
                                       [--end 2025-04-30] [--mode tradier]
"""
import argparse
import json
import sys
from pathlib import Path
import numpy as np
from datetime import datetime, timezone


TFS_TRADIER = ["5m", "15m", "1h", "4h", "D"]
TFS_CRYPTO = ["3m", "15m", "1h", "4h", "D"]


def parse_iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()


def lr_slope_r2(y: np.ndarray) -> tuple[float, float]:
    """Linear regression of y vs index. Returns (slope, R²)."""
    if len(y) < 2:
        return 0.0, 0.0
    x = np.arange(len(y), dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    mx = x.mean(); my = y.mean()
    dx = x - mx; dy = y - my
    sxx = (dx * dx).sum()
    if sxx <= 1e-12:
        return 0.0, 0.0
    slope = (dx * dy).sum() / sxx
    intercept = my - slope * mx
    yhat = slope * x + intercept
    ss_res = ((y - yhat) ** 2).sum()
    ss_tot = (dy * dy).sum()
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else 0.0
    return float(slope), float(r2)


def regime_metrics_for_npz(npz_path: Path, tfs: list[str], t_start: float, t_end: float) -> dict:
    """Per-TF LR slope + R² of close, mean bb_pct_b, stdev of returns. Filtered to [t_start, t_end]."""
    try:
        z = np.load(npz_path, allow_pickle=True)
    except Exception as e:
        return {"error": str(e)}
    out = {}
    for tf in tfs:
        try:
            ts_key = f"timestamp_{tf}"
            close_key = f"close_{tf}"
            bb_key = f"bb_pct_b_{tf}"
            if ts_key not in z.files or close_key not in z.files:
                out[tf] = {"missing": True}
                continue
            ts = np.asarray(z[ts_key], dtype=np.float64)
            close = np.asarray(z[close_key], dtype=np.float64)
            mask = (ts >= t_start) & (ts <= t_end) & (close > 0) & np.isfinite(close)
            if mask.sum() < 10:
                out[tf] = {"n_bars": int(mask.sum()), "insufficient": True}
                continue
            c = close[mask]
            slope, r2 = lr_slope_r2(c)
            slope_pct = (slope / c.mean() * 100) if c.mean() > 0 else 0.0  # %/bar
            rets = np.diff(c) / c[:-1]
            stdev_ret = float(np.std(rets)) if len(rets) > 1 else 0.0
            mean_bbb = 0.0
            if bb_key in z.files:
                bbb = np.asarray(z[bb_key], dtype=np.float64)[mask]
                bbb = bbb[np.isfinite(bbb)]
                mean_bbb = float(np.mean(bbb)) if len(bbb) else 0.0
            out[tf] = {
                "n_bars": int(mask.sum()),
                "lr_slope_pct_per_bar": round(slope_pct, 5),
                "lr_r2": round(r2, 4),
                "mean_bb_pct_b": round(mean_bbb, 4),
                "stdev_returns": round(stdev_ret, 6),
            }
        except Exception as e:
            out[tf] = {"error": str(e)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--symbols", required=True, help="Comma-separated. Or 'all' to use every NPZ in dir.")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=None)
    ap.add_argument("--mode", choices=["tradier", "crypto"], default="tradier")
    args = ap.parse_args()
    npz_dir = Path(args.npz_dir)
    if not npz_dir.is_dir():
        print(f"ERROR: npz-dir not found: {npz_dir}", file=sys.stderr); sys.exit(2)
    if args.symbols == "all":
        syms = [p.stem for p in npz_dir.glob("*.npz")]
    else:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    t_start = parse_iso(args.start)
    t_end = parse_iso(args.end) if args.end else datetime.now(timezone.utc).timestamp()
    tfs = TFS_TRADIER if args.mode == "tradier" else TFS_CRYPTO
    per_sym = {}
    for s in syms:
        p = npz_dir / f"{s}.npz"
        if not p.exists():
            per_sym[s] = {"missing_npz": True}
            continue
        per_sym[s] = regime_metrics_for_npz(p, tfs, t_start, t_end)
    # Aggregate per-TF averages
    agg = {tf: {"lr_slope_pct_per_bar": [], "lr_r2": [], "mean_bb_pct_b": [], "stdev_returns": [], "n_syms": 0}
           for tf in tfs}
    for s, m in per_sym.items():
        if "missing_npz" in m or "error" in m: continue
        for tf in tfs:
            d = m.get(tf, {})
            if d.get("missing") or d.get("insufficient") or d.get("error"): continue
            agg[tf]["lr_slope_pct_per_bar"].append(d["lr_slope_pct_per_bar"])
            agg[tf]["lr_r2"].append(d["lr_r2"])
            agg[tf]["mean_bb_pct_b"].append(d["mean_bb_pct_b"])
            agg[tf]["stdev_returns"].append(d["stdev_returns"])
            agg[tf]["n_syms"] += 1
    summary = {}
    for tf, d in agg.items():
        if d["n_syms"] == 0:
            summary[tf] = {"n_syms": 0, "no_data": True}; continue
        summary[tf] = {
            "n_syms": d["n_syms"],
            "avg_lr_slope_pct_per_bar": round(float(np.mean(d["lr_slope_pct_per_bar"])), 5),
            "avg_lr_r2": round(float(np.mean(d["lr_r2"])), 4),
            "avg_bb_pct_b": round(float(np.mean(d["mean_bb_pct_b"])), 4),
            "avg_stdev_returns": round(float(np.mean(d["stdev_returns"])), 6),
            "median_lr_slope_pct_per_bar": round(float(np.median(d["lr_slope_pct_per_bar"])), 5),
            "median_lr_r2": round(float(np.median(d["lr_r2"])), 4),
        }
    out = {
        "window": {"start": args.start, "end": args.end or "now"},
        "mode": args.mode,
        "n_symbols_total": len(syms),
        "tfs": tfs,
        "per_tf_avg": summary,
    }
    print(json.dumps(out, indent=2))
    print()
    print("=" * 80)
    print(f"  REGIME REPORT — {args.mode} | window {args.start}..{args.end or 'now'} | {len(syms)} symbols")
    print("=" * 80)
    print(f"  {'TF':>4} | {'n_syms':>6} | {'lr_slope%/bar':>14} | {'lr_r2':>6} | {'bb_pct_b':>8} | {'stdev_ret':>9}")
    for tf in tfs:
        s = summary[tf]
        if s.get("no_data"):
            print(f"  {tf:>4} | {'0':>6} | {'(no data)':>14} | {'-':>6} | {'-':>8} | {'-':>9}")
        else:
            print(f"  {tf:>4} | {s['n_syms']:>6} | {s['avg_lr_slope_pct_per_bar']:>14.5f} | {s['avg_lr_r2']:>6.4f} | {s['avg_bb_pct_b']:>8.4f} | {s['avg_stdev_returns']:>9.6f}")
    print()


if __name__ == "__main__":
    main()
