"""run_v4_churn_fix_sweep.py — Test churn-reduction + pyramid improvements.

Root causes from v7_final analysis (91 tradier syms):
  - X1 top-catch kills 57% of trades at median +0.52% (scalping, not trending)
  - X5 structural flip kills 36% at median -0.05% (noise)
  - 0 pyramids on most symbols (2.8h median hold, 4h HH never fires)
  - Path B oversold reversal = 50% of entries = constant churn in ranges
  - Median trade lasts 2.8 hours, max 1.3 days

Variants test different combinations of fixes:
  A) Harder X1 (require 1h confirmation)
  B) Minimum hold period (force 2+ hours before exiting)
  C) Faster pyramid TF (1h instead of 4h) + price-breakout pyramid
  D) Longer cooldown between trades
  E) Entry path filtering (disable G, tighten B)
"""
from __future__ import annotations

import datetime, json, sys, time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_struct_v4_aggressive import AggressiveCfg, simulate_aggressive
from v8_vec_structure_sweep import load_npz

BASE = Path(__file__).resolve().parent
RESULTS_DIR = BASE / "data" / "sweep_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

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

# ═══════════════════════════════════════════════════════════════════════════════
# Base config (v7_final) + improvement variants
# ═══════════════════════════════════════════════════════════════════════════════

def cfg_v7_baseline():
    """v7_final as-is — baseline for comparison."""
    return AggressiveCfg(
        exit_X2_trailing_pct=12.0,
        exit_X3_hardstop_atr_mult=0,
        exit_X7_tech_stop_enabled=True,
        exit_X7_freeze_dc_tf="4h",
        exit_X7_abs_floor_pct=-8.0,
        max_pyramid_levels=5,
        pyramid_add_fraction=0.5,
        pyramid_tf="4h",
        pyramid_also_on_k1h_oversold=True,
        htf_trend_filter_enabled=True,
        path_G_momentum_continuation_enabled=True,
    )

def cfg_v8_hold_2h():
    """v7 + min 2h hold + X5 needs 1h hold."""
    c = cfg_v7_baseline()
    c.min_hold_bars = 24          # 24 × 5min = 2 hours
    c.exit_X5_min_hold_bars = 12  # 1 hour before X5 can fire
    return c

def cfg_v9_hard_x1():
    """v7 + X1 requires K_1h>75 + bear WT cross 1h."""
    c = cfg_v7_baseline()
    c.exit_X1_k15_min = 90.0
    c.exit_X1_require_k1h_min = 75.0
    c.exit_X1_require_wt_bear_1h = True
    return c

def cfg_v10_no_x1():
    """v7 + X1 disabled entirely. Trail + X4 + X5 + X7 only."""
    c = cfg_v7_baseline()
    c.exit_X1_topcatch_enabled = False
    return c

def cfg_v11_hold_x1_pyramid():
    """v7 + 2h hold + hard X1 + 1h pyramid + price breakout pyramid."""
    c = cfg_v7_baseline()
    c.min_hold_bars = 24
    c.exit_X1_k15_min = 92.0
    c.exit_X1_require_k1h_min = 75.0
    c.exit_X1_require_wt_bear_1h = True
    c.exit_X5_min_hold_bars = 24
    c.pyramid_tf = "1h"
    c.pyramid_min_gain_since_last_pct = 1.5
    c.pyramid_on_price_breakout = True
    c.pyramid_price_breakout_min_gain = 2.0
    c.max_pyramid_levels = 8
    return c

def cfg_v12_no_x1_pyramid_cooldown():
    """v7 + X1 off + 1h pyramid + price breakout + cooldown 50 bars."""
    c = cfg_v7_baseline()
    c.exit_X1_topcatch_enabled = False
    c.cooldown_bars_after_exit = 50
    c.pyramid_tf = "1h"
    c.pyramid_min_gain_since_last_pct = 1.5
    c.pyramid_on_price_breakout = True
    c.pyramid_price_breakout_min_gain = 1.5
    c.max_pyramid_levels = 8
    return c

