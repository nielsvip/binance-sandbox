"""run_crypto_hard_stop_compare.py — 5-variant hard-stop comparison for v3_no_stop on crypto.

Counterpart to the tradier 4h_stop sweep — tests whether a hard-stop floor on top of
v3_no_stop (X3=disabled, relies on trail+X4) is needed for crypto's higher volatility.

Variants (all built on top of cfg_safer_v3_no_stop):
  1. NO_STOP       — pure v3_no_stop, no hard stop
  2. DC_LOW_1H     — frozen dc_low_1h at entry (~20h lookback Donchian low)
  3. DC_LOW_4H     — frozen dc_low_4h at entry (~80h Donchian low, wider)
  4. BB_LOWER_1H   — frozen bb_lower_1h at entry (~2sigma lower band)
  5. ABS_-20PCT    — pure absolute -20% floor (no technical level)

Universe: every crypto NPZ in backtest_v8/indicators/
Window: start=2022-01-01 (bear + bear + bull)
Engine: v8_struct_v4_aggressive.simulate_aggressive (numpy vectorized)

Outputs:
  - data/sweep_results/crypto_hard_stop_<variant>_<ts>_per_sym.csv (per-sym rows)
  - data/sweep_results/crypto_hard_stop_<variant>_<ts>_summary.json (aggregate)
  - /tmp/crypto_hard_stop_compare.md (final markdown report)
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from run_struct_v4_all_actions import cfg_safer_v3_no_stop, _get_crypto_symbols
from v8_struct_v4_aggressive import AggressiveCfg, simulate_aggressive

RESULTS_DIR = BASE / "data" / "sweep_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _build_variants() -> List[Tuple[str, AggressiveCfg]]:
    """Build the 5 hard-stop variants on top of cfg_safer_v3_no_stop."""
    variants = []
    # 1. NO_STOP — pure baseline
    c1 = cfg_safer_v3_no_stop()
    c1.exit_X7_tech_stop_enabled = False
    variants.append(("NO_STOP", c1))
    # 2. DC_LOW_1H — frozen dc_low_1h at entry, no abs floor (only DC triggers)
    c2 = cfg_safer_v3_no_stop()
    c2.exit_X7_tech_stop_enabled = True
    c2.exit_X7_freeze_dc_tf = "1h"
    c2.exit_X7_freeze_bb_tf = ""
    c2.exit_X7_abs_floor_pct = -999.0  # disable abs floor for clean isolation
    variants.append(("DC_LOW_1H", c2))
    # 3. DC_LOW_4H — frozen dc_low_4h at entry
    c3 = cfg_safer_v3_no_stop()
    c3.exit_X7_tech_stop_enabled = True
    c3.exit_X7_freeze_dc_tf = "4h"
    c3.exit_X7_freeze_bb_tf = ""
    c3.exit_X7_abs_floor_pct = -999.0
    variants.append(("DC_LOW_4H", c3))
    # 4. BB_LOWER_1H — frozen bb_lower_1h at entry
    c4 = cfg_safer_v3_no_stop()
    c4.exit_X7_tech_stop_enabled = True
    c4.exit_X7_freeze_dc_tf = "__none__"  # nonexistent → DC disabled
    c4.exit_X7_freeze_bb_tf = "1h"
    c4.exit_X7_abs_floor_pct = -999.0
    variants.append(("BB_LOWER_1H", c4))
    # 5. ABS_-20PCT — pure -20% floor
    c5 = cfg_safer_v3_no_stop()
    c5.exit_X7_tech_stop_enabled = True
    c5.exit_X7_freeze_dc_tf = "__none__"  # nonexistent → DC disabled
    c5.exit_X7_freeze_bb_tf = ""
    c5.exit_X7_abs_floor_pct = -20.0
    variants.append(("ABS_-20PCT", c5))
    return variants


def _run_one_variant(symbols: List[str], mode: str, cfg: AggressiveCfg,
                     start_date: str, label: str) -> Dict:
    """Run one variant across all symbols. Return summary dict + per-sym rows."""
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    all_returns: List[float] = []
    all_worst_dd: List[float] = []
    rows: List[Dict] = []
    t0 = time.time()
    for sym in symbols:
        try:
            r = simulate_aggressive(sym, mode, cfg, start_ts=start_ts)
        except Exception as e:
            sys.stderr.write(f"  SKIP {sym}: {type(e).__name__}: {e}\n")
            continue
        if r.get("skip"):
            continue
        all_returns.extend(r["trade_returns"])
        all_worst_dd.extend(r.get("trade_worst_dd", []))
        # per-sym worst DD (most negative)
        sym_worst_dd = min(r.get("trade_worst_dd", [0.0])) if r.get("trade_worst_dd") else 0.0
        rows.append({
            "sym": r["sym"],
            "years": r["years"],
            "trades": r["trades"],
            "wr_pct": r["wr_pct"],
            "avg_gain_trade": r["avg_gain_pct"],
            "bh_mult": r["bh_mult"],
            "compound_mult": r["compound_mult"],
            "ratio_vs_bh": r["ratio_vs_bh"],
            "cleared_4x": 1 if r["ratio_vs_bh"] >= 4.0 else 0,
            "worst_trade_dd_pct": sym_worst_dd,
            "exit_paths": json.dumps(r.get("exit_paths", {})),
        })
    elapsed = time.time() - t0
    n_syms = len(rows)
    total_trades = sum(r["trades"] for r in rows)
    # Pool Sharpe
    if len(all_returns) > 1 and np.std(all_returns) > 0:
        pool_sharpe = float(np.mean(all_returns) / np.std(all_returns))
    else:
        pool_sharpe = 0.0
    # Aggregates
    beat_bh = sum(1 for r in rows if r["compound_mult"] > r["bh_mult"])
    cleared_4x = sum(1 for r in rows if r["cleared_4x"])
    losers = sum(1 for r in rows if r["compound_mult"] < 1.0)
    catastrophic = sum(1 for r in rows if r["compound_mult"] < 0.5)
    avg_ratio = float(np.mean([r["ratio_vs_bh"] for r in rows])) if rows else 0.0
    median_ratio = float(np.median([r["ratio_vs_bh"] for r in rows])) if rows else 0.0
    mean_wr = float(np.mean([r["wr_pct"] for r in rows])) if rows else 0.0
    n_years = rows[0]["years"] if rows else 4.0
    acc_gain_pct_mean = float(np.mean([(r["compound_mult"] - 1.0) * 100 for r in rows])) if rows else 0.0
    gain_per_yr = acc_gain_pct_mean / n_years if n_years > 0 else 0.0
    # Worst-sym DD = worst compound_mult drop (single sym total)
    worst_sym_compound_dd = (1 - min(r["compound_mult"] for r in rows)) * 100 if rows else 0.0
    # Trade-level worst-DD distribution
    wdd = np.array(all_worst_dd) if all_worst_dd else np.array([0.0])
    summary = {
        "label": label,
        "mode": mode,
        "start": start_date,
        "n_syms": n_syms,
        "n_years": n_years,
        "total_trades": total_trades,
        "pool_sharpe": pool_sharpe,
        "mean_wr": mean_wr,
        "avg_ratio_vs_bh": avg_ratio,
        "median_ratio_vs_bh": median_ratio,
        "beat_bh": beat_bh,
        "cleared_4x": cleared_4x,
        "losers_lt_1x": losers,
        "catastrophic_lt_50pct": catastrophic,
        "gain_per_yr_pct": gain_per_yr,
        "worst_sym_compound_dd_pct": worst_sym_compound_dd,
        # Worst intra-trade DD distribution (per-trade, across pool)
        "trade_worst_dd_min_pct": float(np.min(wdd)),
        "trade_worst_dd_p01_pct": float(np.percentile(wdd, 1)),
        "trade_worst_dd_p05_pct": float(np.percentile(wdd, 5)),
        "trade_worst_dd_p25_pct": float(np.percentile(wdd, 25)),
        "trade_worst_dd_median_pct": float(np.percentile(wdd, 50)),
        "trade_worst_dd_mean_pct": float(np.mean(wdd)),
        "n_trades_with_dd": int(len(wdd)),
        "elapsed_s": elapsed,
    }
    return summary, rows


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--mode", default="crypto")
    ap.add_argument("--symbols", default="", help="comma-separated; empty=all crypto NPZs")
    ap.add_argument("--variants", default="all", help="comma-separated subset")
    ap.add_argument("--report", default="/tmp/crypto_hard_stop_compare.md")
    args = ap.parse_args()

    if args.symbols:
        symbols = args.symbols.split(",")
    else:
        symbols = _get_crypto_symbols()
    print(f"[run] mode={args.mode} start={args.start} n_syms={len(symbols)}")

    variants = _build_variants()
    if args.variants != "all":
        wanted = set(args.variants.split(","))
        variants = [(n, c) for (n, c) in variants if n in wanted]
    print(f"[run] variants={[n for n,_ in variants]}")

    ts_label = int(time.time())
    all_summaries = []
    for label, cfg in variants:
        print(f"\n[{label}] starting…")
        sys.stdout.flush()
        summary, rows = _run_one_variant(symbols, args.mode, cfg, args.start, label)
        all_summaries.append(summary)
        # Write per-sym CSV
        csv_path = RESULTS_DIR / f"crypto_hard_stop_{label}_{ts_label}_per_sym.csv"
        if rows:
            with open(csv_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
        # Write summary JSON
        json_path = RESULTS_DIR / f"crypto_hard_stop_{label}_{ts_label}_summary.json"
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2)
        # Live print
        print(f"  [{label}] done in {summary['elapsed_s']:.0f}s")
        print(f"    pool_sharpe={summary['pool_sharpe']:+.4f}  trades={summary['total_trades']}  WR={summary['mean_wr']:.1f}%")
        print(f"    cleared_4x={summary['cleared_4x']}/{summary['n_syms']}  losers={summary['losers_lt_1x']}  cata={summary['catastrophic_lt_50pct']}")
        print(f"    gain/yr={summary['gain_per_yr_pct']:.1f}%  worst-sym-compound-dd={summary['worst_sym_compound_dd_pct']:.1f}%")
        print(f"    trade_worst_dd min={summary['trade_worst_dd_min_pct']:.1f}% p01={summary['trade_worst_dd_p01_pct']:.1f}% median={summary['trade_worst_dd_median_pct']:.1f}%")
        sys.stdout.flush()

    # Write markdown report
    _write_markdown_report(all_summaries, args.report, args.start, len(symbols))
    print(f"\n[done] report -> {args.report}")


def _write_markdown_report(summaries: List[Dict], path: str, start: str, n_syms_input: int):
    lines = []
    lines.append("# Crypto Hard-Stop Variant Comparison — v3_no_stop")
    lines.append("")
    lines.append(f"- Engine: `v8_struct_v4_aggressive.simulate_aggressive` (NPZ-vectorized)")
    lines.append(f"- Universe: all crypto NPZs in `backtest_v8/indicators/` ({n_syms_input} syms requested)")
    lines.append(f"- Window: start={start} → present")
    lines.append(f"- Base config: `cfg_safer_v3_no_stop` (X3 ATR hard-stop OFF, trail=12%, max_pyramid=5, htf_filter ON)")
    lines.append(f"- Worst intra-trade DD: (`lowest_close_in_trade - entry_price`) / entry_price, per trade")
    lines.append(f"- All Sharpe figures are **pool_sharpe** = mean(trade_returns)/std(trade_returns) per CLAUDE.md")
    lines.append("")
    # Sample-floor compliance note
    n_syms_actual = summaries[0]["n_syms"] if summaries else 0
    n_years = summaries[0]["n_years"] if summaries else 0.0
    if n_syms_actual < 48:
        lines.append(f"> **[DIAGNOSTIC ONLY · n_syms={n_syms_actual} · years={n_years:.1f}]** — Below 48-crypto sample floor; cannot promote/deploy.")
        lines.append("")

    # ── Section 1: per-variant aggregate ──
    lines.append("## 1. Per-variant aggregate")
    lines.append("")
    lines.append("| Variant | pool_sharpe | trades | WR% | cleared_4xB&H | losers <1x | cata <0.5x | worst-sym-cmpd-DD% | gain/yr% |")
    lines.append("|---------|-------------|--------|-----|---------------|------------|------------|--------------------|----------|")
    for s in summaries:
        lines.append(
            f"| {s['label']} | {s['pool_sharpe']:+.4f} | {s['total_trades']} | "
            f"{s['mean_wr']:.1f} | {s['cleared_4x']}/{s['n_syms']} | "
            f"{s['losers_lt_1x']} | {s['catastrophic_lt_50pct']} | "
            f"{s['worst_sym_compound_dd_pct']:.1f} | {s['gain_per_yr_pct']:+.1f} |"
        )
    lines.append("")

    # ── Section 2: worst-trade DD distribution ──
    lines.append("## 2. Worst intra-trade DD distribution (per trade, across pool)")
    lines.append("")
    lines.append("Trade-level worst DD = lowest close during trade vs entry price.")
    lines.append("")
    lines.append("| Variant | n_trades | min | p01 | p05 | p25 | median | mean |")
    lines.append("|---------|----------|-----|-----|-----|-----|--------|------|")
    for s in summaries:
        lines.append(
            f"| {s['label']} | {s['n_trades_with_dd']} | "
            f"{s['trade_worst_dd_min_pct']:.1f}% | "
            f"{s['trade_worst_dd_p01_pct']:.1f}% | "
            f"{s['trade_worst_dd_p05_pct']:.1f}% | "
            f"{s['trade_worst_dd_p25_pct']:.1f}% | "
            f"{s['trade_worst_dd_median_pct']:.1f}% | "
            f"{s['trade_worst_dd_mean_pct']:.1f}% |"
        )
    lines.append("")

    # ── Section 3: verdict ──
    lines.append("## 3. Verdict")
    lines.append("")
    if not summaries:
        lines.append("(no variants ran)")
    else:
        # Sort by gain/yr descending, then by pool_sharpe descending
        ranked = sorted(summaries, key=lambda s: (s["gain_per_yr_pct"], s["pool_sharpe"]), reverse=True)
        best_alpha = ranked[0]
        # Find safest (smallest |trade_worst_dd_min_pct|)
        ranked_safety = sorted(summaries, key=lambda s: s["trade_worst_dd_min_pct"], reverse=True)
        safest = ranked_safety[0]
        no_stop = next((s for s in summaries if s["label"] == "NO_STOP"), None)

        lines.append(f"- **Highest alpha (gain/yr)**: `{best_alpha['label']}` at {best_alpha['gain_per_yr_pct']:+.1f}%/yr, "
                     f"pool_sharpe {best_alpha['pool_sharpe']:+.4f}, worst-trade-DD min {best_alpha['trade_worst_dd_min_pct']:.1f}%.")
        lines.append(f"- **Safest (best worst-trade-DD floor)**: `{safest['label']}` worst-trade-DD min {safest['trade_worst_dd_min_pct']:.1f}%, "
                     f"gain/yr {safest['gain_per_yr_pct']:+.1f}%.")
        if no_stop is not None:
            lines.append(f"- **NO_STOP baseline**: gain/yr {no_stop['gain_per_yr_pct']:+.1f}%, "
                         f"pool_sharpe {no_stop['pool_sharpe']:+.4f}, "
                         f"worst-trade-DD min {no_stop['trade_worst_dd_min_pct']:.1f}%, "
                         f"losers {no_stop['losers_lt_1x']}, cata {no_stop['catastrophic_lt_50pct']}.")
        lines.append("")
        lines.append("**Was the goal achieved?** A hard stop is worthwhile if it materially reduces the worst-trade-DD min "
                     "(and worst-sym-compound-DD) without crushing gain/yr or pool_sharpe vs NO_STOP. Compare each variant's "
                     "(worst_trade_dd_min, gain/yr) trade-off above. For crypto live deployment, prefer the variant that "
                     "(a) keeps gain/yr within ~10–15% of NO_STOP and (b) tightens worst-trade-DD min by ≥5 pct-points.")
        lines.append("")
        if n_syms_actual < 48:
            lines.append("> **NOTE**: Sub-floor sample. This is DIAGNOSTIC ONLY. Cannot recommend any variant for live deployment "
                         "until run on ≥48 crypto syms × >1yr × ≥30 trades/sym.")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
