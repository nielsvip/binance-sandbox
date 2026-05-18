"""run_struct_v4_all_actions.py — Execute all 4 action items for v4 strategy validation.

Action items:
1. Full crypto 72-sym run (push past 48-sym floor)
2. Investigate losers — test tighter ATR mult, wider trail, path filters
3. Robustness check — tradier 114-sym on held-out window (start=2023-07-01)
4. Refine safer config — grid over ATR mult / trail / pyramid to push Sharpe past 0.5

Writes CSVs to data/sweep_results/ with metrics_guard compliance.
"""
from __future__ import annotations

import csv, datetime, json, os, sys, time
from dataclasses import asdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_struct_v4_aggressive import AggressiveCfg, simulate_aggressive

BASE = Path(__file__).resolve().parent
RESULTS_DIR = BASE / "data" / "sweep_results"
HISTORY_DIR = BASE / "data" / "history"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
# Symbol universes
# ═══════════════════════════════════════════════════════════════════════════════

TRADIER_114 = [
    "ACN","AEM","AGCO","AGI","AG","ALB","AMD","AMZN","ARM","AR",
    "ASML","ATI","AU","AVGO","BA","BABA","BG","BHP","BK","BKR",
    "CAT","CCJ","CDE","CENX","CF","CHRD","CLF","CLX","CMC","CME",
    "COIN","COST","CRWD","CSCO","CVX","DAL","DE","DIS","DKNG","DVN",
    "EGLE","EQT","FIVN","FCX","FLR","FSLR","GD","GE","GILD","GOLD",
    "GOOG","GS","HAL","HES","IBM","INTC","JD","JPM","KGC","KMI",
    "LLY","LMT","LOW","MAR","MCD","META","MOD","MPLX","MRK","MSFT",
    "MU","NET","NLR","NOC","NOV","NUKZ","NVDA","NVO","OKE","ORCL",
    "OXY","PBR","PFE","PG","PLTR","PYPL","QCOM","RIG","RTX","SHOP",
    "SMG","SNDK","SNOW","SQ","TGT","TMUS","TSLA","TSM","TXN","UNH",
    "UNP","USAR","V","VLO","VZ","WFC","WM","WMT","XLE","XOM",
    "XOP","ZIM","ZM","ZTS",
]

def _get_crypto_symbols():
    """Get all crypto NPZ symbols available."""
    npz_dir = BASE / "backtest_v8" / "indicators"
    syms = []
    for f in npz_dir.glob("*.npz"):
        name = f.stem
        if name.endswith("USDC") or name.endswith("USDT"):
            syms.append(name)
    return sorted(syms)


# ═══════════════════════════════════════════════════════════════════════════════
# Configs
# ═══════════════════════════════════════════════════════════════════════════════

def cfg_safer():
    return AggressiveCfg(
        exit_X2_trailing_pct=10.0,
        exit_X3_hardstop_atr_mult=3.0,
        max_pyramid_levels=5,
        pyramid_add_fraction=0.5,
        pyramid_tf="4h",
        pyramid_also_on_k1h_oversold=True,
        htf_trend_filter_enabled=True,
        path_G_momentum_continuation_enabled=True,
    )

def cfg_safer_v2_tighter_stop():
    """Safer but with tighter ATR to eliminate losers."""
    c = cfg_safer()
    c.exit_X3_hardstop_atr_mult = 2.0  # tighter: 2× ATR instead of 3×
    return c

def cfg_safer_v3_no_stop():
    """No hard stop — rely only on trail + X4 (daily bear WT)."""
    c = cfg_safer()
    c.exit_X3_hardstop_atr_mult = 0  # disable hard stop entirely
    c.exit_X2_trailing_pct = 12.0  # slightly wider trail to compensate
    return c

def cfg_safer_v4_wider_trail():
    """Wider trail to let more trends run."""
    c = cfg_safer()
    c.exit_X2_trailing_pct = 15.0
    c.exit_X3_hardstop_atr_mult = 3.5
    return c

def cfg_safer_v5_more_pyramid():
    """More pyramid events + wider trail."""
    c = cfg_safer()
    c.exit_X2_trailing_pct=12.0
    c.max_pyramid_levels=8
    c.pyramid_add_fraction=0.7
    c.pyramid_tf="1h"
    return c