def cfg_v13_full_trend():
    """Maximum trend-following: no X1, 4h hold, 1h pyramid, big cooldown, tighter entries."""
    c = cfg_v7_baseline()
    c.exit_X1_topcatch_enabled = False
    c.min_hold_bars = 48          # 4 hours
    c.exit_X5_min_hold_bars = 48
    c.exit_X5_min_count = 4       # harder structural flip
    c.cooldown_bars_after_exit = 30
    c.pyramid_tf = "1h"
    c.pyramid_min_gain_since_last_pct = 1.0
    c.pyramid_on_price_breakout = True
    c.pyramid_price_breakout_min_gain = 1.5
    c.max_pyramid_levels = 10
    c.pyramid_add_fraction = 0.7
    c.path_G_momentum_continuation_enabled = False  # reduce churn entries
    c.path_B_k1h_max = 30.0  # tighter B: K_1h must be < 30 (not 40)
    return c

def cfg_v14_x1_off_hold_4h_pyr_aggressive():
    """X1 off, 4h hold, aggressive pyramid, X5 at 5 count."""
    c = cfg_v7_baseline()
    c.exit_X1_topcatch_enabled = False
    c.min_hold_bars = 48
    c.exit_X5_min_hold_bars = 48
    c.exit_X5_min_count = 5
    c.cooldown_bars_after_exit = 20
    c.pyramid_tf = "1h"
    c.pyramid_min_gain_since_last_pct = 1.0
    c.pyramid_on_price_breakout = True
    c.pyramid_price_breakout_min_gain = 1.0
    c.max_pyramid_levels = 10
    c.pyramid_add_fraction = 0.8
    return c


def cfg_v15_trail_always():
    """v14 + trail fires at ANY drawdown from peak (not just in profit)."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.exit_X2_require_profit = False  # trail fires even when losing
    return c

def cfg_v16_tight_trail_2h_hold():
    """v14 + 8% trail always + 2h hold (instead of 4h)."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.exit_X2_trailing_pct = 8.0
    c.exit_X2_require_profit = False
    c.min_hold_bars = 24  # 2h instead of 4h
    c.exit_X5_min_hold_bars = 24
    return c

def cfg_v17_8pct_trail():
    """v14 + 8% trail in-profit only (tighter trail, same hold)."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.exit_X2_trailing_pct = 8.0
    return c

def cfg_v18_floor6_trail8():
    """v14 + -6% floor + 8% trail always. Strictest tail-risk control."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.exit_X7_abs_floor_pct = -6.0
    c.exit_X2_trailing_pct = 8.0
    c.exit_X2_require_profit = False
    return c

def cfg_v19_sma50_filter():
    """v14 + SMA50 regime filter. Only enter above SMA50_D."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.require_above_sma50_D = True
    return c

def cfg_v20_x4_long_cooldown():
    """v14 + after X4 exit, cooldown = 200 bars (~16h). Prevents D→X4→D churn."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.x4_exit_extended_cooldown = 200
    return c

def cfg_v21_sma50_x4cd():
    """v14 + SMA50 filter + X4 extended cooldown. Double anti-churn."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.require_above_sma50_D = True
    c.x4_exit_extended_cooldown = 200
    return c

def cfg_v22_ultimate():
    """Best of everything: SMA50 filter + X4 cooldown + 8% trail always + 1h pyramid."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.require_above_sma50_D = True
    c.x4_exit_extended_cooldown = 200
    c.exit_X2_trailing_pct = 8.0
    c.exit_X2_require_profit = False
    c.exit_X5_min_count = 5
    return c

def cfg_v23_hyper_pyramid():
    """v14 but with 20 pyramid levels, 1.0 fraction, 0.5% breakout threshold."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.max_pyramid_levels = 20
    c.pyramid_add_fraction = 1.0
    c.pyramid_price_breakout_min_gain = 0.5
    c.pyramid_min_gain_since_last_pct = 0.5
    return c

def cfg_v24_15m_pyramid():
    """v14 but pyramid on 15m HH instead of 1h — more frequent adds."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.pyramid_tf = "15m"
    c.max_pyramid_levels = 15
    c.pyramid_min_gain_since_last_pct = 0.5
    c.pyramid_price_breakout_min_gain = 0.5
    return c

