#!/usr/bin/env python3
"""
rapid_grid_sweep.py — targeted grid sweep for trade-count + win-rate tests.

Each config is a single vectorized simulate() call (all symbols at once).
Runs sequentially in one process; each sim takes 30-120s → full grid ~1-2hr.

Usage:
  python3 rapid_grid_sweep.py --mode crypto  --npz-dir /home/niels/binance-sandbox/backtest_v8/indicators --symbols 50 --start 2022-01-01 --base-json /home/niels/binance-sandbox/data/baselines/crypto_2p6365_genuine.json
  python3 rapid_grid_sweep.py --mode tradier --npz-dir /home/niels/binance-sandbox/backtest_v8/indicators --symbols 114 --start 2022-01-01 --base-json /home/niels/binance-sandbox/data/baselines/tradier_2p4860_genuine.json
"""
import argparse, json, os, sys, time
from datetime import datetime
from pathlib import Path

# ── grid definitions ────────────────────────────────────────────────────────
# Each entry: (label, {override_key: value, ...})
# All overrides applied on top of the base JSON. Empty dict = baseline.

CRYPTO_GRID = [
    # ── BASELINE ──────────────────────────────────────────────────────────
    ("BASELINE",                   {}),

    # ── TEST 1: PPL threshold (trade doubler on winners) ──────────────────
    # Baseline evolved to ~1.125%; testing faster exit (0.3-0.5) for rapid trading
    ("PPL_0p3",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 0.3, "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 0.5,  "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT": 0.02}),
    ("PPL_0p4",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 0.4, "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 0.6,  "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT": 0.02}),
    ("PPL_0p5",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 0.5, "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 0.75, "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT": 0.02}),
    ("PPL_0p8",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 0.8, "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 1.0,  "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT": 0.02}),
    ("PPL_disabled",               {"PARTIAL_PROFIT_LOCK_ENABLED": False}),

    # ── TEST 2: STALL_SUB — not vectorized in engine; tested via WRONG_SIDE_ABS_KILL aggression ──
    ("WS_KILL_on",                 {"WRONG_SIDE_ABS_KILL_ENABLED": True}),
    ("WS_KILL_off",                {"WRONG_SIDE_ABS_KILL_ENABLED": False}),

    # ── TEST 3: Reentry K gate (shallow pullback re-entries on momentum) ──
    # Baseline K=30 (deep pullback only); loosening catches momentum continuation
    ("REENTRY_K40",                {"REENTRY_RALLY_K15M_MAX": 40.0}),
    ("REENTRY_K50",                {"REENTRY_RALLY_K15M_MAX": 50.0}),
    ("REENTRY_K50_GAP3",           {"REENTRY_RALLY_K15M_MAX": 50.0, "REENTRY_MIN_GAP_BARS": 3}),
    ("REENTRY_K60_GAP3",           {"REENTRY_RALLY_K15M_MAX": 60.0, "REENTRY_MIN_GAP_BARS": 3}),

    # ── TEST 4: Augment as trade multiplier (add to already-winning positions) ─
    ("AUG_4H_bounce",              {"AUGMENT_WT_4H_BOUNCE_ENABLED": True, "AUGMENT_WT_4H_MULTIPLIER": 2.0}),
    ("AUG_4H_bounce_PT",           {"AUGMENT_WT_4H_BOUNCE_ENABLED": True, "AUGMENT_WT_4H_MULTIPLIER": 2.0, "AUGMENT_PT_ENABLED": True, "AUGMENT_PT_PCT": 0.4}),
    ("AUG_D_and_4H",               {"AUGMENT_WT_D_BOUNCE_ENABLED": True, "AUGMENT_WT_4H_BOUNCE_ENABLED": True}),

    # ── TEST 5: NOLOSS_BYPASS_WT_5OF5 (exit confirmed losers → free capital) ─
    ("NOLOSS_BYP_5TF",             {"NOLOSS_BYPASS_WT_5OF5_ENABLED": True, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": 5}),
    ("NOLOSS_BYP_4TF",             {"NOLOSS_BYPASS_WT_5OF5_ENABLED": True, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": 4}),

    # ── TEST 6: WT exit tighter + faster reentry ──────────────────────────
    # WT_EXIT_MIN_TFS=2 (was 3): exits fire sooner; paired with WT15m reentry
    ("WT_EXIT_2",                  {"WT_EXIT_MIN_TFS": 2}),
    ("WT_EXIT_2_no_htf",           {"WT_EXIT_MIN_TFS": 2, "REENTRY_WT15M_HTF_FAVOR_REQUIRED": False}),

    # ── COMBOS: best from each test stacked ───────────────────────────────
    ("COMBO_noloss5_wskill",       {"NOLOSS_BYPASS_WT_5OF5_ENABLED": True, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": 5, "WRONG_SIDE_ABS_KILL_ENABLED": True}),
    ("COMBO_k50_wtexit2",          {"REENTRY_RALLY_K15M_MAX": 50.0, "WT_EXIT_MIN_TFS": 2}),
]

TRADIER_GRID = [
    # ── BASELINE ──────────────────────────────────────────────────────────
    ("BASELINE",                   {}),

    # ── TEST 1: PPL threshold ─────────────────────────────────────────────
    # NOTE: engine reads PARTIAL_PROFIT_LOCK_GAIN_PCT (not _TRADIER). Baseline has 0.9375%.
    # Previous run used _TRADIER key → results identical to baseline (ignored). Fixed here.
    ("PPL_0p5",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 0.5,  "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 0.75}),
    ("PPL_0p9375_baseline",        {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 0.9375, "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 0.46875}),
    ("PPL_1p5",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 1.5,  "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 2.0}),
    ("PPL_2p0",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 2.0,  "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 2.75}),
    ("PPL_2p5",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 2.5,  "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 3.25}),
    ("PPL_3p0",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 3.0,  "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 4.0}),
    ("PPL_4p0",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 4.0,  "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 5.5}),
    ("PPL_5p0",                    {"PARTIAL_PROFIT_LOCK_ENABLED": True, "PARTIAL_PROFIT_LOCK_GAIN_PCT": 5.0,  "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 7.0}),
    ("PPL_disabled",               {"PARTIAL_PROFIT_LOCK_ENABLED": False}),

    # ── TEST 2: WRONG_SIDE_ABS_KILL (capital recycler via removing confirmed losers) ──
    ("WS_KILL_on",                 {"WRONG_SIDE_ABS_KILL_ENABLED": True}),
    ("WS_KILL_off",                {"WRONG_SIDE_ABS_KILL_ENABLED": False}),

    # ── TEST 3: Reentry K gate ────────────────────────────────────────────
    ("REENTRY_K40",                {"REENTRY_RALLY_K15M_MAX": 40.0}),
    ("REENTRY_K50",                {"REENTRY_RALLY_K15M_MAX": 50.0}),
    ("REENTRY_K50_GAP2",           {"REENTRY_RALLY_K15M_MAX": 50.0, "REENTRY_MIN_GAP_BARS": 2}),
    ("REENTRY_K60_GAP2",           {"REENTRY_RALLY_K15M_MAX": 60.0, "REENTRY_MIN_GAP_BARS": 2}),

    # ── TEST 4: Augment gate (tradier) ────────────────────────────────────
    # Baseline already has AUGMENT_WT_D_BOUNCE=True; test 4H addition
    ("AUG_4H_bounce",              {"AUGMENT_WT_4H_BOUNCE_ENABLED": True}),
    ("AUG_4H_bounce_PT",           {"AUGMENT_WT_4H_BOUNCE_ENABLED": True, "AUGMENT_PT_ENABLED": True, "AUGMENT_PT_PCT": 0.5}),
    ("AUG_only_profitable",        {"AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER": True}),
    ("AUG_only_prof_4H",           {"AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER": True, "AUGMENT_WT_4H_BOUNCE_ENABLED": True}),

    # ── TEST 5: NOLOSS_BYPASS_WT_5OF5 ────────────────────────────────────
    ("NOLOSS_BYP_5TF",             {"NOLOSS_BYPASS_WT_5OF5_ENABLED": True, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": 5}),
    ("NOLOSS_BYP_4TF",             {"NOLOSS_BYPASS_WT_5OF5_ENABLED": True, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": 4}),

    # ── TEST 6: WT exit tighter + faster reentry ──────────────────────────
    ("WT_EXIT_2",                  {"WT_EXIT_MIN_TFS": 2}),
    ("WT_EXIT_2_no_htf",           {"WT_EXIT_MIN_TFS": 2, "REENTRY_WT15M_HTF_FAVOR_REQUIRED": False}),

    # ── COMBOS ────────────────────────────────────────────────────────────
    ("COMBO_noloss5_wskill",       {"NOLOSS_BYPASS_WT_5OF5_ENABLED": True, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": 5, "WRONG_SIDE_ABS_KILL_ENABLED": False}),
    ("COMBO_k50_wtexit2",          {"REENTRY_RALLY_K15M_MAX": 50.0, "WT_EXIT_MIN_TFS": 2}),
]

GRIDS = {"crypto": CRYPTO_GRID, "tradier": TRADIER_GRID}

# ── helpers ─────────────────────────────────────────────────────────────────

def apply_overrides(base_cfg, overrides):
    """Apply override dict to a QuickConfig, return cfg."""
    import copy
    cfg = copy.deepcopy(base_cfg)
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
        else:
            print(f"  [WARN] override key '{k}' not found in QuickConfig — skipping")
    return cfg

def fmt_result(r):
    """Extract the 5 required metrics from simulate() result dict."""
    pool_sharpe   = r.get("pool_sharpe", 0.0)
    sym_sharpe    = r.get("sym_sharpe",  r.get("sharpe", 0.0))
    acc_gain      = r.get("accumulated_gain_pct", 0.0)
    trades        = int(r.get("trades", r.get("total_trades", 0)))
    max_dd        = r.get("max_dd_pct",  r.get("max_drawdown_pct", 0.0))
    win_rate      = r.get("win_rate",    r.get("wr", 0.0))
    return pool_sharpe, sym_sharpe, acc_gain, trades, max_dd, win_rate

def compute_derived(acc_gain, trades, n_syms, n_years):
    avg_gain_trade = acc_gain / trades if trades > 0 else 0.0
    gain_per_yr    = acc_gain / n_years if n_years > 0 else 0.0
    gain_sym_yr    = acc_gain / n_syms / n_years if n_syms > 0 and n_years > 0 else 0.0
    return avg_gain_trade, gain_per_yr, gain_sym_yr

# ── main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode",     required=True, choices=["crypto", "tradier"])
    ap.add_argument("--npz-dir",  required=True)
    ap.add_argument("--symbols",  type=int, default=50)
    ap.add_argument("--start",    default="2022-01-01")
    ap.add_argument("--base-json",required=True, help="Path to baseline overrides JSON")
    ap.add_argument("--out-csv",  default=None,  help="Output CSV path (default: auto)")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from v8_quick_engine import QuickConfig, load_npz, simulate

    n_years = max((datetime.utcnow() - datetime.strptime(args.start, "%Y-%m-%d")).days / 365.25, 0.01)
    n_syms  = args.symbols
    grid    = GRIDS[args.mode]

    out_csv = args.out_csv or f"data/sweep_results/rapid_grid_{args.mode}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)

    print(f"\n[RAPID_GRID] mode={args.mode}  syms={n_syms}  start={args.start}  n_years={n_years:.1f}  configs={len(grid)}", flush=True)
    print(f"[RAPID_GRID] Loading NPZ from {args.npz_dir} ...", flush=True)
    t_load = time.time()

    CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    all_files   = sorted(Path(args.npz_dir).glob("*.npz"))
    candidates  = []
    for p in all_files:
        sym = p.stem
        is_crypto = any(sym.endswith(s) for s in CRYPTO_SUFFIXES)
        if args.mode == "crypto"  and not is_crypto: continue
        if args.mode == "tradier" and     is_crypto: continue
        candidates.append(sym)
    syms   = candidates[:n_syms]
    stores = load_npz(args.mode, syms, args.start, args.npz_dir)
    print(f"[RAPID_GRID] Loaded {len(stores)} symbols in {time.time()-t_load:.1f}s", flush=True)

    # Build base QuickConfig from JSON
    base_cfg = QuickConfig()
    base_cfg.MODE = args.mode
    base_cfg.LTF  = "3m" if args.mode == "crypto" else "5m"
    with open(args.base_json) as f:
        base_overrides = json.load(f)
    base_overrides.pop("_meta", None)
    for k, v in base_overrides.items():
        if hasattr(base_cfg, k):
            setattr(base_cfg, k, v)
    print(f"[RAPID_GRID] Base JSON applied: {len(base_overrides)} keys from {args.base_json}", flush=True)

    header = ("label", "pool_sharpe", "sym_sharpe", "acc_gain_pct", "avg_gain_trade", "gain_per_yr", "gain_sym_yr", "trades", "max_dd_pct", "win_rate_pct", "elapsed_s")
    rows   = []

    print(f"\n{'Label':<28} {'Pool_S':>7} {'Sym_S':>7} {'Acc%':>8} {'AvgTr%':>7} {'Gain/Yr':>8} {'G/Sym/Yr':>9} {'Trades':>7} {'DD%':>7} {'WR%':>6}  Sec", flush=True)
    print("-" * 110, flush=True)

    for label, overrides in grid:
        cfg  = apply_overrides(base_cfg, overrides)
        t0   = time.time()
        try:
            result = simulate(stores, cfg)
        except Exception as e:
            print(f"  {label:<26} ERROR: {e}", flush=True)
            continue
        elapsed = time.time() - t0

        pool_sharpe, sym_sharpe, acc_gain, trades, max_dd, win_rate = fmt_result(result)
        avg_gain_trade, gain_per_yr, gain_sym_yr = compute_derived(acc_gain, trades, n_syms, n_years)

        rows.append((label, pool_sharpe, sym_sharpe, acc_gain, avg_gain_trade, gain_per_yr, gain_sym_yr, trades, max_dd, win_rate * 100, elapsed))
        print(f"  {label:<26} {pool_sharpe:>7.4f} {sym_sharpe:>7.4f} {acc_gain:>8.1f} {avg_gain_trade:>7.4f} {gain_per_yr:>8.1f} {gain_sym_yr:>9.4f} {trades:>7d} {max_dd:>7.2f} {win_rate*100:>6.1f}  {elapsed:>5.0f}s", flush=True)

    # Write CSV
    import csv
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"\n[RAPID_GRID] Results → {out_csv}", flush=True)

    # Summary: rank by pool_sharpe, highlight vs baseline
    if rows:
        baseline = next((r for r in rows if r[0] == "BASELINE"), None)
        ranked   = sorted(rows, key=lambda r: r[1], reverse=True)
        print(f"\n{'─'*60}", flush=True)
        baseline_s = f"{baseline[1]:.4f}" if baseline else "n/a"
        print(f"TOP 5 by pool_sharpe (baseline pool_S={baseline_s}):", flush=True)
        for r in ranked[:5]:
            delta = f"(Δ{r[1]-baseline[1]:+.4f} sharpe, Δ{r[7]-baseline[7]:+d} trades)" if baseline else ""
            print(f"  {r[0]:<28} pool_sharpe={r[1]:.4f}  trades={r[7]}  wr={r[9]:.1f}%  {delta}", flush=True)

if __name__ == "__main__":
    main()