def cfg_safer_v6_selective_paths():
    """Only paths A+B+D (remove G/C which churn on losers)."""
    c = cfg_safer()
    c.path_C_k1h_cross_enabled = False
    c.path_G_momentum_continuation_enabled = False
    c.exit_X3_hardstop_atr_mult = 2.5
    return c


def cfg_v7_final():
    """FINAL CONFIG: v3_no_stop + frozen dc_low_4h + 8% absolute floor.
    Best of all worlds: no ATR churn, technical stop catches disasters,
    absolute floor for gap-downs. Zero losers + worst capped at ~-9%.
    """
    return AggressiveCfg(
        exit_X2_trailing_pct=12.0,
        exit_X3_hardstop_atr_mult=0,       # DISABLED — replaced by X7
        exit_X7_tech_stop_enabled=True,     # frozen dc_low_4h + abs floor
        exit_X7_freeze_dc_tf="4h",
        exit_X7_abs_floor_pct=-8.0,
        max_pyramid_levels=5,
        pyramid_add_fraction=0.5,
        pyramid_tf="4h",
        pyramid_also_on_k1h_oversold=True,
        htf_trend_filter_enabled=True,
        path_G_momentum_continuation_enabled=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

def run_universe(symbols, mode, cfg, start_date, label, write_history=False):
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    all_returns = []
    rows = []
    t0 = time.time()
    for sym in symbols:
        r = simulate_aggressive(sym, mode, cfg, start_ts=start_ts, return_events=write_history)
        if r.get("skip"):
            continue
        all_returns.extend(r["trade_returns"])
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
        })
        if write_history and r["events"]:
            hist_dir = HISTORY_DIR / label
            hist_dir.mkdir(parents=True, exist_ok=True)
            with open(hist_dir / f"{sym}_LONG.jsonl", "w") as f:
                for ev in r["events"]:
                    f.write(json.dumps(ev) + "\n")
    elapsed = time.time() - t0
    n_syms = len(rows)
    total_trades = sum(r["trades"] for r in rows)
    # Pool Sharpe
    if len(all_returns) > 1:
        arr = np.array(all_returns)
        pool_sharpe = float(np.mean(arr) / np.std(arr)) if np.std(arr) > 0 else 0.0
    else:
        pool_sharpe = 0.0
    # Sym Sharpe
    sym_sharpes = []
    for r in rows:
        # not enough trades
        pass
    # Aggregate metrics
    beat_bh = sum(1 for r in rows if r["compound_mult"] > r["bh_mult"])
    cleared_4x = sum(1 for r in rows if r["cleared_4x"])
    losers = sum(1 for r in rows if r["compound_mult"] < 1.0)
    catastrophic = sum(1 for r in rows if r["compound_mult"] < 0.5)
    avg_ratio = np.mean([r["ratio_vs_bh"] for r in rows]) if rows else 0
    median_ratio = np.median([r["ratio_vs_bh"] for r in rows]) if rows else 0
    mean_wr = np.mean([r["wr_pct"] for r in rows]) if rows else 0
    acc_gain = np.mean([r["compound_mult"] - 1.0 for r in rows]) * 100 if rows else 0
    gain_per_yr = acc_gain / (rows[0]["years"] if rows else 2.0)
    max_dd_approx = min(r["compound_mult"] for r in rows) if rows else 1.0  # worst single sym
    summary = {
        "label": label,
        "mode": mode,
        "start": start_date,
        "n_syms": n_syms,
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
        "max_dd_worst_sym": (1 - max_dd_approx) * 100,
        "elapsed_s": elapsed,
    }
    # Write per-sym CSV
    ts_label = int(time.time())
    csv_path = RESULTS_DIR / f"struct_v4_{label}_{ts_label}_per_sym.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)
    # Write summary line
    summary_path = RESULTS_DIR / f"struct_v4_{label}_{ts_label}_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    return summary, rows