def cfg_v25_wider_trail_more_pyr():
    """v14 + wider trail (20%) + more pyramids. Let winners run AND compound harder."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.exit_X2_trailing_pct = 20.0
    c.max_pyramid_levels = 15
    c.pyramid_add_fraction = 1.0
    c.pyramid_price_breakout_min_gain = 0.5
    c.pyramid_min_gain_since_last_pct = 0.5
    return c

def cfg_v26_no_x5_no_x4():
    """v14 + X5 disabled + X4 disabled. Only exits: X2 trail + X7 floor. Pure trend ride."""
    c = cfg_v14_x1_off_hold_4h_pyr_aggressive()
    c.exit_X5_structural_flip_enabled = False
    c.exit_X4_daily_bear_wt_enabled = False
    c.exit_X2_trailing_pct = 15.0
    c.max_pyramid_levels = 15
    c.pyramid_add_fraction = 1.0
    c.pyramid_price_breakout_min_gain = 0.5
    c.pyramid_min_gain_since_last_pct = 0.5
    return c


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════

def run_one(symbols, mode, cfg, start_date, label, write_history=False):
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    all_returns = []
    rows = []
    from collections import Counter
    all_entry_paths = Counter()
    all_exit_paths = Counter()
    t0 = time.time()
    for sym in symbols:
        r = simulate_aggressive(sym, mode, cfg, start_ts=start_ts,
                                return_events=write_history)
        if r.get("skip"):
            continue
        all_returns.extend(r["trade_returns"])
        for k, v in r.get("entry_paths", {}).items():
            all_entry_paths[k] += v
        for k, v in r.get("exit_paths", {}).items():
            all_exit_paths[k] += v
        rows.append({
            "sym": r["sym"],
            "trades": r["trades"],
            "wr_pct": r["wr_pct"],
            "avg_gain": r["avg_gain_pct"],
            "bh_mult": r["bh_mult"],
            "compound_mult": r["compound_mult"],
            "ratio_vs_bh": r["ratio_vs_bh"],
        })
        if write_history and r.get("events"):
            hist_dir = BASE / "data" / "history" / label
            hist_dir.mkdir(parents=True, exist_ok=True)
            with open(hist_dir / f"{sym}_LONG.jsonl", "w") as f:
                for ev in r["events"]:
                    f.write(json.dumps(ev) + "\n")
    elapsed = time.time() - t0
    n_syms = len(rows)
    total_trades = sum(r["trades"] for r in rows)
    if len(all_returns) > 1:
        arr = np.array(all_returns)
        pool_sharpe = float(np.mean(arr) / np.std(arr)) if np.std(arr) > 0 else 0.0
    else:
        pool_sharpe = 0.0
    beat_bh = sum(1 for r in rows if r["compound_mult"] > r["bh_mult"])
    cleared_4x = sum(1 for r in rows if r["ratio_vs_bh"] >= 4.0)
    losers = sum(1 for r in rows if r["compound_mult"] < 1.0)
    ratios = [r["ratio_vs_bh"] for r in rows]
    med_ratio = float(np.median(ratios)) if ratios else 0
    mean_wr = float(np.mean([r["wr_pct"] for r in rows])) if rows else 0
    gains = [r["compound_mult"] - 1.0 for r in rows]
    acc_gain = float(np.mean(gains)) * 100 if gains else 0
    years = rows[0].get("years", 2.0) if rows and "years" in rows[0] else 2.0
    worst_trade = min(all_returns) if all_returns else 0
    pyr_count = all_exit_paths.get("MTM", 0)  # proxy
    # Count augments from entry paths
    total_pyramids = sum(v for k, v in all_entry_paths.items() if "PYR" in k.upper())
    s = {
        "label": label, "n_syms": n_syms, "total_trades": total_trades,
        "pool_sharpe": pool_sharpe, "mean_wr": mean_wr,
        "med_ratio_vs_bh": med_ratio,
        "beat_bh": beat_bh, "cleared_4x": cleared_4x,
        "losers": losers, "acc_gain_pct": acc_gain,
        "worst_trade": worst_trade, "elapsed_s": elapsed,
        "entry_paths": dict(all_entry_paths.most_common()),
        "exit_paths": dict(all_exit_paths.most_common()),
    }
    return s, rows


def print_summary(s):
    print(f"\n{'='*80}")
    print(f"  {s['label']} | {s['n_syms']} syms | {s['elapsed_s']:.0f}s")
    print(f"{'='*80}")
    print(f"  pool_sharpe  = {s['pool_sharpe']:+.4f}")
    print(f"  trades       = {s['total_trades']}")
    print(f"  WR%          = {s['mean_wr']:.1f}%")
    print(f"  med xBH      = {s['med_ratio_vs_bh']:.2f}")
    print(f"  beat B&H     = {s['beat_bh']}/{s['n_syms']}")
    print(f"  cleared 4x   = {s['cleared_4x']}/{s['n_syms']}")
    print(f"  losers       = {s['losers']}")
    print(f"  worst trade  = {s['worst_trade']:.2f}%")
    print(f"  acc_gain     = {s['acc_gain_pct']:.1f}%")
    print(f"  entries: {s['entry_paths']}")
    print(f"  exits:   {s['exit_paths']}")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="tradier")
    ap.add_argument("--start", default="2024-04-01")
    ap.add_argument("--write-history", action="store_true")
    ap.add_argument("--variant", default="all",
                    help="all, or specific: v7,v8,v9,v10,v11,v12,v13,v14")
    args = ap.parse_args()

    configs = {
        "v7_baseline": cfg_v7_baseline(),
        "v8_hold_2h": cfg_v8_hold_2h(),
        "v9_hard_x1": cfg_v9_hard_x1(),
        "v10_no_x1": cfg_v10_no_x1(),
        "v11_hold_x1_pyramid": cfg_v11_hold_x1_pyramid(),
        "v12_no_x1_pyr_cooldown": cfg_v12_no_x1_pyramid_cooldown(),
        "v13_full_trend": cfg_v13_full_trend(),
        "v14_x1off_4h_aggr_pyr": cfg_v14_x1_off_hold_4h_pyr_aggressive(),
        "v15_v14_trail_always": cfg_v15_trail_always(),
        "v16_v14_tight_trail_2h": cfg_v16_tight_trail_2h_hold(),
        "v17_v14_8pct_trail": cfg_v17_8pct_trail(),
        "v18_v14_floor6_trail8": cfg_v18_floor6_trail8(),
        "v19_sma50_filter": cfg_v19_sma50_filter(),
        "v20_x4_long_cooldown": cfg_v20_x4_long_cooldown(),
        "v21_sma50_x4cd": cfg_v21_sma50_x4cd(),
        "v22_ultimate": cfg_v22_ultimate(),
        "v23_hyper_pyramid": cfg_v23_hyper_pyramid(),
        "v24_15m_pyramid": cfg_v24_15m_pyramid(),
        "v25_wide_trail_pyr": cfg_v25_wider_trail_more_pyr(),
        "v26_trail_only_exit": cfg_v26_no_x5_no_x4(),
    }

    if args.variant != "all":
        selected = args.variant.split(",")
        configs = {k: v for k, v in configs.items() if any(s in k for s in selected)}

    all_summaries = []
    for label, cfg in configs.items():
        s, rows = run_one(TRADIER_114, args.mode, cfg, args.start, label,
                          write_history=args.write_history)
        print_summary(s)
        all_summaries.append(s)

    # Final comparison
    print(f"\n\n{'='*120}")
    print("  COMPARISON TABLE")
    print(f"{'='*120}")
    hdr = (f"{'Config':<28}{'Sharpe':>8}{'Trades':>7}{'WR%':>6}{'MedxBH':>8}"
           f"{'4x':>5}{'Losers':>7}{'Worst':>7}{'AccGain':>9}")
    print(hdr)
    print("-" * 95)
    for s in all_summaries:
        print(f"{s['label']:<28}{s['pool_sharpe']:>+8.4f}{s['total_trades']:>7}"
              f"{s['mean_wr']:>6.1f}{s['med_ratio_vs_bh']:>8.2f}"
              f"{s['cleared_4x']:>5}{s['losers']:>7}{s['worst_trade']:>7.1f}"
              f"{s['acc_gain_pct']:>+9.1f}%")


if __name__ == "__main__":
    main()