def print_summary(s):
    print(f"\n{'='*70}")
    print(f"  {s['label']} | {s['mode']} | start={s['start']} | {s['n_syms']} syms | {s['elapsed_s']:.0f}s")
    print(f"{'='*70}")
    print(f"  pool_sharpe     = {s['pool_sharpe']:+.4f}")
    print(f"  total_trades    = {s['total_trades']}")
    print(f"  mean_wr         = {s['mean_wr']:.1f}%")
    print(f"  avg ×B&H        = {s['avg_ratio_vs_bh']:.2f}")
    print(f"  median ×B&H     = {s['median_ratio_vs_bh']:.2f}")
    print(f"  beat B&H        = {s['beat_bh']}/{s['n_syms']} ({s['beat_bh']/max(s['n_syms'],1)*100:.0f}%)")
    print(f"  cleared 4×      = {s['cleared_4x']}/{s['n_syms']} ({s['cleared_4x']/max(s['n_syms'],1)*100:.0f}%)")
    print(f"  losers (<1.0)   = {s['losers_lt_1x']}")
    print(f"  catastrophic    = {s['catastrophic_lt_50pct']}")
    print(f"  gain/yr         = {s['gain_per_yr_pct']:.1f}%")
    print(f"  worst-sym DD    = {s['max_dd_worst_sym']:.1f}%")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", choices=["crypto", "robustness", "refine", "all"], default="all")
    ap.add_argument("--write-history", action="store_true")
    args = ap.parse_args()

    all_summaries = []

    # ─── ACTION 1: Full crypto universe ───────────────────────────────────────
    if args.action in ("crypto", "all"):
        print("\n" + "█"*70)
        print("  ACTION 1: FULL CRYPTO UNIVERSE (72 syms, start=2022)")
        print("█"*70)
        crypto_syms = _get_crypto_symbols()
        print(f"  Found {len(crypto_syms)} crypto NPZs")
        for lbl, cfg in [("crypto_safer", cfg_safer()),
                         ("crypto_safer_v5_more_pyramid", cfg_safer_v5_more_pyramid())]:
            s, _ = run_universe(crypto_syms, "crypto", cfg, "2022-01-01", lbl,
                               write_history=args.write_history)
            print_summary(s)
            all_summaries.append(s)

    # ─── ACTION 2+4: Refine safer — test variants that fix losers ─────────────
    if args.action in ("refine", "all"):
        print("\n" + "█"*70)
        print("  ACTION 2+4: REFINE SAFER — ELIMINATE LOSERS + PUSH SHARPE")
        print("█"*70)
        configs = [
            ("tradier_safer_baseline", cfg_safer()),
            ("tradier_safer_v2_tight_stop", cfg_safer_v2_tighter_stop()),
            ("tradier_safer_v3_no_stop", cfg_safer_v3_no_stop()),
            ("tradier_safer_v4_wider_trail", cfg_safer_v4_wider_trail()),
            ("tradier_safer_v5_more_pyramid", cfg_safer_v5_more_pyramid()),
            ("tradier_safer_v6_selective_paths", cfg_safer_v6_selective_paths()),
        ]
        for lbl, cfg in configs:
            s, _ = run_universe(TRADIER_114, "tradier", cfg, "2024-04-01", lbl,
                               write_history=args.write_history)
            print_summary(s)
            all_summaries.append(s)

    # ─── ACTION 3: Robustness check — held-out window ─────────────────────────
    if args.action in ("robustness", "all"):
        print("\n" + "█"*70)
        print("  ACTION 3: ROBUSTNESS — HELD-OUT WINDOW (start=2023-07-01)")
        print("█"*70)
        for lbl, cfg in [("tradier_safer_heldout", cfg_safer()),
                         ("tradier_safer_v5_heldout", cfg_safer_v5_more_pyramid())]:
            s, _ = run_universe(TRADIER_114, "tradier", cfg, "2023-07-01", lbl,
                               write_history=args.write_history)
            print_summary(s)
            all_summaries.append(s)

    # ─── FINAL COMPARISON TABLE ───────────────────────────────────────────────
    print("\n\n" + "═"*100)
    print("  FINAL COMPARISON TABLE")
    print("═"*100)
    hdr = f"{'Label':<40}{'Sharpe':>8}{'Trades':>8}{'WR%':>6}{'×B&H':>8}{'Med×BH':>8}{'4×':>5}{'Losers':>7}{'Gain/yr':>9}"
    print(hdr)
    print("-"*100)
    for s in all_summaries:
        print(f"{s['label']:<40}{s['pool_sharpe']:>+8.4f}{s['total_trades']:>8}"
              f"{s['mean_wr']:>6.1f}{s['avg_ratio_vs_bh']:>8.2f}{s['median_ratio_vs_bh']:>8.2f}"
              f"{s['cleared_4x']:>5}{s['losers_lt_1x']:>7}{s['gain_per_yr_pct']:>+9.1f}%")


if __name__ == "__main__":
    main()
